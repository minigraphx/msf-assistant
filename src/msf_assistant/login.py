"""Short-lived, loopback-only browser login for a personal MSF account."""

from __future__ import annotations

import math
import secrets
import socket
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from msf_assistant.auth import MSFOAuth2, TokenSet
from msf_assistant.config import Settings


class LoginError(RuntimeError):
    """A safe-to-display local login failure."""


@dataclass(frozen=True)
class CallbackAddress:
    host: str
    port: int
    path: str

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"


def callback_address(uri: str) -> CallbackAddress:
    """Accept only explicit local HTTP callback addresses, never public binds."""
    try:
        parsed = urlparse(uri)
        port = parsed.port
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1"}
            and parsed.username is None
            and parsed.password is None
            and port is not None
            and port > 0
            and parsed.path.startswith("/")
            and parsed.path not in {"/", "/login", "/privacy.html"}
            and not parsed.query
            and not parsed.fragment
            and not parsed.params
        )
    except ValueError:
        valid = False
    if not valid:
        raise LoginError(
            "MSF_REDIRECT_URI muss eine lokale HTTP-Adresse mit Port und Callback-Pfad sein, "
            "zum Beispiel http://localhost:8000/oauth/callback."
        )
    return CallbackAddress(parsed.hostname, port, parsed.path)


def _page(title: str, content: str) -> str:
    # Only fixed application text is passed here; never remote errors or tokens.
    return (
        '<!doctype html><html lang="de"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title} · MSF Assistant</title><style>"
        "body{font:17px/1.6 system-ui,sans-serif;background:#101722;color:#e7edf7;"
        "max-width:680px;margin:10vh auto;padding:24px}h1{line-height:1.2}"
        "a{color:#92d0ff}.button{display:inline-block;padding:12px 22px;"
        "border-radius:8px;background:#ffd45a;color:#141a23;text-decoration:none;"
        "font-weight:700}small{color:#aab9ce}code{overflow-wrap:anywhere}"
        f"</style><main><small>MSF · PERSÖNLICHER ASSISTENT</small><h1>{title}</h1>"
        f"{content}</main></html>"
    )


PRIVACY = _page(
    "Datenschutz im lokalen Testbetrieb",
    """
<p>Diese lokale Anwendung dient dem Zugriff auf den eigenen Marvel-Strike-Force-Account.</p>
<p>Bei der Anmeldung kommuniziert dein Browser mit MSF/Scopely. Die Anwendung
tauscht den Freigabecode gegen OAuth-Tokens und ruft Profil, Roster und Inventar
über die MSF-API ab. Dein MSF-Passwort wird ausschließlich bei MSF eingegeben.</p>
<p>ID und Client-Secret liegen in deiner lokalen <code>.env</code>.
OAuth-Tokens werden im macOS-Schlüsselbund gespeichert. Bei
<code>login --no-save</code> bleiben sie nur für den aktuellen Lauf im Arbeitsspeicher.
Profil-, Roster- und Inventardaten werden als lokale JSON-Datei im Ordner
<code>outputs</code> gespeichert; dieser Ordner und die Zugangsdaten sind im
Repository von Git ausgeschlossen.</p>
<p>Die Anwendung überträgt die abgerufenen Daten derzeit nicht an ChatGPT oder
andere Dienste. Eine spätere Anbindung ist hier noch nicht aktiv.</p>
<p>Mit <code>msf-assistant logout</code> entfernst du die gespeicherten Tokens.
Ausgabedateien und <code>.env</code> kannst du separat lokal löschen. Logout
widerruft nicht die Freigabe bei MSF; diese verwaltest du in deinem MSF-Account.</p>
<p>Diese Seite ist nur auf diesem Rechner und während des Login-Vorgangs verfügbar.</p>
<p><a href="/">Zur Anmeldung</a></p>
""",
)


@dataclass
class Reply:
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class LoginFlow:
    """One browser-bound OAuth attempt; upstream errors are never rendered raw."""

    def __init__(
        self,
        settings: Settings,
        *,
        oauth: MSFOAuth2 | None = None,
        timeout: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise LoginError("Die Anmeldefrist muss eine positive, endliche Sekundenzahl sein.")
        self.address = callback_address(settings.redirect_uri)
        self.oauth = oauth or MSFOAuth2(settings)
        self.clock = clock
        self.deadline = clock() + timeout
        self._state = secrets.token_urlsafe(32)
        self._cookie = secrets.token_urlsafe(32)
        self._attempted = False
        self.tokens: TokenSet | None = None
        self.error: LoginError | None = None

    @property
    def done(self) -> bool:
        return self.tokens is not None or self.error is not None

    def expire(self) -> bool:
        if not self.done and self.clock() >= self.deadline:
            self.error = LoginError("Die Anmeldung ist abgelaufen. Bitte login erneut starten.")
        return self.error is not None

    def handle(self, target: str, host: str, cookie_header: str) -> Reply:
        if host.lower() != self.address.authority:
            return Reply(403, _page("Anfrage abgelehnt", "<p>Ungültiger lokaler Host.</p>"))
        parsed = urlparse(target)
        if len(target) > 8192 or parsed.scheme or parsed.netloc or parsed.fragment or parsed.params:
            return Reply(
                400, _page("Ungültige Anfrage", "<p>Bitte die lokale Startseite öffnen.</p>")
            )
        if parsed.path == "/privacy.html":
            return Reply(200, PRIVACY)
        if self.done or self._attempted:
            return Reply(
                409,
                _page("Anmeldung bereits verarbeitet", "<p>Du kannst das Fenster schließen.</p>"),
            )
        if self.expire():
            return Reply(410, _page("Anmeldung abgelaufen", "<p>Bitte login erneut starten.</p>"))
        if parsed.path == "/":
            return Reply(
                200,
                _page(
                    "Verbinde deinen MSF-Account",
                    """
<p>Melde dich bei MSF an und erlaube den lesenden Zugriff auf Profil, Roster und
Inventar. Anschließend werden die Daten lokal auf diesem Rechner gespeichert.</p>
<p><a class="button" href="/login">Mit MSF anmelden</a></p>
<p><a href="/privacy.html">Datenschutz im lokalen Testbetrieb</a></p>
<small>Die Anmeldung läuft nach wenigen Minuten ab.</small>
""",
                ),
            )
        if parsed.path == "/login":
            url, _ = self.oauth.authorization_url(state=self._state)
            return Reply(
                302,
                headers={
                    "Location": url,
                    "Set-Cookie": f"msf_login={self._cookie}; Path=/; HttpOnly; SameSite=Lax",
                },
            )
        if parsed.path != self.address.path:
            return Reply(404, _page("Seite nicht gefunden", '<p><a href="/">Zur Anmeldung</a></p>'))
        params = parse_qs(parsed.query, keep_blank_values=True)
        try:
            cookies = SimpleCookie(cookie_header)
            cookie = cookies["msf_login"].value
        except (CookieError, KeyError):
            cookie = ""
        state = params.get("state", [""])
        valid = (
            len(state) == 1
            and secrets.compare_digest(state[0].encode(), self._state.encode())
            and secrets.compare_digest(cookie.encode(), self._cookie.encode())
            and all(len(values) == 1 for values in params.values())
            and bool(params.get("code", [""])[0] or params.get("error", [""])[0])
            and not ("code" in params and "error" in params)
        )
        if not valid:
            return Reply(
                400,
                _page(
                    "Anmeldung nicht zugeordnet",
                    """
<p>Die Rückmeldung passt nicht zu diesem Anmeldeversuch. Starte über
<a href="/">die lokale Anmeldeseite</a> und verwende denselben Browser.</p>
""",
                ),
            )
        self._attempted = True
        if "error" in params:
            self.error = LoginError("Die Freigabe wurde bei MSF abgebrochen oder abgelehnt.")
            return Reply(400, _page("Anmeldung abgebrochen", "<p>Bitte login erneut starten.</p>"))
        try:
            self.tokens = self.oauth.exchange_code(params["code"][0])
        except (requests.RequestException, ValueError):
            self.error = LoginError(
                "Der Token-Austausch mit MSF ist fehlgeschlagen. Zugangsdaten prüfen."
            )
            return Reply(
                502,
                _page(
                    "Anmeldung fehlgeschlagen",
                    "<p>Bitte die Zugangsdaten prüfen und login erneut starten.</p>",
                ),
            )
        return Reply(
            200,
            _page(
                "Bei MSF angemeldet",
                """
<p>Die Freigabe wurde übernommen. Die Anwendung verarbeitet jetzt die Anmeldung
und ruft deine Daten ab. Das Ergebnis erscheint im Terminal bzw. in Codex.</p>
<p>Du kannst dieses Fenster schließen.</p>
""",
            ),
            {"Set-Cookie": "msf_login=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"},
        )


class _Handler(BaseHTTPRequestHandler):
    server: LoginHTTPServer

    def do_GET(self) -> None:
        hosts = self.headers.get_all("Host", [])
        response = self.server.flow.handle(
            self.path, hosts[0] if len(hosts) == 1 else "", self.headers.get("Cookie", "")
        )
        body = response.body.encode("utf-8")
        self.send_response(response.status)
        for name, value in {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            **response.headers,
        }.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # OAuth callback URLs contain one-use credentials. Never log requests.
        return


class LoginHTTPServer(HTTPServer):
    def __init__(self, address: tuple[str, int], flow: LoginFlow) -> None:
        self.flow = flow
        super().__init__(address, _Handler)
        self.timeout = 0.25

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def handle_error(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        # Suppress traceback/request dumps that could include authorization data.
        self.flow.error = LoginError("Die lokale Anmeldung konnte nicht verarbeitet werden.")


def browser_login(
    settings: Settings,
    *,
    open_browser: bool = True,
    timeout: float = 300,
    on_ready: Callable[[str], None] | None = None,
) -> TokenSet:
    """Wait for one browser login, then release the local listening socket."""
    flow = LoginFlow(settings, timeout=timeout)
    try:
        with LoginHTTPServer(("127.0.0.1", flow.address.port), flow) as server:
            url = flow.address.origin + "/"
            if on_ready:
                on_ready(url)
            if open_browser:
                webbrowser.open(url)
            while not flow.done:
                flow.expire()
                if not flow.done:
                    server.handle_request()
    except OSError:
        raise LoginError(
            "Der lokale Login-Port ist nicht verfügbar. Andere Server beenden."
        ) from None
    finally:
        flow.oauth.session.close()
    if flow.error:
        raise flow.error
    if flow.tokens is None:
        raise LoginError("Die Anmeldung lieferte keine Tokens.")
    return flow.tokens
