import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from test_hosted_oauth import client, params

from msf_assistant.config import Settings
from msf_assistant.hosted_oauth import HostedOAuthProvider
from msf_assistant.hosted_store import HostedStore


def issue(store, provider, subject, scopes, *, client_id):
    async def mint():
        app = await provider.get_client(client_id)
        if app is None:
            app = client(client_id)
            await provider.register_client(app)
        player = store.player("issuer", subject)
        login = await provider.authorize(app, params(scopes=scopes))
        request_id = parse_qs(urlparse(login).query)["request_id"][0]
        await provider.complete_login(request_id, player.id)
        callback = await provider.approve(request_id, player.id)
        raw = parse_qs(urlparse(callback).query)["code"][0]
        code = await provider.load_authorization_code(app, raw)
        return player, await provider.exchange_authorization_code(app, code)

    return asyncio.run(mint())


@pytest.fixture
def env(tmp_path):
    from msf_assistant.hosted_server import create_hosted_app

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    provider = HostedOAuthProvider(store, "https://msf.example")
    alice, a = issue(store, provider, "alice", ["msf:read", "msf:write"], client_id="chatgpt")
    bob, b = issue(store, provider, "bob", ["msf:read"], client_id="chatgpt")
    app = create_hosted_app(store, provider, object(), Settings("test"), "https://msf.example")
    return app, store, provider, alice, a, bob, b


def rpc(http, token, method, params=None, **kwargs):
    return http.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        **kwargs,
    )


def test_auth_isolation_scopes_and_prompt(env):
    app, store, provider, alice, a, bob, b = env
    with TestClient(app, base_url="https://msf.example") as http:
        response = http.post("/mcp")
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
        args = dict(
            expected_revision=0,
            title="Alice goal",
            description="",
            status="proposed",
            provenance="test",
        )
        assert (
            rpc(
                http, b.access_token, "tools/call", {"name": "save_goal", "arguments": args}
            ).status_code
            == 403
        )
        saved = rpc(http, a.access_token, "tools/call", {"name": "save_goal", "arguments": args})
        assert saved.status_code == 200, saved.text
        assert "Alice goal" in saved.text
        other = rpc(http, b.access_token, "tools/call", {"name": "get_advisor_context"})
        assert "Alice goal" not in other.text
        prompt = rpc(
            http,
            b.access_token,
            "prompts/get",
            {"name": "plan_upgrades", "arguments": {"question": "Hi"}},
        )
        assert prompt.status_code == 200
        # Bob has no snapshot yet: the guide must not depend on player data.
        guide = rpc(http, b.access_token, "tools/call", {"name": "get_guide"})
        assert guide.json()["result"]["structuredContent"]["workflow"]
        task = rpc(http, b.access_token, "prompts/get", {"name": "data_check", "arguments": {}})
        assert "Task focus" in task.text
        resource = rpc(http, b.access_token, "resources/read", {"uri": "guide://advisor"})
        assert "get_status" in resource.text
        extra = rpc(
            http,
            a.access_token,
            "tools/call",
            {"name": "save_goal", "arguments": {**args, "player_id": bob.id}},
        )
        assert "Unknown tool argument" in extra.text


def test_limits_status_codes_and_capacity(env):
    from msf_assistant.hosted_server import HostedLimits

    app, store, provider, alice, a, bob, b = env
    app.limits = HostedLimits(request_bytes=300, requests_per_minute=2)
    with TestClient(app, base_url="https://msf.example") as http:
        assert http.post("/register", content=b"x" * 301).status_code == 413
        for _ in range(2):
            assert rpc(http, b.access_token, "tools/list").status_code == 200
        limited = rpc(http, b.access_token, "tools/list")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
        app.sync.capacity.acquire()
        app.sync.capacity.acquire()
        try:
            assert (
                rpc(http, a.access_token, "tools/call", {"name": "refresh_data"}).status_code == 503
            )
        finally:
            app.sync.capacity.release()
            app.sync.capacity.release()
        app.active = 4
        assert rpc(http, a.access_token, "tools/list").status_code == 503
        assert http.get("/health").status_code == 200
        app.active = 0


def test_revoke_wrong_resource_expiry_and_recreation(env):
    import time

    from msf_assistant.hosted_server import create_hosted_app

    app, store, provider, alice, a, bob, b = env
    rebuilt = create_hosted_app(store, provider, object(), Settings("test"), "https://msf.example")
    with TestClient(rebuilt, base_url="https://msf.example") as http:
        assert rpc(http, a.access_token, "tools/list").status_code == 200
        with store.transaction() as db:
            db.execute(
                "UPDATE oauth_grants SET resource=? WHERE player=?",
                ("https://foreign/mcp", alice.id),
            )
        assert rpc(http, a.access_token, "tools/list").status_code == 401
        with store.transaction() as db:
            db.execute(
                "UPDATE oauth_grants SET expires=? WHERE player=?", (time.time() - 1, bob.id)
            )
        assert rpc(http, b.access_token, "tools/list").status_code == 401
    player, c = issue(store, provider, "revoked", ["msf:read"], client_id="revoked-client")
    asyncio.run(provider.revoke_player(player.id))
    with TestClient(app, base_url="https://msf.example") as http:
        assert rpc(http, c.access_token, "prompts/list").status_code == 401


def test_real_loopback_sdk_two_clients_and_reconnect(env):
    import socket
    import threading
    import time

    import httpx2
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app, store, provider, alice, a, bob, b = env
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    from contextlib import asynccontextmanager

    alice_again, claude = issue(
        store, provider, "alice", ["msf:read", "msf:write"], client_id="claude"
    )
    assert alice_again.id == alice.id
    assert claude.access_token != a.access_token

    @asynccontextmanager
    async def connect(token):
        async with (
            httpx2.AsyncClient(
                headers={
                    "Authorization": "Bearer " + token,
                    "Mcp-Session-Id": "foreign-session",
                    "Last-Event-ID": "foreign-request",
                }
            ) as http,
            streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert any(tool.name == "get_advisor_context" for tool in tools.tools)
            yield session

    async def check():
        async with (
            connect(a.access_token) as chatgpt,
            connect(claude.access_token) as second_client,
            connect(b.access_token) as bob_client,
        ):
            saved = await chatgpt.call_tool(
                "save_goal",
                dict(
                    expected_revision=0,
                    title="From ChatGPT",
                    description="",
                    status="selected",
                    provenance="test",
                ),
            )
            assert not saved.is_error
            shared = await second_client.call_tool("get_advisor_context")
            assert shared.structured_content["revision"] == 1
            assert "From ChatGPT" in str(shared)
            updated = await second_client.call_tool(
                "save_goal",
                dict(
                    expected_revision=1,
                    title="From Claude",
                    description="",
                    status="selected",
                    provenance="test",
                ),
            )
            assert not updated.is_error
            shared, isolated = await asyncio.gather(
                chatgpt.call_tool("get_advisor_context"),
                bob_client.call_tool("get_advisor_context"),
            )
            assert shared.structured_content["revision"] == 2
            assert "From ChatGPT" in str(shared) and "From Claude" in str(shared)
            assert isolated.structured_content["revision"] == 0
            assert not isolated.structured_content["goals"]
            await second_client.get_prompt("plan_upgrades", {"question": "test"})
        # Reconnect with Alice's other independently issued client grant.
        async with connect(claude.access_token) as reconnected:
            durable = await reconnected.call_tool("get_advisor_context")
            assert durable.structured_content["revision"] == 2
            assert "From ChatGPT" in str(durable) and "From Claude" in str(durable)

    try:
        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


def test_simultaneous_context_writes_report_conflict(env):
    from concurrent.futures import ThreadPoolExecutor

    app, store, provider, alice, a, bob, b = env
    with TestClient(app, base_url="https://msf.example") as http:

        def save(title):
            return rpc(
                http,
                a.access_token,
                "tools/call",
                {
                    "name": "save_goal",
                    "arguments": dict(
                        expected_revision=0,
                        title=title,
                        description="",
                        status="proposed",
                        provenance="test",
                    ),
                },
            ).json()

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, ["one", "two"]))
        assert sum(bool(result["result"].get("isError")) for result in results) == 1
        assert "revision" in str(results).lower()


def test_slow_request_body_times_out_and_releases_capacity(env):
    import anyio

    from msf_assistant.hosted_server import HostedLimits

    app, *_ = env
    app.limits = HostedLimits(body_timeout=0.01)

    async def exercise():
        sent = []

        async def receive():
            await anyio.sleep(1)
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        await app({"type": "http", "path": "/register", "method": "POST"}, receive, send)
        assert sent[0]["status"] == 408
        assert app.active == 0

    asyncio.run(exercise())


def test_slow_refresh_does_not_block_other_player(env, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from msf_assistant import hosted_sync
    from msf_assistant.auth import TokenSet

    app, store, provider, alice, a, bob, b = env
    store.save_tokens(alice.id, TokenSet("secret"))
    entered, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("DO-NOT-EXPOSE-secret")

    monkeypatch.setattr(hosted_sync, "fetch_snapshot", fetch)
    with TestClient(app, base_url="https://msf.example") as http, ThreadPoolExecutor(1) as pool:
        job = pool.submit(rpc, http, a.access_token, "tools/call", {"name": "refresh_data"})
        assert entered.wait(timeout=5)
        try:
            response = rpc(http, b.access_token, "tools/call", {"name": "get_advisor_context"})
            assert response.status_code == 200
            assert not job.done()
        finally:
            release.set()
        assert "DO-NOT-EXPOSE" not in job.result(timeout=5).text


def test_malformed_tool_name_is_protocol_error_not_crash(env):
    app, store, provider, alice, a, bob, b = env
    with TestClient(app, base_url="https://msf.example") as http:
        response = rpc(http, a.access_token, "tools/call", {"name": ["invalid"]})
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32602
        assert app.active == 0


def test_rate_state_expires_and_remains_bounded(env, monkeypatch):
    from msf_assistant import hosted_server

    app, store, provider, alice, a, bob, b = env
    app.limits = hosted_server.HostedLimits(rate_players=1)
    now = [1000.0]
    monkeypatch.setattr(hosted_server.time, "monotonic", lambda: now[0])
    with TestClient(app, base_url="https://msf.example") as http:
        assert rpc(http, a.access_token, "tools/list").status_code == 200
        assert rpc(http, b.access_token, "tools/list").status_code == 503
        now[0] += 61
        assert rpc(http, b.access_token, "tools/list").status_code == 200
        assert len(app.rates) == 1
        assert http.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
        assert http.get("/.well-known/oauth-authorization-server").status_code == 200


def test_composed_app_advertises_every_scope_to_real_clients(env):
    """Clients derive requested scopes from WWW-Authenticate, then PRM, then AS metadata.

    The SDK mounts its own protected-resource route from AuthSettings; it must not
    shadow the provider's, or real clients would only ever ask for msf:read.
    """
    from msf_assistant.hosted_oauth import SCOPES

    app, *_ = env
    with TestClient(app, base_url="https://msf.example") as http:
        challenge = http.post("/mcp")
        assert challenge.status_code == 401
        assert 'scope="msf:read msf:write offline_access"' in challenge.headers["WWW-Authenticate"]
        prm = http.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["scopes_supported"] == SCOPES
        assert prm["resource"] == "https://msf.example/mcp"
        served = http.get("/.well-known/oauth-authorization-server").json()
        assert served["scopes_supported"] == SCOPES
        # RFC 9728 entries are issuer identifiers; they must equal the issuer
        # byte for byte (no trailing slash) for strict clients.
        assert prm["authorization_servers"] == [served["issuer"]] == ["https://msf.example"]
        assert [
            r.path for r in app.app.routes if r.path == "/.well-known/oauth-protected-resource/mcp"
        ] == ["/.well-known/oauth-protected-resource/mcp"]


def test_initialization_retains_advisor_instructions(env):
    from msf_assistant.advisor_instructions import ADVISOR_INSTRUCTIONS

    app, store, provider, alice, a, bob, b = env
    with TestClient(app, base_url="https://msf.example") as http:
        response = rpc(
            http,
            a.access_token,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert response.json()["result"].get("instructions") == ADVISOR_INSTRUCTIONS


def test_snapshot_reads_and_context_survive_app_recreation(env):
    from test_snapshot import write_snapshot

    from msf_assistant.hosted_server import create_hosted_app

    app, store, provider, alice, a, bob, b = env
    alice_again, claude = issue(
        store, provider, "alice", ["msf:read", "msf:write"], client_id="claude"
    )
    assert alice_again.id == alice.id
    assert asyncio.run(provider.load_access_token(a.access_token)).client_id == "chatgpt"
    assert asyncio.run(provider.load_access_token(claude.access_token)).client_id == "claude"
    write_snapshot(store.player_dir(alice.id), profile={"data": {"name": "Alice synthetic"}})
    write_snapshot(store.player_dir(bob.id), profile={"data": {"name": "Bob synthetic"}})

    def context(http, token):
        response = rpc(http, token.access_token, "tools/call", {"name": "get_advisor_context"})
        assert response.status_code == 200
        return response.json()["result"]["structuredContent"]

    def save(http, token, revision, title):
        response = rpc(
            http,
            token.access_token,
            "tools/call",
            {
                "name": "save_goal",
                "arguments": dict(
                    expected_revision=revision,
                    title=title,
                    description="",
                    status="selected",
                    provenance="test",
                ),
            },
        )
        assert not response.json()["result"].get("isError")

    with TestClient(app, base_url="https://msf.example") as http:
        for token, expected, other in [
            (a, "Alice synthetic", "Bob synthetic"),
            (claude, "Alice synthetic", "Bob synthetic"),
            (b, "Bob synthetic", "Alice synthetic"),
        ]:
            response = rpc(http, token.access_token, "tools/call", {"name": "get_player_profile"})
            assert expected in response.text and other not in response.text
        save(http, a, 0, "ChatGPT durable goal")
        assert context(http, claude)["revision"] == 1
        save(http, claude, 1, "Claude durable goal")
        assert context(http, a)["revision"] == 2
        assert context(http, b)["revision"] == 0
    rebuilt = create_hosted_app(store, provider, object(), Settings("test"), "https://msf.example")
    with TestClient(rebuilt, base_url="https://msf.example") as http:
        first = context(http, a)
        second = context(http, claude)
        assert first == second
        assert first["revision"] == 2
        assert {goal["title"] for goal in first["goals"]} == {
            "ChatGPT durable goal",
            "Claude durable goal",
        }
        save(http, claude, 2, "After recreation")
        assert context(http, a)["revision"] == 3
        assert context(http, b)["revision"] == 0
        assert not context(http, b)["goals"]


def test_auth_storage_failure_returns_secret_free_error(env, monkeypatch):
    app, store, provider, alice, a, bob, b = env

    async def broken(token):
        raise RuntimeError("private-storage-secret")

    monkeypatch.setattr(provider, "load_access_token", broken)
    with TestClient(app, base_url="https://msf.example") as http:
        response = rpc(http, a.access_token, "tools/list")
        assert response.status_code == 503
        assert "private-storage-secret" not in response.text
        assert app.active == 0


def test_response_size_cap_returns_safe_error(env):
    from msf_assistant.hosted_server import HostedLimits

    app, store, provider, alice, a, bob, b = env
    app.limits = HostedLimits(response_bytes=100)
    with TestClient(app, base_url="https://msf.example") as http:
        response = rpc(http, a.access_token, "tools/list")
        assert response.status_code == 503
        assert len(response.content) < 100
        assert app.active == 0


def test_hosted_tool_errors_never_mention_local_cli_commands(env, monkeypatch):
    from msf_assistant.hosted_sync import HostedSyncError

    app, store, provider, alice, a, bob, b = env
    with TestClient(app, base_url="https://msf.example") as http:
        # Alice has no snapshot yet: the advice must be refresh_data, not "run login".
        profile = rpc(http, a.access_token, "tools/call", {"name": "get_player_profile"})
        assert profile.status_code == 200 and profile.json()["result"]["isError"]
        assert "refresh_data" in profile.text
        for forbidden in ("run login", "sync --characters", "Lokal", "lokal", "local"):
            assert forbidden not in profile.text, forbidden
        monkeypatch.setattr(
            app.sync,
            "refresh",
            lambda *a, **k: (_ for _ in ()).throw(
                HostedSyncError("MSF-Anmeldung abgelaufen: https://msf.example/login")
            ),
        )
        failed = rpc(http, a.access_token, "tools/call", {"name": "refresh_data"})
        assert failed.status_code == 200
        assert "https://msf.example/login" in failed.text
        assert "sync --characters" not in failed.text


def test_write_tools_match_registered_annotations():
    """WRITE_TOOLS gates the write scope; it must equal the tools that declare writes."""
    from msf_assistant import hosted_server
    from msf_assistant.mcp_server import AdvisorServer, register_tools

    server = AdvisorServer("t", version="0", instructions="", log_level="WARNING")
    register_tools(server, object(), object(), refresh=lambda: None, query=lambda fn: None)
    tools = asyncio.run(server.list_tools())
    writes = {t.name for t in tools if not t.annotations.read_only_hint}
    assert writes == set(hosted_server.WRITE_TOOLS)


def test_snapshots_expire_after_thirty_days(env):
    """MSF API terms require a 30-day TTL on Data pulled from the API."""
    import os
    import time

    app, store, provider, alice, a, bob, b = env
    path = store.player_dir(alice.id) / "snapshot.json"
    path.write_text('{"retrieved_at": "old"}')
    path.chmod(0o600)
    old = time.time() - 31 * 86400
    os.utime(path, (old, old))
    with TestClient(app, base_url="https://msf.example") as http:
        profile = rpc(http, a.access_token, "tools/call", {"name": "get_player_profile"})
        assert profile.json()["result"]["isError"]
        assert "expired" in profile.text and "refresh_data" in profile.text
        assert not path.exists()
        status = rpc(http, a.access_token, "tools/call", {"name": "get_status"})
        assert status.json()["result"]["structuredContent"]["available"] is False


def test_project_character_is_a_read_scope_live_query_per_player(env, monkeypatch):
    from msf_assistant import hosted_sync
    from msf_assistant.auth import TokenSet

    app, store, provider, alice, a, bob, b = env
    store.save_tokens(bob.id, TokenSet("bob-access"))
    calls = []

    def fake_run_query(settings, tokens, fn, *, save_tokens, max_bytes=None):
        calls.append((tokens.access_token, max_bytes))

        class FakeClient:
            def character_instance(self, character_id, **build):
                return {"data": {"gearTier": build["gear_tier"], "power": 99, "stats": {}}}

        return fn(FakeClient())

    monkeypatch.setattr(hosted_sync, "run_query", fake_run_query)
    build = {"character_id": "Wolverine", "level": 90, "yellow": 7, "red": 7, "gear_tier": 18}
    with TestClient(app, base_url="https://msf.example") as http:
        tools = rpc(http, b.access_token, "tools/list").json()["result"]["tools"]
        project = next(t for t in tools if t["name"] == "project_character")
        assert project["annotations"]["readOnlyHint"] and project["annotations"]["openWorldHint"]
        # Read scope suffices; the query runs with Bob's own credentials.
        result = rpc(
            http, b.access_token, "tools/call", {"name": "project_character", "arguments": build}
        )
        assert result.status_code == 200, result.text
        assert result.json()["result"]["structuredContent"]["builds"][0]["power"] == 99
        assert calls == [("bob-access", hosted_sync.QUERY_BYTES)]
        # Alice has no stored MSF credentials: hosted remedy, no local CLI hints.
        failed = rpc(
            http, a.access_token, "tools/call", {"name": "project_character", "arguments": build}
        )
        assert failed.json()["result"]["isError"]
        assert "https://msf.example/login" in failed.text
        assert "run login" not in failed.text and "sync --characters" not in failed.text
