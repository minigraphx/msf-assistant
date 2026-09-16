"""Small German account UI; browser credentials never contain upstream tokens.

Startup initializes tables synchronously. Runtime file/HTTP/SQLite work runs in
bounded workers. Acquire maintenance before player locks when composing hosting.
"""

import hashlib
import secrets
import shutil
import time
from html import escape
from urllib.parse import parse_qsl, urlsplit

import anyio
from starlette.datastructures import FormData
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

COOKIE = "__Host-msf_session"
SESSION_TTL = 8 * 3600
LOGIN_TTL = 600
MAX_SESSIONS = 1000
PERMISSIONS = {
    "msf:read": "Eigene Spieldaten und Kontext lesen",
    "msf:write": "Eigenen Spielkontext ändern und Daten aktualisieren",
    "offline_access": "Zugriff ohne erneute Anmeldung (bis zu 30 Tage)",
}


def client_label(connection):
    return {
        "https://chatgpt.com": "ChatGPT (chatgpt.com)",
        "https://claude.ai": "Claude (claude.ai)",
    }.get(connection.get("callback_origin"), "Unbekannte Verbindung (ältere Freigabe)")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def page(body, status=200, *, form_origins=""):
    form_policy = "form-action 'self'" + (" " + form_origins if form_origins else "")
    return HTMLResponse(
        '<!doctype html><html lang="de"><meta charset="utf-8">'
        "<title>MSF Assistant</title><body>"
        + body
        + '<p><a href="/">Hilfe</a> · <a href="/privacy.html">Datenschutz</a></p></body></html>',
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


def account_routes(store, provider, identity, public_url):
    public_url = public_url.rstrip("/")
    if public_url != provider.public_url:
        raise ValueError("Public origins must match")
    parts = urlsplit(public_url)
    origin = parts.scheme + "://" + parts.netloc
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
            "<h1>MSF Assistant</h1><p>Verbinde dein eigenes MSF-Konto mit deinem "
            "Assistenten. Jeder Spieler meldet sich direkt bei MSF an; ein lokales "
            "Passwort ist nicht nötig.</p><p>Nach der Verbindung kannst du deine Daten "
            "im Assistenten aktualisieren. Die Anmeldung startet keine vollständige "
            'Synchronisierung.</p><a href="/login">Mit MSF anmelden / registrieren</a> '
            '<a href="/account">Konto und Verbindungen</a>'
        )

    async def privacy(request):
        return page(
            "<h1>Datenschutz</h1><p>Der gehostete Dienst verarbeitet deine eigenen "
            "MSF-Spieldaten und den von dir gespeicherten Spielkontext. Er speichert "
            "deine bestätigte MSF-Kennung, verschlüsselte MSF-Zugangsdaten, "
            "Browsersitzungen und Berechtigungen verbundener Clients. Freigegebene "
            "Daten werden dem von dir autorisierten Assistenten bereitgestellt.</p>"
            "<p>Du kannst Verbindungen widerrufen oder dein Konto löschen. Die Löschung "
            "deaktiviert den Zugang und entfernt aktive Zugangsdaten und Spielerdateien; "
            "Sicherheits- und Widerrufseinträge können verbleiben. Bereits vorhandene "
            "Sicherungskopien laufen zeitversetzt gemäß der Backup-Aufbewahrung ab. "
            "Daten, die ein verbundener Client bereits erhalten hat, werden durch den "
            "Widerruf hier nicht aus diesem Client gelöscht.</p>"
        )

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
                "<h1>MSF-Anmeldung</h1>"
                + form("/login", payload["csrf"], "<button>Bei MSF anmelden</button>"),
                form_origins="https://hydra-public.prod.m3.scopelypv.com",
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
        body = (
            "<h1>Verbindung erlauben</h1><p>"
            + escape(client_label(pending))
            + "</p><p>Verbindungskennung: "
            + escape(pending["client_id"])
            + "</p><ul>"
        )
        body += (
            "".join("<li>" + escape(PERMISSIONS[s]) + "</li>" for s in pending["scopes"]) + "</ul>"
        )
        action = "/consent?request_id=" + escape(request_id, quote=True)
        return page(
            body
            + form(action, payload["csrf"], "<button>Zugriff erlauben</button>")
            + '<p><a href="/account">Abbrechen</a></p>',
            form_origins="https://chatgpt.com https://claude.ai",
        )

    async def account(request):
        try:
            payload = await session(request)
            player = payload["player"]
        except (ValueError, KeyError):
            return redirect("/login")
        grants = await provider.list_grants(player)
        body = "<h1>Dein Konto</h1><h2>Verbindungen</h2>"
        for grant in grants:
            body += (
                "<p>"
                + escape(client_label(grant))
                + "</p><p>Verbindungskennung: "
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
                + '"><button>Verbindung widerrufen</button>',
            )
        body += "<h2>Konto löschen</h2><p>Entfernt deine aktiven Daten und alle Verbindungen.</p>"
        body += form(
            "/account/delete",
            payload["csrf"],
            '<label><input type="checkbox" name="confirm" value="delete" required>'
            " Konto endgültig löschen</label><button>Löschen bestätigen</button>",
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
            except Exception:
                # No upstream response bodies, request parameters or credentials in HTML.
                return page(
                    "<h1>Vorgang nicht möglich</h1><p>Die Anmeldung oder Sitzung ist "
                    "ungültig, abgelaufen oder der Dienst ist vorübergehend nicht "
                    "erreichbar. Bitte starte die Anmeldung erneut.</p>"
                    '<a href="/login">Neu anmelden</a>',
                    400,
                )

        return handle

    return [
        Route(path, safe(handler), methods=methods)
        for path, handler, methods in [
            ("/", home, ["GET"]),
            ("/privacy.html", privacy, ["GET"]),
            ("/login", login, ["GET", "POST"]),
            ("/oauth/callback", callback, ["GET"]),
            ("/consent", consent, ["GET", "POST"]),
            ("/account", account, ["GET"]),
            ("/account/revoke", revoke, ["POST"]),
            ("/account/delete", delete, ["POST"]),
        ]
    ]
