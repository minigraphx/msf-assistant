"""Durable public-client OAuth; all public operations except auth_routes are async.

Operations offload complete SQLite transactions to AnyIO’s bounded worker pool.
Construction and auth_routes are synchronous startup operations.
Browser handlers MUST verify their own session/CSRF before complete_login/approve;
only complete_login accepts the identity obtained from verified MSF authentication.
Request IDs and credentials are random bearer secrets, stored only as SHA-256 hashes.
Refresh families have a fixed 30-day lifetime, including consumed-token tombstones.
Service maintenance locking must wrap mutations before entering these transactions;
no player lock is acquired or awaited while a database transaction is held.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import anyio
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.provider import (
    AuthorizeError as SDKAuthorizeError,
)
from mcp.server.auth.provider import (
    RegistrationError as SDKRegistrationError,
)
from mcp.server.auth.provider import (
    TokenError as SDKTokenError,
)
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from msf_assistant.hosted_store import HostedStore


# SDK error dataclasses are frozen; subclasses allow contextlib to attach tracebacks.
class AuthorizeError(SDKAuthorizeError):
    pass


class RegistrationError(SDKRegistrationError):
    pass


class TokenError(SDKTokenError):
    pass


SCOPES = ["msf:read", "msf:write", "offline_access"]


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _offload(operation):
    """Keep SQLite lock waits off the event loop; AnyIO bounds worker concurrency."""

    @wraps(operation)
    async def call(*args, **kwargs):
        from functools import partial

        return await anyio.to_thread.run_sync(partial(operation, *args, **kwargs))

    return call


class _SDKFormCompatibility:
    """Narrow SDK 2.2 resource/revocation fixes; delegate authentication and protocol."""

    def __init__(self, app, resource=None):
        self.app = app
        self.resource = resource

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        body = await request.body()
        form = await request.form()
        error = None
        if self.resource is not None and form.getlist("resource") != [self.resource]:
            error = "invalid_target"
        if self.resource is None:
            if any(len(form.getlist(key)) != 1 for key in form):
                error = "invalid_request"
            elif "client_secret" not in form:
                body = urlencode([*form.multi_items(), ("client_secret", "")]).encode()
                scope = dict(scope)
                scope["headers"] = [
                    (key, value)
                    for key, value in scope["headers"]
                    if key.lower() not in (b"content-type", b"content-length")
                ] + [
                    (b"content-type", b"application/x-www-form-urlencoded"),
                    (b"content-length", str(len(body)).encode()),
                ]
        if error is not None:
            response = JSONResponse(
                {"error": error},
                status_code=400,
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "Access-Control-Allow-Origin": "*",
                },
            )
            return await response(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class HostedOAuthProvider:
    CLIENT_TTL = 86400
    REQUEST_TTL = 600
    CODE_TTL = 60
    ACCESS_TTL = 900
    FAMILY_TTL = 30 * 86400
    MAX_CLIENTS = 1000
    MAX_REQUESTS = 1000

    def __init__(self, store: HostedStore, public_url: str):
        self.store = store
        self.public_url = public_url.rstrip("/")
        parsed = urlsplit(self.public_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("A public HTTPS base URL is required")
        self.resource = self.public_url + "/mcp"
        with store.transaction() as db:
            # execute individually: executescript would implicitly commit the transaction.
            for statement in (
                "CREATE TABLE IF NOT EXISTS oauth_clients ("
                "id TEXT PRIMARY KEY, metadata TEXT NOT NULL, expires REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oauth_requests ("
                "hash TEXT PRIMARY KEY, client TEXT NOT NULL REFERENCES oauth_clients(id), "
                "player TEXT REFERENCES players(id), encrypted BLOB NOT NULL, "
                "expires REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oauth_grants ("
                "id TEXT PRIMARY KEY, client TEXT NOT NULL REFERENCES oauth_clients(id), "
                "player TEXT NOT NULL REFERENCES players(id), resource TEXT NOT NULL, "
                "scopes TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS oauth_codes ("
                "hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES oauth_grants(id) "
                "ON DELETE CASCADE, encrypted BLOB NOT NULL, expires REAL NOT NULL, "
                "used INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS oauth_tokens ("
                "hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES oauth_grants(id) "
                "ON DELETE CASCADE, kind TEXT NOT NULL, scopes TEXT NOT NULL, "
                "expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)",
                "CREATE INDEX IF NOT EXISTS oauth_token_grant ON oauth_tokens(grant_id)",
            ):
                db.execute(statement)
            # Nullable migration preserves legacy grants without guessing their callback.
            columns = {row[1] for row in db.execute("PRAGMA table_info(oauth_grants)")}
            if "callback_origin" not in columns:
                db.execute("ALTER TABLE oauth_grants ADD COLUMN callback_origin TEXT")

    def _cleanup(self, db):
        now = time.time()
        db.execute("DELETE FROM oauth_requests WHERE expires<=?", (now,))
        db.execute("DELETE FROM oauth_grants WHERE expires<=?", (now,))
        db.execute("DELETE FROM oauth_codes WHERE expires<=?", (now,))
        db.execute("DELETE FROM oauth_tokens WHERE kind='access' AND expires<=?", (now,))
        db.execute(
            "DELETE FROM oauth_clients WHERE expires<=? "
            "AND id NOT IN (SELECT client FROM oauth_grants) "
            "AND id NOT IN (SELECT client FROM oauth_requests)",
            (now,),
        )

    def _client(self, db, client_id):
        row = db.execute(
            "SELECT metadata FROM oauth_clients WHERE id=? AND (expires>? OR EXISTS ("
            "SELECT 1 FROM oauth_grants g JOIN players p ON p.id=g.player "
            "WHERE g.client=oauth_clients.id AND g.revoked=0 AND g.expires>? AND p.active=1))",
            (client_id, time.time(), time.time()),
        ).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    @_offload
    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.store.transaction() as db:
            return self._client(db, client_id)

    @_offload
    def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client = client_info
        if (
            not client.client_id
            or client.client_secret
            or client.token_endpoint_auth_method != "none"
            or not client.grant_types
            or "authorization_code" not in client.grant_types
            or not set(client.grant_types) <= {"authorization_code", "refresh_token"}
            or client.response_types != ["code"]
            or not set((client.scope or "").split()) <= set(SCOPES)
        ):
            raise RegistrationError("invalid_client_metadata")
        if not client.redirect_uris:
            raise RegistrationError("invalid_redirect_uri")
        for uri in client.redirect_uris:
            value = str(uri)
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or parts.netloc not in {"chatgpt.com", "claude.ai"}
                or parts.username
                or parts.password
                or "#" in value
                or "*" in value
            ):
                raise RegistrationError("invalid_redirect_uri")
        with self.store.transaction() as db:
            self._cleanup(db)
            count = db.execute(
                "SELECT count(*) FROM oauth_clients c WHERE NOT EXISTS ("
                "SELECT 1 FROM oauth_grants g JOIN players p ON p.id=g.player "
                "WHERE g.client=c.id AND g.revoked=0 AND g.expires>? AND p.active=1)",
                (time.time(),),
            ).fetchone()[0]
            if count >= self.MAX_CLIENTS:
                raise RegistrationError(
                    "invalid_client_metadata", "Registration capacity exhausted"
                )
            if db.execute("SELECT 1 FROM oauth_clients WHERE id=?", (client.client_id,)).fetchone():
                raise RegistrationError("invalid_client_metadata", "Client already registered")
            db.execute(
                "INSERT INTO oauth_clients VALUES (?, ?, ?)",
                (client.client_id, client.model_dump_json(), time.time() + self.CLIENT_TTL),
            )

    @_offload
    def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        with self.store.transaction() as db:
            self._cleanup(db)
            registered = self._client(db, client.client_id)
            if registered is None:
                raise AuthorizeError("unauthorized_client")
            if str(params.redirect_uri) not in {str(uri) for uri in registered.redirect_uris}:
                raise AuthorizeError("invalid_request")
            if params.resource != self.resource:
                raise AuthorizeError("invalid_target")
            scopes = params.scopes or ["msf:read"]
            if (
                "msf:read" not in scopes
                or not set(scopes) <= set(SCOPES)
                or not set(scopes) <= set((registered.scope or "").split())
                or ("offline_access" in scopes and "refresh_token" not in registered.grant_types)
            ):
                raise AuthorizeError("invalid_scope")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
                raise AuthorizeError("invalid_request")
            count = db.execute("SELECT count(*) FROM oauth_requests").fetchone()[0]
            if count >= self.MAX_REQUESTS:
                raise AuthorizeError("temporarily_unavailable")
            raw = secrets.token_urlsafe(32)
            digest = _hash(raw)
            payload = params.model_dump(mode="json")
            payload["scopes"] = scopes
            db.execute(
                "INSERT INTO oauth_requests VALUES (?, ?, NULL, ?, ?)",
                (
                    digest,
                    client.client_id,
                    self.store.encrypt_private("oauth:request:" + digest, payload),
                    time.time() + self.REQUEST_TTL,
                ),
            )
        return self.public_url + "/login?" + urlencode({"request_id": raw})

    def _request(self, db, request_id):
        digest = _hash(request_id)
        row = db.execute(
            "SELECT * FROM oauth_requests WHERE hash=? AND expires>?", (digest, time.time())
        ).fetchone()
        if row is None or self._client(db, row[1]) is None:
            raise AuthorizeError("invalid_request")
        payload = self.store.decrypt_private("oauth:request:" + row[0], row[3])
        if payload["resource"] != self.resource:
            raise AuthorizeError("invalid_target")
        return row

    @staticmethod
    def _active(db, player_id):
        return (
            db.execute("SELECT 1 FROM players WHERE id=? AND active=1", (player_id,)).fetchone()
            is not None
        )

    @_offload
    def complete_login(self, request_id: str, player_id: str) -> str:
        """Bind a verified identity; caller verifies browser session and CSRF first."""
        with self.store.transaction() as db:
            row = self._request(db, request_id)
            if not self._active(db, player_id) or row[2] not in (None, player_id):
                raise AuthorizeError("access_denied")
            db.execute("UPDATE oauth_requests SET player=? WHERE hash=?", (player_id, row[0]))
        return self.public_url + "/consent?" + urlencode({"request_id": request_id})

    @_offload
    def pending_consent(self, request_id: str, player_id: str) -> dict:
        """Only display-safe consent fields for an active, bound pending request."""
        with self.store.transaction() as db:
            row = self._request(db, request_id)
            if row[2] != player_id or not self._active(db, player_id):
                raise AuthorizeError("access_denied")
            payload = self.store.decrypt_private("oauth:request:" + row[0], row[3])
            return {
                "client_id": row[1],
                "resource": self.resource,
                "scopes": payload["scopes"],
                "callback_origin": self._callback_origin(payload["redirect_uri"]),
            }

    @staticmethod
    def _callback_origin(redirect_uri: str) -> str | None:
        """Recognize only the exact HTTPS origins allowed by registration."""
        parts = urlsplit(redirect_uri)
        if parts.scheme == "https" and parts.netloc in {"chatgpt.com", "claude.ai"}:
            return "https://" + parts.netloc
        return None

    @_offload
    def approve(self, request_id: str, player_id: str) -> str:
        """Issue one code after Task3 verifies session binding and consent CSRF."""
        with self.store.transaction() as db:
            row = self._request(db, request_id)
            if row[2] != player_id or not self._active(db, player_id):
                raise AuthorizeError("access_denied")
            payload = self.store.decrypt_private("oauth:request:" + row[0], row[3])
            grant = secrets.token_urlsafe(24)
            db.execute(
                "INSERT INTO oauth_grants "
                "(id, client, player, resource, scopes, expires, revoked, callback_origin) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    grant,
                    row[1],
                    player_id,
                    self.resource,
                    json.dumps(payload["scopes"]),
                    time.time() + self.FAMILY_TTL,
                    self._callback_origin(payload["redirect_uri"]),
                ),
            )
            raw = secrets.token_urlsafe(32)
            digest = _hash(raw)
            db.execute(
                "INSERT INTO oauth_codes VALUES (?, ?, ?, ?, 0)",
                (
                    digest,
                    grant,
                    self.store.encrypt_private("oauth:code:" + digest, payload),
                    time.time() + self.CODE_TTL,
                ),
            )
            db.execute("DELETE FROM oauth_requests WHERE hash=?", (row[0],))
            uri = urlsplit(payload["redirect_uri"])
            query = parse_qsl(uri.query, keep_blank_values=True) + [("code", raw)]
            if payload["state"] is not None:
                query.append(("state", payload["state"]))
            return urlunsplit(uri._replace(query=urlencode(query)))

    def _credential(self, db, table, raw, client_id=None):
        db.row_factory = sqlite3.Row
        row = db.execute(
            f"SELECT c.*, g.client, g.player, g.resource, g.scopes AS grant_scopes, "
            f"g.expires AS family_expires FROM {table} c JOIN oauth_grants g ON g.id=c.grant_id "
            "JOIN players p ON p.id=g.player WHERE c.hash=? AND c.expires>? "
            "AND g.expires>? AND g.revoked=0 AND p.active=1 AND g.resource=?",
            (_hash(raw), time.time(), time.time(), self.resource),
        ).fetchone()
        if row is None or (client_id is not None and row["client"] != client_id):
            return None
        return row

    @_offload
    def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_codes", authorization_code, client.client_id)
            if row is None or row["used"]:
                return None
            payload = self.store.decrypt_private("oauth:code:" + row["hash"], row["encrypted"])
            return AuthorizationCode(
                code=authorization_code,
                client_id=row["client"],
                expires_at=row["expires"],
                subject=row["player"],
                **payload,
            )

    def _issue(self, db, row, scopes):
        access = secrets.token_urlsafe(32)
        now = time.time()
        db.execute(
            "INSERT INTO oauth_tokens VALUES (?, ?, ?, ?, ?, 0)",
            (
                _hash(access),
                row["grant_id"],
                "access",
                json.dumps(scopes),
                min(now + self.ACCESS_TTL, row["family_expires"]),
            ),
        )
        refresh = None
        if "offline_access" in scopes:
            refresh = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO oauth_tokens VALUES (?, ?, ?, ?, ?, 0)",
                (
                    _hash(refresh),
                    row["grant_id"],
                    "refresh",
                    json.dumps(scopes),
                    row["family_expires"],
                ),
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(min(self.ACCESS_TTL, row["family_expires"] - now)),
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    @_offload
    def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_codes", authorization_code.code, client.client_id)
            if row is None or row["used"]:
                raise TokenError("invalid_grant")
            db.execute("UPDATE oauth_codes SET used=1 WHERE hash=?", (row["hash"],))
            return self._issue(db, row, json.loads(row["grant_scopes"]))

    @_offload
    def load_access_token(self, token: str) -> AccessToken | None:
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_tokens", token)
            if row is None or row["kind"] != "access" or row["used"]:
                return None
            return AccessToken(
                token=token,
                client_id=row["client"],
                scopes=json.loads(row["scopes"]),
                expires_at=int(row["expires"]),
                resource=row["resource"],
                subject=row["player"],
                claims={"iss": self.public_url},
            )

    @_offload
    def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_tokens", refresh_token, client.client_id)
            if row is None or row["kind"] != "refresh":
                return None
            if row["used"]:
                db.execute("UPDATE oauth_grants SET revoked=1 WHERE id=?", (row["grant_id"],))
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row["client"],
                scopes=json.loads(row["scopes"]),
                expires_at=int(row["expires"]),
                resource=row["resource"],
                subject=row["player"],
            )

    @_offload
    def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        replay = False
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_tokens", refresh_token.token, client.client_id)
            if row is None or row["kind"] != "refresh":
                raise TokenError("invalid_grant")
            if not set(scopes) <= set(json.loads(row["scopes"])) or "msf:read" not in scopes:
                raise TokenError("invalid_scope")
            if row["used"]:
                db.execute("UPDATE oauth_grants SET revoked=1 WHERE id=?", (row["grant_id"],))
                replay = True
            else:
                db.execute("UPDATE oauth_tokens SET used=1 WHERE hash=?", (row["hash"],))
                result = self._issue(db, row, scopes)
        # Raise after committing the family revocation, never roll it back.
        if replay:
            raise TokenError("invalid_grant")
        return result

    @_offload
    def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self.store.transaction() as db:
            row = self._credential(db, "oauth_tokens", token.token, token.client_id)
            if row:
                db.execute("UPDATE oauth_grants SET revoked=1 WHERE id=?", (row["grant_id"],))

    @_offload
    def list_grants(self, player_id: str) -> list[dict]:
        with self.store.transaction() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row) | {"scopes": json.loads(row["scopes"])}
                for row in db.execute(
                    "SELECT g.id, g.client AS client_id, g.resource, g.scopes, "
                    "g.expires AS expires_at, g.callback_origin "
                    "FROM oauth_grants g JOIN players p ON p.id=g.player "
                    "WHERE g.player=? AND p.active=1 AND g.revoked=0 AND g.expires>?",
                    (player_id, time.time()),
                )
            ]

    @_offload
    def revoke_grant(self, player_id: str, grant_id: str) -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE oauth_grants SET revoked=1 WHERE id=? AND player=?", (grant_id, player_id)
            )

    @_offload
    def revoke_player(self, player_id: str) -> None:
        with self.store.transaction() as db:
            db.execute("UPDATE oauth_grants SET revoked=1 WHERE player=?", (player_id,))
            db.execute("DELETE FROM oauth_requests WHERE player=?", (player_id,))

    @_offload
    def invalidate_all(self) -> None:
        with self.store.transaction() as db:
            db.execute("UPDATE oauth_grants SET revoked=1")
            db.execute("DELETE FROM oauth_requests")

    def auth_routes(self) -> list[Route]:
        """SDK handlers plus public-client metadata and exact token-resource guard."""
        issuer = AnyHttpUrl(self.public_url)
        # A registration without `scope` must not pin the client to read-only;
        # the scopes actually granted are chosen per authorization request.
        options = ClientRegistrationOptions(
            enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
        )
        revocation = RevocationOptions(enabled=True)
        routes = create_auth_routes(
            self, issuer, client_registration_options=options, revocation_options=revocation
        )
        metadata = build_metadata(issuer, None, options, revocation)
        # Assign strings through the model's URL-preserving validation.
        metadata = metadata.model_validate(metadata.model_dump() | {"issuer": self.public_url})
        metadata.token_endpoint_auth_methods_supported = ["none"]
        metadata.revocation_endpoint_auth_methods_supported = ["none"]
        for route in routes:
            if route.path == "/.well-known/oauth-authorization-server":
                route.app = cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"])
            elif route.path in {"/token", "/revoke"}:
                resource = self.resource if route.path == "/token" else None
                route.app = RequestBodyLimitMiddleware(
                    _SDKFormCompatibility(route.app, resource), DEFAULT_MAX_REQUEST_BODY_SIZE
                )
        # Pass plain strings: the metadata model keeps an empty path only when it
        # validates from a string, so the issuer identifier stays slash-free.
        return routes + create_protected_resource_routes(
            self.resource,  # type: ignore[arg-type]
            [self.public_url],  # type: ignore[list-item]
            scopes_supported=SCOPES,
        )
