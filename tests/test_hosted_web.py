import json
import re
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.testclient import TestClient

from msf_assistant.config import Settings
from msf_assistant.hosted_identity import MSFIdentity
from msf_assistant.hosted_oauth import HostedOAuthProvider
from msf_assistant.hosted_store import HostedStore
from msf_assistant.hosted_web import account_routes

ORIGIN = "https://assistant.example"


@pytest.fixture
def setup(tmp_path):
    store = HostedStore(tmp_path / "store", Fernet.generate_key())
    provider = HostedOAuthProvider(store, ORIGIN)
    identity = MSFIdentity(Settings(client_id="app", client_secret="secret"))
    identity.oauth.session = Mock()
    for method in (identity.oauth.session.get, identity.oauth.session.post):
        response = method.return_value
        response.iter_content.side_effect = lambda chunk_size, r=response: [
            json.dumps(r.json.return_value).encode()
        ]
    identity.oauth.session.get.return_value.status_code = 200
    identity.oauth.session.post.return_value.json.return_value = {"access_token": "private-token"}
    identity.oauth.session.get.return_value.json.return_value = {"sub": "alice"}
    app = Starlette(routes=account_routes(store, provider, identity, ORIGIN))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as browser:
        yield store, provider, identity, browser


def csrf(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]


def start(browser):
    page = browser.get("/login")
    response = browser.post("/login", data={"csrf": csrf(page)}, headers={"Origin": ORIGIN})
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def login(browser):
    state = start(browser)
    return browser.get("/oauth/callback", params={"state": state, "code": "code"})


def test_wrong_replayed_state_and_rotation(setup):
    store, provider, identity, browser = setup
    state = start(browser)
    old_cookie = browser.cookies.get("__Host-msf_session")
    assert browser.get("/oauth/callback?state=wrong&code=c").status_code == 400
    assert browser.get("/oauth/callback", params={"state": state, "code": "c"}).status_code == 303
    assert browser.cookies.get("__Host-msf_session") != old_cookie
    assert browser.get("/oauth/callback", params={"state": state, "code": "c"}).status_code == 400
    assert "private-token" not in str(browser.cookies)
    browser.cookies.set("__Host-msf_session", old_cookie)
    assert browser.get("/account").status_code == 303


def test_cookie_mismatch_and_safe_error(setup):
    store, provider, identity, browser = setup
    state = start(browser)
    browser.cookies.clear()
    response = browser.get("/oauth/callback", params={"state": state, "code": "<script>"})
    assert response.status_code == 400
    assert "<script>" not in response.text
    identity.oauth.session.post.assert_not_called()


def test_forged_posts_and_deletion(setup):
    store, provider, identity, browser = setup
    assert login(browser).status_code == 303
    page = browser.get("/account")
    token = csrf(page)
    for headers, data in [
        ({}, {"csrf": token}),
        ({"Origin": "https://evil.example"}, {"csrf": token}),
        ({"Origin": ORIGIN}, {"csrf": "bad"}),
    ]:
        assert browser.post("/account/delete", headers=headers, data=data).status_code == 400
    assert browser.get("/account/delete").status_code == 405
    assert (
        browser.post(
            "/account/delete", headers={"Origin": ORIGIN}, data={"csrf": token}
        ).status_code
        == 400
    )
    assert (
        browser.post(
            "/account/delete", headers={"Origin": ORIGIN}, data={"csrf": token, "confirm": "delete"}
        ).status_code
        == 303
    )
    assert browser.get("/account").status_code == 303


def test_upstream_failure_is_safe(setup):
    store, provider, identity, browser = setup
    state = start(browser)
    identity.oauth.session.post.side_effect = ValueError("secret upstream content")
    response = browser.get("/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 400
    assert "secret upstream content" not in response.text
    assert browser.get("/privacy.html").status_code == 200


def test_two_players_two_clients_and_html_escaping(setup):
    import asyncio

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    store, provider, identity, browser = setup

    def connect(client_id, subject):
        app = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=["https://chatgpt.com/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code"],
            response_types=["code"],
            scope="msf:read",
        )
        asyncio.run(provider.register_client(app))
        target = asyncio.run(
            provider.authorize(
                app,
                AuthorizationParams(
                    state="downstream",
                    scopes=["msf:read"],
                    code_challenge="x" * 43,
                    redirect_uri="https://chatgpt.com/callback",
                    redirect_uri_provided_explicitly=True,
                    resource=provider.resource,
                ),
            )
        )
        page = browser.get(target)
        response = browser.post("/login", data={"csrf": csrf(page)}, headers={"Origin": ORIGIN})
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        identity.oauth.session.get.return_value.json.return_value = {"sub": subject}
        response = browser.get("/oauth/callback", params={"state": state, "code": "code"})
        target = response.headers["location"]
        page = browser.get(target)
        assert page.status_code == 200
        assert "<script>" not in page.text
        assert "Eigene Spieldaten" in page.text
        assert (
            "form-action 'self' https://chatgpt.com https://claude.ai;"
            in (page.headers["content-security-policy"])
        )
        assert "hydra-public" not in page.headers["content-security-policy"]
        assert "downstream" not in page.text
        assert (
            browser.post(target, data={"csrf": "wrong"}, headers={"Origin": ORIGIN}).status_code
            == 400
        )
        approved = browser.post(target, data={"csrf": csrf(page)}, headers={"Origin": ORIGIN})
        assert approved.status_code == 303
        assert approved.headers["location"].startswith("https://chatgpt.com/callback?")
        assert browser.get(target).status_code == 400
        return target

    connect("alice<script>", "alice")
    connect("alice-second", "alice")
    assert "alice&lt;script&gt;" in browser.get("/account").text
    assert "alice-second" in browser.get("/account").text
    connect("bob-only", "bob")
    page = browser.get("/account")
    assert "bob-only" in page.text
    assert "alice-second" not in page.text
    alice = store.player("https://hydra-public.prod.m3.scopelypv.com/", "alice")
    grants = asyncio.run(provider.list_grants(alice.id))
    assert len(grants) == 2
    response = browser.post(
        "/account/revoke",
        data={"csrf": csrf(page), "grant": grants[0]["id"]},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 303
    assert len(asyncio.run(provider.list_grants(alice.id))) == 2
    own_grant = re.search(r'name="grant" value="([^"]+)"', page.text)[1]
    browser.post(
        "/account/revoke", data={"csrf": csrf(page), "grant": own_grant}, headers={"Origin": ORIGIN}
    )
    assert "bob-only" not in browser.get("/account").text


def test_delete_waits_for_refresh_and_prevents_resurrection(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from msf_assistant.auth import TokenSet
    from msf_assistant.hosted_store import HostedStoreError

    store, provider, identity, browser = setup
    login(browser)
    token = csrf(browser.get("/account"))
    player = store.player("https://hydra-public.prod.m3.scopelypv.com/", "alice")
    path = store.player_dir(player.id)
    entered, release = Event(), Event()

    def refresh():
        with store.player_lock(player.id):
            entered.set()
            assert release.wait(5)
            store.save_tokens(player.id, TokenSet("new-token"))
            (path / "data").write_text("data")

    with ThreadPoolExecutor(max_workers=2) as pool:
        refreshing = pool.submit(refresh)
        assert entered.wait(5)
        deleting = pool.submit(
            browser.post,
            "/account/delete",
            data={"csrf": token, "confirm": "delete"},
            headers={"Origin": ORIGIN},
        )
        release.set()
        refreshing.result(timeout=5)
        assert deleting.result(timeout=5).status_code == 303
    assert not path.exists()
    with pytest.raises(HostedStoreError), store.player_lock(player.id):
        store.save_tokens(player.id, TokenSet("resurrect"))


def test_session_secrets_hashed_and_cookie_flags(setup):
    store, provider, identity, browser = setup
    response = browser.get("/login")
    raw = browser.cookies.get("__Host-msf_session")
    assert all(
        flag in response.headers["set-cookie"] for flag in ["Secure", "HttpOnly", "SameSite=lax"]
    )
    assert raw.encode() not in store.database_path.read_bytes()
    state = start(browser)
    assert state.encode() not in store.database_path.read_bytes()


def test_expired_state_and_session(setup, monkeypatch):
    from msf_assistant import hosted_web

    store, provider, identity, browser = setup
    state = start(browser)
    now = hosted_web.time.time()
    monkeypatch.setattr(hosted_web.time, "time", lambda: now + hosted_web.LOGIN_TTL + 1)
    assert browser.get("/oauth/callback", params={"state": state, "code": "c"}).status_code == 400
    identity.oauth.session.post.assert_not_called()


def test_second_login_invalidates_first_browser_binding(setup):
    store, provider, identity, browser = setup
    state = start(browser)
    new_state = start(browser)
    assert browser.get("/oauth/callback", params={"state": state, "code": "c"}).status_code == 400
    assert (
        browser.get("/oauth/callback", params={"state": new_state, "code": "c"}).status_code == 303
    )
    assert browser.get("/consent?request_id=foreign").status_code == 400


def test_login_policy_allows_fixed_msf_redirect(setup):
    store, provider, identity, browser = setup
    policy = browser.get("/login").headers["content-security-policy"]
    assert "form-action 'self' https://hydra-public.prod.m3.scopelypv.com" in policy
    assert "https://chatgpt.com" not in policy
    assert "form-action 'self';" in browser.get("/").headers["content-security-policy"]


def test_chunked_form_is_bounded(setup):
    store, provider, identity, browser = setup
    token = csrf(browser.get("/login"))
    response = browser.post(
        "/login",
        content=iter([("csrf=" + token + "&large=" + "x" * 9000).encode()]),
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400


def test_selected_callback_identifies_each_opaque_client_grant(setup):
    import asyncio

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    store, provider, identity, browser = setup
    app = OAuthClientInformationFull(
        client_id="opaque-8ac3",
        client_name="Misleading Other Service",
        redirect_uris=["https://chatgpt.com/callback", "https://claude.ai/callback"],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code"],
        response_types=["code"],
        scope="msf:read msf:write",
    )
    asyncio.run(provider.register_client(app))
    second = app.model_copy(update={"client_id": "opaque-92fb", "client_name": "Claude"})
    asyncio.run(provider.register_client(second))
    for selected, origin, label in [
        (app, "https://claude.ai", "Claude (claude.ai)"),
        (app, "https://chatgpt.com", "ChatGPT (chatgpt.com)"),
        (second, "https://chatgpt.com", "ChatGPT (chatgpt.com)"),
    ]:
        target = asyncio.run(
            provider.authorize(
                selected,
                AuthorizationParams(
                    state="state",
                    scopes=["msf:read", "msf:write"],
                    code_challenge="x" * 43,
                    redirect_uri=origin + "/callback",
                    redirect_uri_provided_explicitly=True,
                    resource=provider.resource,
                ),
            )
        )
        page = browser.get(target)
        response = browser.post("/login", data={"csrf": csrf(page)}, headers={"Origin": ORIGIN})
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        response = browser.get("/oauth/callback", params={"state": state, "code": "code"})
        target = response.headers["location"]
        page = browser.get(target)
        assert label in page.text
        assert "Misleading Other Service" not in page.text
        assert (
            browser.post(target, data={"csrf": csrf(page)}, headers={"Origin": ORIGIN}).status_code
            == 303
        )
    page = browser.get("/account")
    assert "Claude (claude.ai)" in page.text and "ChatGPT (chatgpt.com)" in page.text
    assert "msf:read" not in page.text and "msf:write" not in page.text
    assert "Eigene Spieldaten und Kontext lesen" in page.text
    assert "Eigenen Spielkontext ändern und Daten aktualisieren" in page.text
    alice = store.player("https://hydra-public.prod.m3.scopelypv.com/", "alice")
    grants = asyncio.run(provider.list_grants(alice.id))
    claude = next(g for g in grants if g["callback_origin"] == "https://claude.ai")
    assert re.search(r'Claude \(claude.ai\).*?name="grant" value="' + claude["id"] + '"', page.text)
    browser.post(
        "/account/revoke",
        data={"csrf": csrf(page), "grant": claude["id"]},
        headers={"Origin": ORIGIN},
    )
    page = browser.get("/account")
    assert "Claude (claude.ai)" not in page.text
    assert "ChatGPT (chatgpt.com)" in page.text

    with store.transaction() as db:
        db.execute("UPDATE oauth_grants SET callback_origin=NULL WHERE player=?", (alice.id,))
    page = browser.get("/account")
    assert "Unbekannte Verbindung (ältere Freigabe)" in page.text
    assert "ChatGPT (chatgpt.com)" not in page.text


def test_home_explains_configured_connector_url_and_clients(setup):
    _, _, _, browser = setup
    text = browser.get("/").text
    assert ORIGIN + "/mcp" in text
    assert "ChatGPT" in text and "Claude" in text
    assert "developers.openai.com/plugins/deploy/connect-chatgpt" in text
    assert "claude.com/docs/connectors/custom/remote-mcp" in text
    assert 'href="/privacy.html"' in text
