"""Small English account UI; browser credentials never contain upstream tokens.

Startup initializes tables synchronously. Runtime file/HTTP/SQLite work runs in
bounded workers. Acquire maintenance before player locks when composing hosting.
"""

import hashlib
import logging
import secrets
import shutil
import time
from functools import partial
from html import escape
from urllib.parse import parse_qsl, urlsplit

import anyio
from starlette.datastructures import FormData
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from msf_assistant.config import DEFAULT_OAUTH_BASE_URL
from msf_assistant.hosted_pages import DEFAULT_SERVICE_NAME, Operator, privacy_body, terms_body

__all__ = ["Operator", "account_routes", "page"]


logger = logging.getLogger("msf_assistant.hosted")
# MSF API Terms of Use §2k: name Scopely as the source of the Data on every page,
# without implying endorsement; no Scopely/Marvel marks in the title or URL.
ATTRIBUTION = (
    "<p>Game data is provided by Scopely's Marvel Strike Force API. This service is "
    "not endorsed by, sponsored by or affiliated with Scopely or Marvel.</p>"
)
COOKIE = "__Host-msf_session"
SESSION_TTL = 8 * 3600
LOGIN_TTL = 600
MAX_SESSIONS = 1000
PERMISSIONS = {
    "msf:read": "Read your own game data and advisor context",
    "msf:write": "Change your own advisor context and refresh your data",
    "offline_access": "Stay connected without signing in again (up to 30 days)",
}


def client_label(connection):
    return {
        "https://chatgpt.com": "ChatGPT (chatgpt.com)",
        "https://claude.ai": "Claude (claude.ai)",
    }.get(connection.get("callback_origin"), "Unknown connection (older grant)")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def module_page(body, status=200, *, form_origins="", title=DEFAULT_SERVICE_NAME):
    form_policy = "form-action 'self'" + (" " + form_origins if form_origins else "")
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>"
        + escape(title)
        + "</title><body>"
        + body
        + ATTRIBUTION
        + '<p><a href="/">Help</a> · <a href="/privacy.html">Privacy</a> · '
        '<a href="/terms.html">Terms</a></p></body></html>',
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; " + form_policy + "; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


page = module_page


def redirect(url):
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


def form(action, csrf, contents):
    return (
        '<form method="post" action="'
        + action
        + '"><input type="hidden" name="csrf" value="'
        + escape(csrf, quote=True)
        + '">'
        + contents
        + "</form>"
    )


class BrowserSessions:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS browser_sessions ("
                "hash TEXT PRIMARY KEY, encrypted BLOB NOT NULL, expires REAL NOT NULL)"
            )

    def create(self, payload, old=None):
        raw = secrets.token_urlsafe(32)
        key = digest(raw)
        payload = dict(payload, csrf=secrets.token_urlsafe(32))
        with self.store.transaction() as db:
            db.execute("DELETE FROM browser_sessions WHERE expires<=?", (time.time(),))
            if old:
                db.execute("DELETE FROM browser_sessions WHERE hash=?", (digest(old),))
            if db.execute("SELECT count(*) FROM browser_sessions").fetchone()[0] >= MAX_SESSIONS:
                raise ValueError("Session capacity")
            db.execute(
                "INSERT INTO browser_sessions VALUES (?, ?, ?)",
                (
                    key,
                    self.store.encrypt_private("browser:" + key, payload),
                    time.time() + (SESSION_TTL if payload.get("player") else LOGIN_TTL),
                ),
            )
        return raw, payload

    def read(self, raw, consume_state=None):
        if not raw:
            raise ValueError("Missing session")
        key = digest(raw)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT encrypted FROM browser_sessions WHERE hash=? AND expires>?",
                (key, time.time()),
            ).fetchone()
            if not row:
                raise ValueError("Expired session")
            payload = self.store.decrypt_private("browser:" + key, row[0])
            if consume_state is not None:
                if (
                    not payload.get("state")
                    or payload.get("state_expires", 0) <= time.time()
                    or not secrets.compare_digest(payload["state"], digest(consume_state))
                ):
                    raise ValueError("Invalid state")
                payload.pop("state")
                db.execute(
                    "UPDATE browser_sessions SET encrypted=? WHERE hash=?",
                    (self.store.encrypt_private("browser:" + key, payload), key),
                )
            if payload.get("player"):
                self.store._require(db, payload["player"])
            return payload

    def begin(self, raw, payload):
        state = secrets.token_urlsafe(32)
        key = digest(raw)
        payload = dict(payload, state=digest(state), state_expires=time.time() + LOGIN_TTL)
        with self.store.transaction() as db:
            result = db.execute(
                "UPDATE browser_sessions SET encrypted=? WHERE hash=? AND expires>?",
                (self.store.encrypt_private("browser:" + key, payload), key, time.time()),
            )
            if result.rowcount != 1:
                raise ValueError("Expired session")
        return state


def account_routes(
    store, provider, identity, public_url, *, operator=None, service_name=DEFAULT_SERVICE_NAME
):
    page = partial(module_page, title=service_name)
    public_url = public_url.rstrip("/")
    if public_url != provider.public_url:
        raise ValueError("Public origins must match")
    parts = urlsplit(public_url)
    origin = parts.scheme + "://" + parts.netloc
    upstream = urlsplit(DEFAULT_OAUTH_BASE_URL)  # hosted config enforces the official issuer
    msf_origin = upstream.scheme + "://" + upstream.netloc
    sessions = BrowserSessions(store)

    async def run(fn, *args):
        return await anyio.to_thread.run_sync(fn, *args)

    def cookie(response, raw):
        response.set_cookie(
            COOKIE, raw, max_age=SESSION_TTL, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    async def session(request):
        return await run(sessions.read, request.cookies.get(COOKIE))

    async def post(request):
        payload = await session(request)
        if request.headers.get("origin") != origin:
            raise ValueError("Wrong origin")
        if int(request.headers.get("content-length", "0")) > 8192:
            raise ValueError("Large form")
        if request.headers.get("content-type", "").split(";")[0] != (
            "application/x-www-form-urlencoded"
        ):
            raise ValueError("Unsupported form")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 8192:
                raise ValueError("Large form")
            body.extend(chunk)
        data = FormData(parse_qsl(body.decode(), keep_blank_values=True, max_num_fields=16))
        if any(len(data.getlist(key)) != 1 for key in data):
            raise ValueError("Duplicate fields")
        csrf = data.get("csrf", "")
        if not isinstance(csrf, str) or not secrets.compare_digest(csrf, payload["csrf"]):
            raise ValueError("Invalid CSRF")
        return payload, data

    async def home(request):
        return page(
            "<h1>"
            + escape(service_name)
            + "</h1><p>Connect your own Marvel Strike Force account to "
            "your AI assistant. Every player signs in directly with MSF; no separate "
            "password is needed.</p><p>Once connected, you can refresh your data from "
            "inside the assistant. Signing in does not start a full "
            'synchronization.</p><a href="/login">Sign in / register with MSF</a> '
            '<a href="/account">Account and connections</a>'
            "<h2>Connect ChatGPT or Claude</h2>"
            "<p>Use this MCP address: <code>" + escape(public_url + "/mcp") + "</code></p>"
            "<ol><li><strong>ChatGPT:</strong> Open Settings → Security and sign-in → "
            "Developer mode. Under Plugins, add the public MCP server with the address "
            "above and choose Connect. "
            '<a href="https://developers.openai.com/plugins/deploy/connect-chatgpt">'
            "Official ChatGPT guide</a>.</li>"
            "<li><strong>Claude:</strong> Open Customize → Connectors → Add custom "
            "connector. Enter the address. If the dialog asks about the OAuth client, "
            "choose \u201cRegister automatically\u201d (not the published identity and not "
            "your own client). Then sign in. "
            '<a href="https://claude.com/docs/connectors/custom/remote-mcp">'
            "Official Claude guide</a>.</li></ol>"
            "<p>The client registers itself automatically. Sign in to your own MSF account "
            "in the browser and approve the requested permissions. You do not need an MSF "
            "app key or client secret. Availability and approvals depend on your plan and "
            "your workspace rules.</p>"
        )

    async def privacy(request):
        return page(privacy_body(operator, public_url, service_name))

    async def terms(request):
        return page(terms_body(operator, public_url, service_name))

    async def login(request):
        if request.method == "POST":
            payload, data = await post(request)
            state = await run(sessions.begin, request.cookies[COOKIE], payload)
            return redirect(identity.begin(state))
        request_id = request.query_params.get("request_id")
        # Starting a new login invalidates any old browser binding.
        raw, payload = await run(
            sessions.create, {"request_id": request_id}, request.cookies.get(COOKIE)
        )
        return cookie(
            page(
                "<h1>Sign in with MSF</h1>"
                + form("/login", payload["csrf"], "<button>Sign in with MSF</button>"),
                form_origins=msf_origin,
            ),
            raw,
        )

    async def callback(request):
        if any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
            raise ValueError("Duplicate callback")
        state, code = request.query_params.get("state"), request.query_params.get("code")
        if not state or not code or request.query_params.get("error"):
            raise ValueError("Invalid callback")
        old = request.cookies.get(COOKIE)
        payload = await run(sessions.read, old, state)
        issuer, subject, tokens = await run(identity.exchange, code)

        def persist():
            player = store.player(issuer, subject)
            with store.player_lock(player.id):
                store.save_tokens(player.id, tokens)
            return player.id

        player = await run(persist)
        target = "/account"
        if payload.get("request_id"):
            target = await provider.complete_login(payload["request_id"], player)
        raw, _ = await run(
            sessions.create, {"player": player, "request_id": payload.get("request_id")}, old
        )
        return cookie(redirect(target), raw)

    async def consent(request):
        payload = await session(request)
        request_id = request.query_params.get("request_id")
        if not request_id or request_id != payload.get("request_id") or not payload.get("player"):
            raise ValueError("Wrong request binding")
        pending = await provider.pending_consent(request_id, payload["player"])
        if request.method == "POST":
            await post(request)
            return redirect(await provider.approve(request_id, payload["player"]))
        account = await run(store.require_player, payload["player"])
        body = (
            "<h1>Allow connection</h1><p>"
            + escape(client_label(pending))
            + "</p><p>Connection ID: "
            + escape(pending["client_id"])
            + "</p><p>Connected MSF account: <code>"
            + escape(account.subject[:8])
            + "…</code></p><ul>"
        )
        body += (
            "".join("<li>" + escape(PERMISSIONS[s]) + "</li>" for s in pending["scopes"]) + "</ul>"
        )
        action = "/consent?request_id=" + escape(request_id, quote=True)
        return page(
            body
            + form(action, payload["csrf"], "<button>Allow access</button>")
            + '<p><a href="/account">Cancel</a></p>',
            form_origins="https://chatgpt.com https://claude.ai",
        )

    async def account(request):
        try:
            payload = await session(request)
            player = payload["player"]
        except (ValueError, KeyError):
            return redirect("/login")
        grants = await provider.list_grants(player)
        body = "<h1>Your account</h1><h2>Connections</h2>"
        for grant in grants:
            body += (
                "<p>"
                + escape(client_label(grant))
                + "</p><p>Connection ID: "
                + escape(grant["client_id"])
                + "</p><p>"
                + escape("; ".join(PERMISSIONS[s] for s in grant["scopes"]))
                + "</p>"
            )
            body += form(
                "/account/revoke",
                payload["csrf"],
                '<input type="hidden" name="grant" value="'
                + escape(grant["id"], quote=True)
                + '"><button>Revoke connection</button>',
            )
        body += "<h2>Delete account</h2><p>Removes your active data and all connections.</p>"
        body += form(
            "/account/delete",
            payload["csrf"],
            '<label><input type="checkbox" name="confirm" value="delete" required>'
            " Permanently delete my account</label><button>Confirm deletion</button>",
        )
        return page(body)

    async def revoke(request):
        payload, data = await post(request)
        await provider.revoke_grant(payload["player"], data.get("grant", ""))
        return redirect("/account")

    async def delete(request):
        payload, data = await post(request)
        if data.get("confirm") != "delete":
            raise ValueError("Confirmation required")

        def remove():
            player = payload["player"]
            with store.player_lock(player):
                path = store.player_dir(player)
                store.deactivate_player(player)
                anyio.from_thread.run(provider.revoke_player, player)
                shutil.rmtree(path)

        await run(remove)
        response = redirect("/")
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    def safe(handler):
        async def handle(request):
            try:
                return await handler(request)
            except Exception as exc:
                # Operators get the route and exception class; never the message,
                # which could carry upstream bodies, parameters or credentials.
                logger.warning("%s failed: %s", request.url.path, type(exc).__name__)
                # No upstream response bodies, request parameters or credentials in HTML.
                return page(
                    "<h1>Action not possible</h1><p>The sign-in or session is invalid or "
                    "expired, or the service is temporarily unavailable. Please start "
                    "the sign-in again.</p>"
                    '<a href="/login">Sign in again</a>',
                    400,
                )

        return handle

    return [
        Route(path, safe(handler), methods=methods)
        for path, handler, methods in [
            ("/", home, ["GET"]),
            ("/privacy.html", privacy, ["GET"]),
            ("/terms.html", terms, ["GET"]),
            ("/login", login, ["GET", "POST"]),
            ("/oauth/callback", callback, ["GET"]),
            ("/consent", consent, ["GET", "POST"]),
            ("/account", account, ["GET"]),
            ("/account/revoke", revoke, ["POST"]),
            ("/account/delete", delete, ["POST"]),
        ]
    ]
