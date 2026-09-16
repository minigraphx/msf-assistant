import asyncio
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull

from msf_assistant import hosted_oauth
from msf_assistant.hosted_store import HostedStore, HostedStoreError


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [2000000000]
    monkeypatch.setattr(hosted_oauth.time, "time", lambda: now[0])
    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "private", key)
    provider = hosted_oauth.HostedOAuthProvider(store, "https://msf.example")
    return provider, store, key, now


def client(identifier="chat", **kwargs):
    values = dict(
        client_id=identifier,
        redirect_uris=["https://chatgpt.com/connector/callback"],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="msf:read msf:write offline_access",
    )
    values.update(kwargs)
    return OAuthClientInformationFull(**values)


def params(**kwargs):
    values = dict(
        state="private-state",
        scopes=["msf:read", "offline_access"],
        code_challenge=base64.urlsafe_b64encode(hashlib.sha256(b"x" * 43).digest())
        .decode()
        .rstrip("="),
        redirect_uri="https://chatgpt.com/connector/callback",
        redirect_uri_provided_explicitly=True,
        resource="https://msf.example/mcp",
    )
    values.update(kwargs)
    return AuthorizationParams(**values)


def code_for(provider, store, subject="alice", app=None):
    app = app or client()
    if run(provider.get_client(app.client_id)) is None:
        run(provider.register_client(app))
    player = store.player("issuer", subject)
    login = run(provider.authorize(app, params()))
    request_id = parse_qs(urlparse(login).query)["request_id"][0]
    assert run(provider.complete_login(request_id, player.id)).startswith(
        "https://msf.example/consent?"
    )
    callback = run(provider.approve(request_id, player.id))
    query = parse_qs(urlparse(callback).query)
    assert query["state"] == ["private-state"]
    return player, query["code"][0], app


def tokens_for(provider, store, subject="alice", app=None):
    player, raw, app = code_for(provider, store, subject, app)
    code = run(provider.load_authorization_code(app, raw))
    return player, run(provider.exchange_authorization_code(app, code)), app


def test_encryption_helper_binds_context(env):
    _, store, _, _ = env
    encrypted = store.encrypt_private("oauth:request:one", {"state": "private"})
    assert b"private" not in encrypted
    assert store.decrypt_private("oauth:request:one", encrypted) == {"state": "private"}
    with pytest.raises(HostedStoreError):
        store.decrypt_private("oauth:request:two", encrypted)
    with pytest.raises(HostedStoreError):
        store.decrypt_private("oauth:request:one", b"tampered")


def test_tokens_are_bound_private_and_durable(env):
    provider, store, key, now = env
    alice, tokens, app = tokens_for(provider, store)
    assert tokens.expires_in == 900
    reopened = hosted_oauth.HostedOAuthProvider(HostedStore(store.root, key), "https://msf.example")
    access = run(reopened.load_access_token(tokens.access_token))
    assert (access.subject, access.resource, access.client_id) == (
        alice.id,
        "https://msf.example/mcp",
        app.client_id,
    )
    assert access.expires_at == now[0] + 900
    assert run(reopened.load_access_token("invented")) is None
    for secret in [tokens.access_token, tokens.refresh_token, "private-state"]:
        assert secret.encode() not in store.database_path.read_bytes()
    now[0] += 900
    assert run(provider.load_access_token(tokens.access_token)) is None


@pytest.mark.parametrize(
    "values",
    [
        {"redirect_uris": ["http://chatgpt.com/callback"]},
        {"redirect_uris": ["https://chatgpt.com.evil/callback"]},
        {"redirect_uris": ["https://user@chatgpt.com/callback"]},
        {"redirect_uris": ["https://chatgpt.com/callback#fragment"]},
        {"redirect_uris": ["https://chatgpt.com/*"]},
        {"redirect_uris": ["https://chatgpt.com:8443/callback"]},
        {"grant_types": ["client_credentials"]},
        {"token_endpoint_auth_method": "client_secret_post"},
        {"scope": "msf:read admin"},
        {"response_types": ["token"]},
    ],
)
def test_registration_rejects_unapproved_metadata(env, values):
    with pytest.raises(RegistrationError):
        run(env[0].register_client(client(**values)))


@pytest.mark.parametrize(
    "values",
    [
        {"resource": None},
        {"resource": "https://other.example/mcp"},
        {"scopes": ["msf:write"]},
        {"scopes": ["msf:read", "admin"]},
        {"redirect_uri": "https://chatgpt.com/other"},
        {"code_challenge": ""},
    ],
)
def test_authorize_requires_exact_callback_resource_scopes(env, values):
    provider = env[0]
    app = client()
    run(provider.register_client(app))
    with pytest.raises(AuthorizeError):
        run(provider.authorize(app, params(**values)))


def test_code_expiry_replay_and_wrong_client(env):
    provider, store, _, now = env
    _, raw, app = code_for(provider, store)
    assert run(provider.load_authorization_code(client("other"), raw)) is None
    code = run(provider.load_authorization_code(app, raw))
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(client("other"), code))
    run(provider.exchange_authorization_code(app, code))
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(app, code))
    _, raw, app = code_for(provider, store)
    code = run(provider.load_authorization_code(app, raw))
    now[0] += 60
    assert run(provider.load_authorization_code(app, raw)) is None
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(app, code))


def test_separate_grants_replay_and_revocation(env):
    provider, store, key, _ = env
    alice, first, app = tokens_for(provider, store)
    _, second, _ = tokens_for(provider, store)
    bob, third, _ = tokens_for(provider, store, "bob")
    assert len(run(provider.list_grants(alice.id))) == 2
    refresh = run(provider.load_refresh_token(app, first.refresh_token))
    rotated = run(provider.exchange_refresh_token(app, refresh, refresh.scopes))
    with pytest.raises(TokenError):
        run(provider.exchange_refresh_token(app, refresh, refresh.scopes))
    assert run(provider.load_access_token(rotated.access_token)) is None
    assert run(provider.load_access_token(second.access_token)) is not None
    run(provider.revoke_player(alice.id))
    reopened = hosted_oauth.HostedOAuthProvider(HostedStore(store.root, key), "https://msf.example")
    assert run(reopened.load_access_token(second.access_token)) is None
    assert run(reopened.load_access_token(third.access_token)).subject == bob.id


def test_wrong_client_and_scope_escalation_do_not_consume_refresh(env):
    provider, store, _, _ = env
    _, tokens, app = tokens_for(provider, store)
    refresh = run(provider.load_refresh_token(app, tokens.refresh_token))
    assert run(provider.load_refresh_token(client("other"), tokens.refresh_token)) is None
    with pytest.raises(TokenError):
        run(provider.exchange_refresh_token(client("other"), refresh, refresh.scopes))
    with pytest.raises(TokenError):
        run(provider.exchange_refresh_token(app, refresh, ["msf:read", "msf:write"]))
    assert run(provider.exchange_refresh_token(app, refresh, refresh.scopes)).access_token


@pytest.mark.parametrize("kind", ["code", "refresh"])
def test_concurrent_exchange_is_single_use(env, kind):
    provider, store, key, _ = env
    if kind == "code":
        _, raw, app = code_for(provider, store)
        credential = run(provider.load_authorization_code(app, raw))
    else:
        _, tokens, app = tokens_for(provider, store)
        credential = run(provider.load_refresh_token(app, tokens.refresh_token))

    def exchange(_):
        other = hosted_oauth.HostedOAuthProvider(
            HostedStore(store.root, key), "https://msf.example"
        )
        try:
            if kind == "code":
                return run(other.exchange_authorization_code(app, credential))
            return run(other.exchange_refresh_token(app, credential, credential.scopes))
        except TokenError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(exchange, range(2)))
    assert sum(result is not None for result in results) == 1


def test_deleted_players_and_invalidation(env):
    provider, store, _, _ = env
    alice, first, _ = tokens_for(provider, store)
    _, second, _ = tokens_for(provider, store, "bob")
    store.deactivate_player(alice.id)
    assert run(provider.load_access_token(first.access_token)) is None
    run(provider.invalidate_all())
    assert run(provider.load_access_token(second.access_token)) is None


def test_pending_player_binding_expiry_and_grant_ownership(env):
    provider, store, _, now = env
    alice, tokens, app = tokens_for(provider, store)
    bob = store.player("issuer", "bob")
    grant = run(provider.list_grants(alice.id))[0]
    run(provider.revoke_grant(bob.id, grant["id"]))
    assert run(provider.load_access_token(tokens.access_token)) is not None
    run(provider.revoke_grant(alice.id, grant["id"]))
    assert run(provider.load_access_token(tokens.access_token)) is None
    login = run(provider.authorize(app, params()))
    request_id = parse_qs(urlparse(login).query)["request_id"][0]
    run(provider.complete_login(request_id, alice.id))
    with pytest.raises(AuthorizeError):
        run(provider.approve(request_id, bob.id))
    now[0] += 600
    with pytest.raises(AuthorizeError):
        run(provider.approve(request_id, alice.id))


@pytest.mark.parametrize(
    "refresh_resource",
    [None, "https://evil.example/mcp", ["https://msf.example/mcp"] * 2],
    ids=["missing-refresh-resource", "wrong-refresh-resource", "duplicate-refresh-resource"],
)
def test_sdk_http_routes_public_metadata_token_resource_and_rotation(env, refresh_resource):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    provider, store, _, _ = env
    _, raw, app = code_for(provider, store)
    with TestClient(Starlette(routes=provider.auth_routes())) as http:
        metadata = http.get("/.well-known/oauth-authorization-server").json()
        assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["revocation_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        assert metadata["issuer"] == "https://msf.example"
        resource = http.get("/.well-known/oauth-protected-resource/mcp").json()
        assert resource["resource"] == "https://msf.example/mcp"
        data = dict(
            grant_type="authorization_code",
            code=raw,
            client_id=app.client_id,
            code_verifier="x" * 43,
            redirect_uri=str(app.redirect_uris[0]),
        )
        for resource in [None, "https://evil.example/mcp", ["https://msf.example/mcp"] * 2]:
            invalid = data | ({"resource": resource} if resource is not None else {})
            response = http.post("/token", data=invalid)
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_target"
            assert run(provider.load_authorization_code(app, raw)) is not None
        data["resource"] = "https://msf.example/mcp"
        response = http.post("/token", data=data)
        assert response.status_code == 200, response.text
        refresh = response.json()["refresh_token"]
        refresh_data = dict(
            grant_type="refresh_token",
            refresh_token=refresh,
            client_id=app.client_id,
        )
        if refresh_resource is not None:
            refresh_data["resource"] = refresh_resource
        rejected = http.post("/token", data=refresh_data)
        assert rejected.status_code == 400
        assert rejected.json()["error"] == "invalid_target"
        refresh_data["resource"] = "https://msf.example/mcp"
        assert http.post("/token", data=refresh_data).status_code == 200
        assert http.post("/token", data=refresh_data).status_code == 400


def test_capacity_expiration_active_client_and_stale_registration(env):
    provider, store, _, now = env
    provider.MAX_CLIENTS = 2
    run(provider.register_client(client("unused")))
    _, tokens, app = tokens_for(provider, store)
    run(provider.register_client(client("unused-two")))
    with pytest.raises(RegistrationError):
        run(provider.register_client(client("too-many")))
    provider.MAX_REQUESTS = 2
    run(provider.authorize(app, params()))
    run(provider.authorize(app, params()))
    with pytest.raises(AuthorizeError):
        run(provider.authorize(app, params()))
    now[0] += 600
    assert run(provider.authorize(app, params()))
    now[0] += 86400
    assert run(provider.get_client("unused")) is None
    assert run(provider.get_client(app.client_id)) is not None
    assert run(provider.load_refresh_token(app, tokens.refresh_token)) is not None
    run(provider.register_client(client("new")))
    with pytest.raises(AuthorizeError):
        run(provider.authorize(client("unused"), params()))


def test_database_wait_does_not_block_event_loop(env, monkeypatch):
    import threading
    import time as clock
    from contextlib import contextmanager

    provider, store, _, _ = env
    entered = threading.Event()
    release = threading.Event()
    original = store.transaction

    @contextmanager
    def slow_transaction():
        entered.set()
        assert release.wait(3)
        with original() as db:
            yield db

    monkeypatch.setattr(store, "transaction", slow_transaction)

    async def check():
        task = asyncio.create_task(provider.load_access_token("invented"))
        start = clock.monotonic()
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.02)
            assert clock.monotonic() - start < 1
        finally:
            release.set()
        assert await task is None

    run(check())


def test_sdk_registration_and_revocation_routes(env):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    provider, store, _, _ = env
    with TestClient(Starlette(routes=provider.auth_routes())) as http:
        registration = client().model_dump(mode="json", exclude_none=True)
        registration.pop("client_id")
        response = http.post("/register", json=registration)
        assert response.status_code == 201, response.text
        app = OAuthClientInformationFull.model_validate(response.json())
        assert app.client_secret is None
        assert app.token_endpoint_auth_method == "none"
        _, tokens, _ = tokens_for(provider, store, app=app)
        response = http.post(
            "/revoke", data={"client_id": app.client_id, "token": tokens.access_token}
        )
        assert response.status_code == 200
        assert run(provider.load_access_token(tokens.access_token)) is None


def test_refresh_expiry_scope_reduction_and_deleted_code(env):
    provider, store, _, now = env
    _, tokens, app = tokens_for(provider, store)
    refresh = run(provider.load_refresh_token(app, tokens.refresh_token))
    reduced = run(provider.exchange_refresh_token(app, refresh, ["msf:read"]))
    assert reduced.refresh_token is None
    assert run(provider.load_access_token(reduced.access_token)).scopes == ["msf:read"]
    player, raw, app = code_for(provider, store, "deleted")
    code = run(provider.load_authorization_code(app, raw))
    store.deactivate_player(player.id)
    assert run(provider.load_authorization_code(app, raw)) is None
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(app, code))
    _, tokens, app = tokens_for(provider, store)
    refresh = run(provider.load_refresh_token(app, tokens.refresh_token))
    now[0] += 30 * 86400
    assert run(provider.load_refresh_token(app, tokens.refresh_token)) is None
    with pytest.raises(TokenError):
        run(provider.exchange_refresh_token(app, refresh, refresh.scopes))


def test_browser_transitions_reject_unbound_rebinding_and_replay(env):
    provider, store, _, _ = env
    app = client()
    run(provider.register_client(app))
    alice = store.player("issuer", "alice")
    bob = store.player("issuer", "bob")
    request_id = parse_qs(urlparse(run(provider.authorize(app, params()))).query)["request_id"][0]
    with pytest.raises(AuthorizeError):
        run(provider.approve(request_id, alice.id))
    run(provider.complete_login(request_id, alice.id))
    with pytest.raises(AuthorizeError):
        run(provider.complete_login(request_id, bob.id))
    run(provider.approve(request_id, alice.id))
    with pytest.raises(AuthorizeError):
        run(provider.approve(request_id, alice.id))
    assert request_id.encode() not in store.database_path.read_bytes()


def test_cross_request_metadata_swap_is_rejected(env):
    provider, store, _, _ = env
    app = client()
    run(provider.register_client(app))
    alice = store.player("issuer", "alice")
    identifiers = [
        parse_qs(urlparse(run(provider.authorize(app, params()))).query)["request_id"][0]
        for _ in range(2)
    ]
    for identifier in identifiers:
        run(provider.complete_login(identifier, alice.id))
    with store.transaction() as db:
        rows = db.execute("SELECT hash, encrypted FROM oauth_requests").fetchall()
        db.execute("UPDATE oauth_requests SET encrypted=? WHERE hash=?", (rows[0][1], rows[1][0]))
    swapped = next(
        raw for raw in identifiers if hashlib.sha256(raw.encode()).hexdigest() == rows[1][0]
    )
    with pytest.raises(HostedStoreError):
        run(provider.approve(swapped, alice.id))


def test_empty_fragment_is_not_an_approved_redirect(env):
    with pytest.raises(RegistrationError):
        run(env[0].register_client(client(redirect_uris=["https://chatgpt.com/callback#"])))


def test_changed_public_resource_rejects_old_tokens_and_requests(env):
    provider, store, _, _ = env
    player, tokens, app = tokens_for(provider, store)
    request_id = parse_qs(urlparse(run(provider.authorize(app, params()))).query)["request_id"][0]
    changed = hosted_oauth.HostedOAuthProvider(store, "https://different.example")
    assert run(changed.load_access_token(tokens.access_token)) is None
    assert run(changed.load_refresh_token(app, tokens.refresh_token)) is None
    with pytest.raises(AuthorizeError):
        run(changed.complete_login(request_id, player.id))


def test_public_revocation_preserves_foreign_grants_and_rejects_duplicates(env):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    provider, store, _, _ = env
    _, tokens, app = tokens_for(provider, store)
    other = client("other")
    run(provider.register_client(other))
    with TestClient(Starlette(routes=provider.auth_routes())) as http:
        foreign = http.post(
            "/revoke", data={"client_id": other.client_id, "token": tokens.access_token}
        )
        assert foreign.status_code == 200
        assert run(provider.load_access_token(tokens.access_token)) is not None
        duplicate = http.post(
            "/revoke",
            data={
                "client_id": app.client_id,
                "token": tokens.access_token,
                "client_secret": ["", "secret"],
            },
        )
        assert duplicate.status_code == 400
        assert run(provider.load_access_token(tokens.access_token)) is not None


def test_read_only_grant_has_no_refresh_and_account_scopes_are_structured(env):
    provider, store, _, _ = env
    app = client()
    run(provider.register_client(app))
    alice = store.player("issuer", "alice")
    login = run(provider.authorize(app, params(scopes=["msf:read"])))
    request_id = parse_qs(urlparse(login).query)["request_id"][0]
    run(provider.complete_login(request_id, alice.id))
    raw = parse_qs(urlparse(run(provider.approve(request_id, alice.id))).query)["code"][0]
    code = run(provider.load_authorization_code(app, raw))
    tokens = run(provider.exchange_authorization_code(app, code))
    assert tokens.refresh_token is None
    assert run(provider.list_grants(alice.id))[0]["scopes"] == ["msf:read"]
