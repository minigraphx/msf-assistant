"""Authenticated stateless HTTP transport with bounded request admission."""

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

import anyio
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from msf_assistant.advisor_context import ContextStore
from msf_assistant.advisor_instructions import ADVISOR_INSTRUCTIONS
from msf_assistant.hosted_oauth import SCOPES
from msf_assistant.hosted_pages import DEFAULT_SERVICE_NAME
from msf_assistant.hosted_sync import HostedSync, HostedSyncError
from msf_assistant.hosted_web import account_routes
from msf_assistant.mcp_server import AdvisorServer, ToolMessages, register_tools
from msf_assistant.snapshot import SnapshotError, SnapshotReader

logger = logging.getLogger("msf_assistant.hosted")
_player: ContextVar[str] = ContextVar("hosted_player")
HOSTED_MESSAGES = ToolMessages(
    read_failed="Your game data could not be read; run refresh_data.",
    refresh_failed="Refresh failed; try again later. Your previous data is retained.",
    passthrough=HostedSyncError,
)
MISSING_SNAPSHOT = (
    "No game data stored for this account yet; run refresh_data to fetch it from MSF."
)
# MSF API Terms of Use: Data pulled from the API is retained at most 30 days.
SNAPSHOT_TTL_SECONDS = 30 * 86400
EXPIRED_SNAPSHOT = (
    "The stored game data expired after 30 days and was deleted; run refresh_data to fetch "
    "it again from MSF."
)


def expire_snapshot(path, *, now=None):
    """Delete a snapshot older than the TTL; return True when it was removed."""
    try:
        age = (now or time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    if age <= SNAPSHOT_TTL_SECONDS:
        return False
    path.unlink(missing_ok=True)
    return True


PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource/mcp"
WRITE_TOOLS = frozenset(
    {
        "save_goal",
        "save_player_fact",
        "save_recommendation",
        "delete_advisor_record",
        "refresh_data",
    }
)


@dataclass(frozen=True)
class HostedLimits:
    body_timeout: float = 30.0
    request_bytes: int = 1024 * 1024
    response_bytes: int = 24 * 1024 * 1024
    requests_per_minute: int = 60
    active_requests: int = 4
    rate_players: int = 10000
    context_bytes: int = 2 * 1024 * 1024


class PlayerBackend:
    """Resolve identity inside the worker; hold the deletion lock across each operation."""

    def __init__(self, store, kind, limits):
        self.store, self.kind, self.limits = store, kind, limits

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            player_id = _player.get()
            with self.store.player_lock(player_id):
                directory = self.store.player_dir(player_id)
                if self.kind == "snapshot":
                    expired = expire_snapshot(directory / "snapshot.json")
                    if name != "status" and expired:
                        raise SnapshotError(EXPIRED_SNAPSHOT)
                    if name != "status" and not (directory / "snapshot.json").exists():
                        raise SnapshotError(MISSING_SNAPSHOT)
                backend = (
                    SnapshotReader(directory / "snapshot.json")
                    if self.kind == "snapshot"
                    else ContextStore(
                        directory / "context.json", max_bytes=self.limits.context_bytes
                    )
                )
                return getattr(backend, name)(*args, **kwargs)

        return invoke


class PublicGuard:
    def __init__(self, app, provider, sync, limits):
        self.app, self.provider, self.sync, self.limits = app, provider, sync, limits
        self.rates = {}
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cheap = scope["method"] in ("GET", "HEAD") and (
            scope["path"] == "/health" or scope["path"].startswith("/.well-known/")
        )
        if cheap:
            return await self.app(scope, receive, send)
        if self.active >= self.limits.active_requests:
            return await JSONResponse({"error": "Service temporarily busy"}, status_code=503)(
                scope, receive, send
            )
        self.active += 1
        started = False

        async def track_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.request(scope, receive, track_send)
        except Exception as exc:
            logger.warning("%s failed: %s", scope.get("path"), type(exc).__name__)
            if not started:
                await JSONResponse({"error": "Service temporarily unavailable"}, status_code=503)(
                    scope, receive, send
                )
        finally:
            self.active -= 1

    async def request(self, scope, receive, send):
        async def reject(status, message, headers=None):
            await JSONResponse({"error": message}, status_code=status, headers=headers)(
                scope, receive, send
            )

        is_mcp = scope["path"].rstrip("/") == "/mcp"
        token = None
        if is_mcp:
            headers = dict(scope.get("headers", []))
            authorization = headers.get(b"authorization", b"").decode("latin1")
            if authorization.startswith("Bearer "):
                token = await self.provider.load_access_token(authorization[7:])
            if (
                token is None
                or token.resource != self.provider.resource
                or token.claims.get("iss") != self.provider.public_url
                or (token.expires_at is not None and token.expires_at <= time.time())
            ):
                return await reject(
                    401,
                    "Authentication required",
                    {
                        # Clients take requested scopes from this challenge first, then
                        # from protected-resource metadata; advertise every scope.
                        "WWW-Authenticate": f'Bearer resource_metadata="{self.provider.public_url}'
                        '/.well-known/oauth-protected-resource/mcp", '
                        f'scope="{" ".join(SCOPES)}"'
                    },
                )
            if "msf:read" not in token.scopes:
                return await reject(403, "Required scope missing")
            now = time.monotonic()
            self.rates = {key: value for key, value in self.rates.items() if value[0] > now}
            if token.subject not in self.rates:
                if len(self.rates) >= self.limits.rate_players:
                    return await reject(503, "Service temporarily busy")
                self.rates[token.subject] = [now + 60, 0]
            window = self.rates[token.subject]
            if window[1] >= self.limits.requests_per_minute:
                return await reject(
                    429,
                    "Request rate exceeded",
                    {"Retry-After": str(max(1, int(window[0] - now) + 1))},
                )
            window[1] += 1
        # Bound all public POST bodies, including OAuth and account forms.
        body = bytearray()
        try:
            with anyio.fail_after(self.limits.body_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.limits.request_bytes:
                        return await reject(413, "Request body too large")
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "Request body timed out")
        refresh = False
        if is_mcp:
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                params = payload.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                if (
                    isinstance(name, str)
                    and name in WRITE_TOOLS
                    and "msf:write" not in token.scopes
                ):
                    return await reject(403, "Required write scope missing")
                refresh = name == "refresh_data"
            if refresh and not self.sync.capacity.acquire(blocking=False):
                return await reject(503, "Refresh capacity unavailable", {"Retry-After": "1"})
        identity = _player.set(token.subject) if token else None
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        # Stateless JSON responses can be buffered with a finite cap before headers go out.
        messages, size = [], 0

        async def bounded_send(message):
            nonlocal size
            size += len(message.get("body", b""))
            if size > self.limits.response_bytes:
                raise ValueError("Response too large")
            messages.append(message)

        try:
            await self.app(scope, replay, bounded_send)
        except Exception as exc:
            logger.warning("%s failed: %s", scope.get("path"), type(exc).__name__)
            await reject(503, "Service temporarily unavailable")
        else:
            for message in messages:
                await send(message)
        finally:
            if identity is not None:
                _player.reset(identity)
            if is_mcp and refresh:
                self.sync.capacity.release()


def create_hosted_app(
    store,
    provider,
    identity,
    settings,
    public_url,
    *,
    limits=None,
    operator=None,
    service_name=DEFAULT_SERVICE_NAME,
):
    limits = limits or HostedLimits()
    public_url = public_url.rstrip("/")
    if public_url != provider.public_url:
        raise ValueError("OAuth issuer must match the public URL")
    sync = HostedSync(store, settings, login_url=public_url + "/login")
    server = AdvisorServer(
        service_name,
        version="0.4.0",
        instructions=ADVISOR_INSTRUCTIONS,
        log_level="WARNING",
        token_verifier=ProviderTokenVerifier(provider),
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url + "/mcp",
            required_scopes=["msf:read"],
            validate_token_resource=True,
        ),
    )
    register_tools(
        server,
        PlayerBackend(store, "snapshot", limits),
        PlayerBackend(store, "context", limits),
        refresh=lambda: sync.refresh(_player.get(), admitted=True),
        messages=HOSTED_MESSAGES,
    )
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=limits.request_bytes,
        host=urlsplit(public_url).hostname,
    )

    async def health(request):
        return JSONResponse({"status": "ok"})

    # AuthSettings makes the SDK mount its own protected-resource metadata built
    # from required_scopes only; Starlette matches the first route, so drop it in
    # favour of the provider's document that advertises every grantable scope.
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != PROTECTED_RESOURCE_PATH]
    app.routes.extend(provider.auth_routes())
    app.routes.extend(
        account_routes(
            store, provider, identity, public_url, operator=operator, service_name=service_name
        )
    )
    app.routes.append(Route("/health", health))
    guarded = PublicGuard(app, provider, sync, limits)
    return guarded
