from http.client import HTTPConnection
from http.cookies import SimpleCookie
from threading import Thread
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import requests

from msf_assistant import login as login_module
from msf_assistant.auth import TokenSet
from msf_assistant.config import Settings
from msf_assistant.login import LoginError, LoginFlow, LoginHTTPServer, callback_address


@pytest.fixture
def flow():
    oauth = Mock()
    oauth.authorization_url.side_effect = lambda **kw: (
        "https://msf.example/authorize?" + urlencode({"state": kw["state"]}),
        kw["state"],
    )
    oauth.exchange_code.return_value = TokenSet("test-access", refresh_token="test-refresh")
    return LoginFlow(Settings("test-client", client_secret="test-secret"), oauth=oauth)


def start_login(flow):
    response = flow.handle("/login", "localhost:8000", "")
    assert response.status == 302
    state = parse_qs(urlparse(response.headers["Location"]).query)["state"][0]
    cookie = SimpleCookie(response.headers["Set-Cookie"])
    return state, cookie.output(header="", sep=";").strip()


def test_callback_success_is_one_use_and_never_exposes_tokens(flow):
    state, cookie = start_login(flow)
    target = "/oauth/callback?" + urlencode({"code": "private-code", "state": state})

    response = flow.handle(target, "localhost:8000", cookie)

    assert response.status == 200
    assert flow.tokens.access_token == "test-access"
    assert flow.done
    flow.oauth.exchange_code.assert_called_once_with("private-code")
    assert "test-access" not in response.body
    assert "private-code" not in response.body
    assert flow.handle(target, "localhost:8000", cookie).status == 409
    flow.oauth.exchange_code.assert_called_once()


@pytest.mark.parametrize("change", ["state", "cookie", "duplicate", "missing", "unicode"])
def test_invalid_callback_does_not_exchange_or_consume(flow, change):
    state, cookie = start_login(flow)
    query = {"code": "private-code", "state": state}
    if change == "state":
        query["state"] = "wrong"
    elif change == "unicode":
        query["state"] = "☃"
    elif change == "cookie":
        cookie = ""
    elif change == "missing":
        del query["code"]
    target = "/oauth/callback?" + urlencode(query)
    if change == "duplicate":
        target += "&code=another"

    assert flow.handle(target, "localhost:8000", cookie).status == 400
    assert not flow.done
    flow.oauth.exchange_code.assert_not_called()


def test_provider_denial_is_sanitized(flow):
    state, cookie = start_login(flow)
    target = "/oauth/callback?" + urlencode(
        {"state": state, "error": "access_denied", "error_description": "<secret-value>"}
    )
    response = flow.handle(target, "localhost:8000", cookie)
    assert response.status == 400
    assert flow.done
    assert isinstance(flow.error, LoginError)
    assert "secret-value" not in response.body
    assert "secret-value" not in str(flow.error)
    flow.oauth.exchange_code.assert_not_called()


def test_token_exchange_error_is_sanitized(flow):
    state, cookie = start_login(flow)
    flow.oauth.exchange_code.side_effect = requests.HTTPError("secret-from-upstream")
    response = flow.handle(
        "/oauth/callback?" + urlencode({"code": "code", "state": state}),
        "localhost:8000",
        cookie,
    )
    assert response.status == 502
    assert flow.done
    assert "secret-from-upstream" not in response.body
    assert "secret-from-upstream" not in str(flow.error)


def test_expiry_prevents_exchange(flow):
    state, cookie = start_login(flow)
    flow.clock = lambda: flow.deadline + 1
    response = flow.handle(
        "/oauth/callback?" + urlencode({"code": "code", "state": state}),
        "localhost:8000",
        cookie,
    )
    assert response.status == 410
    assert flow.done
    flow.oauth.exchange_code.assert_not_called()


@pytest.mark.parametrize("host", ["evil.example", "localhost:9000", "localhost:8000.evil"])
def test_wrong_host_is_rejected(flow, host):
    assert flow.handle("/", host, "").status == 403


def test_pages_and_cookie_headers(flow):
    response = flow.handle("/", "localhost:8000", "")
    assert response.status == 200
    assert "/login" in response.body
    privacy = flow.handle("/privacy.html", "localhost:8000", "")
    assert privacy.status == 200
    assert "Schlüsselbund" in privacy.body
    assert flow.handle("/.env", "localhost:8000", "").status == 404
    assert flow.handle("http://evil.example/login", "localhost:8000", "").status == 400
    cookie = flow.handle("/login", "localhost:8000", "").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


@pytest.mark.parametrize(
    "uri",
    [
        "http://0.0.0.0:8000/oauth/callback",
        "https://localhost:8000/oauth/callback",
        "http://example.com/oauth/callback",
        "http://user@localhost:8000/oauth/callback",
        "http://localhost:8000/oauth/callback?state=bad",
        "http://localhost:8000/login",
        "http://localhost:8000/",
        "http://localhost:8000/privacy.html",
        "http://localhost:8000/oauth/callback#fragment",
    ],
)
def test_nonlocal_or_ambiguous_redirect_is_rejected(uri):
    with pytest.raises(LoginError):
        callback_address(uri)


def test_real_http_response_headers_and_silent_logs(flow, capsys):
    with LoginHTTPServer(("127.0.0.1", 0), flow) as server:
        worker = Thread(target=server.handle_request)
        worker.start()
        connection = HTTPConnection(*server.server_address, timeout=2)
        connection.request(
            "GET", "/privacy.html?code=do-not-log", headers={"Host": "localhost:8000"}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Referrer-Policy") == "no-referrer"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert "Schlüsselbund" in response.read().decode()
        connection.close()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert "do-not-log" not in capsys.readouterr().err


def test_token_repr_does_not_contain_secrets():
    token = TokenSet("private-access", refresh_token="private-refresh")
    assert "private-access" not in repr(token)
    assert "private-refresh" not in repr(token)


def test_browser_login_handles_real_callback_and_closes_server(monkeypatch, flow):
    servers = []
    workers = []
    statuses = []

    def make_server(address, active_flow):
        server = LoginHTTPServer(("127.0.0.1", 0), active_flow)
        servers.append(server)
        return server

    def browser_requests():
        connection = HTTPConnection(*servers[0].server_address, timeout=2)
        connection.request("GET", "/login", headers={"Host": "localhost:8000"})
        response = connection.getresponse()
        statuses.append(response.status)
        state = parse_qs(urlparse(response.getheader("Location")).query)["state"][0]
        cookie = response.getheader("Set-Cookie")
        response.read()
        connection.close()
        connection = HTTPConnection(*servers[0].server_address, timeout=2)
        connection.request(
            "GET",
            "/oauth/callback?" + urlencode({"state": state, "code": "code"}),
            headers={"Host": "localhost:8000", "Cookie": cookie},
        )
        response = connection.getresponse()
        statuses.append(response.status)
        response.read()
        connection.close()

    def on_ready(url):
        assert url == "http://localhost:8000/"
        worker = Thread(target=browser_requests)
        workers.append(worker)
        worker.start()

    monkeypatch.setattr(login_module, "LoginHTTPServer", make_server)
    monkeypatch.setattr(login_module, "MSFOAuth2", Mock(return_value=flow.oauth))
    token = login_module.browser_login(
        Settings("test-client", client_secret="test-secret"),
        open_browser=False,
        timeout=2,
        on_ready=on_ready,
    )
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert token.access_token == "test-access"
    assert statuses == [302, 200]
    assert servers[0].socket.fileno() == -1
    flow.oauth.session.close.assert_called_once()


def test_browser_login_timeout_releases_port(monkeypatch, flow):
    servers = []

    def make_server(address, active_flow):
        server = LoginHTTPServer(("127.0.0.1", 0), active_flow)
        servers.append(server)
        return server

    monkeypatch.setattr(login_module, "LoginHTTPServer", make_server)
    monkeypatch.setattr(login_module, "MSFOAuth2", Mock(return_value=flow.oauth))
    with pytest.raises(LoginError, match="abgelaufen"):
        login_module.browser_login(
            Settings("test-client", client_secret="test-secret"), open_browser=False, timeout=0.01
        )
    assert servers[0].socket.fileno() == -1
    flow.oauth.session.close.assert_called_once()
