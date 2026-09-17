import pytest
from cryptography.fernet import Fernet

from msf_assistant.auth import TokenSet
from msf_assistant.config import Settings
from msf_assistant.hosted_store import HostedStore


def test_refresh_persists_rotated_credentials_before_failed_fetch(tmp_path, monkeypatch):
    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("old", refresh_token="keep"))
    output = store.player_dir(player.id) / "snapshot.json"
    output.write_text("old snapshot")
    monkeypatch.setattr(hosted_sync.MSFOAuth2, "refresh", lambda self, token: TokenSet("renewed"))

    def fail(*args, **kwargs):
        assert store.load_tokens(player.id).access_token == "renewed"
        raise RuntimeError("secret upstream data")

    monkeypatch.setattr(hosted_sync, "fetch_snapshot", fail)
    with pytest.raises(hosted_sync.HostedSyncError, match="Refresh failed"):
        hosted_sync.HostedSync(store, Settings("test")).refresh(player.id)
    assert store.load_tokens(player.id).refresh_token == "keep"
    assert output.read_text() == "old snapshot"


def test_context_limit_rejects_without_creating_file(tmp_path):
    from msf_assistant.advisor_context import ContextError, ContextStore

    context = ContextStore(tmp_path / "context.json", max_bytes=300)
    with pytest.raises(ContextError, match="too large"):
        context.save_goal(
            expected_revision=0,
            title="x" * 200,
            description="",
            status="proposed",
            provenance="test",
        )
    assert not context.path.exists()


def test_snapshot_budget_rejects_before_replacement(tmp_path):
    from msf_assistant.cli import write_snapshot

    output = tmp_path / "snapshot.json"
    output.write_text("existing")
    with pytest.raises(ValueError, match="too large"):
        write_snapshot(output, {"data": "x" * 100}, max_bytes=10)
    assert output.read_text() == "existing"


def test_upstream_ingestion_is_bounded():
    from msf_assistant.client import MSFAPIClient, MSFAPIError

    class Response:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'{"data":"'
            yield b"x" * 100

        def close(self):
            pass

    class Session:
        headers = {}

        def get(self, *args, **kwargs):
            assert kwargs["stream"] is True
            return Response()

    client = MSFAPIClient(Settings("test"), "secret", Session(), max_bytes=20)
    with pytest.raises(MSFAPIError, match="too large"):
        client.player_profile()


def test_parallel_refresh_isolation_and_capacity(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    players = [store.player("issuer", str(i)) for i in range(3)]
    for i, player in enumerate(players):
        store.save_tokens(player.id, TokenSet(f"token-{i}"))
    entered = threading.Barrier(3)
    release = threading.Event()

    def fetch(settings, tokens, output, **kwargs):
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        output.write_text(tokens.access_token)

    monkeypatch.setattr(hosted_sync, "fetch_snapshot", fetch)
    sync = hosted_sync.HostedSync(store, Settings("test"))
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(sync.refresh, p.id) for p in players[:2]]
        entered.wait(timeout=5)
        try:
            with pytest.raises(hosted_sync.HostedSyncError, match="busy"):
                sync.refresh(players[2].id)
        finally:
            release.set()
        for job in jobs:
            job.result(timeout=5)
    for i, player in enumerate(players[:2]):
        assert (store.player_dir(player.id) / "snapshot.json").read_text() == f"token-{i}"


def test_deletion_waits_for_refresh_and_cannot_resurrect(tmp_path, monkeypatch):
    import shutil
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("token"))
    directory = store.player_dir(player.id)
    entered, release = threading.Event(), threading.Event()

    def fetch(settings, tokens, output, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        output.write_text("snapshot")

    def delete():
        with store.player_lock(player.id):
            store.deactivate_player(player.id)
            shutil.rmtree(directory)

    monkeypatch.setattr(hosted_sync, "fetch_snapshot", fetch)
    sync = hosted_sync.HostedSync(store, Settings("test"))
    with ThreadPoolExecutor(2) as pool:
        job = pool.submit(sync.refresh, player.id)
        assert entered.wait(timeout=5)
        deletion = pool.submit(delete)
        release.set()
        job.result(timeout=5)
        deletion.result(timeout=5)
    with pytest.raises(hosted_sync.HostedSyncError):
        sync.refresh(player.id)
    assert not directory.exists()


def test_character_catalog_uses_cumulative_ingestion_budget():
    import json

    from msf_assistant.client import MSFAPIClient, MSFAPIError

    raw = json.dumps({"data": [{"id": "x"}]}).encode()

    class Response:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield raw

        def close(self):
            pass

    class Session:
        headers = {}

        def get(self, *args, **kwargs):
            return Response()

    client = MSFAPIClient(Settings("test"), "token", Session(), max_bytes=len(raw) * 2)
    with pytest.raises(MSFAPIError, match="too large"):
        client.game_characters(per_page=1)
    assert client.remaining_bytes == 0


def test_hosted_token_response_rejected_before_json_parsing():
    from unittest.mock import Mock

    from msf_assistant.auth import MSFOAuth2

    session = Mock()
    session.post.return_value.iter_content.return_value = [b"x" * 65]
    oauth = MSFOAuth2(Settings("app", client_secret="secret"), session, max_response_bytes=64)
    with pytest.raises(ValueError, match="too large"):
        oauth.refresh("refresh-secret")
    session.post.return_value.json.assert_not_called()
    assert session.post.call_args.kwargs["stream"] is True


def test_hosted_userinfo_response_is_bounded():
    from unittest.mock import Mock

    from msf_assistant.hosted_identity import MSFIdentity

    identity = MSFIdentity(Settings("app", client_secret="secret"))
    identity.oauth.session = Mock()
    identity.oauth.exchange_code = Mock(return_value=TokenSet("access"))
    response = identity.oauth.session.get.return_value
    response.status_code = 200
    response.iter_content.return_value = [b"x" * (65536 + 1)]
    with pytest.raises(ValueError, match="too large"):
        identity.exchange("code")
    response.json.assert_not_called()


@pytest.mark.parametrize("endpoint", ["api", "token", "userinfo"])
def test_hosted_redirect_rejected_without_consuming_body(endpoint, monkeypatch):
    """Exercise real Requests Session.send/resolve_redirects, without a network."""
    import io
    from functools import partial

    import requests

    from msf_assistant.auth import MSFOAuth2
    from msf_assistant.client import MSFAPIClient
    from msf_assistant.hosted_identity import MSFIdentity

    class Probe(io.BytesIO):
        bytes_read = 0

        def read(self, amount=-1):
            data = super().read(amount)
            self.bytes_read += len(data)
            return data

    class Adapter(requests.adapters.BaseAdapter):
        calls = 0

        def send(self, request, **kwargs):
            self.calls += 1
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.status_code = 302 if self.calls == 1 else 200
            response.headers["Location"] = "https://upstream.example/redirected"
            response.raw = raw if self.calls == 1 else io.BytesIO(b'{"data":{}}')
            return response

        def close(self):
            pass

    raw = Probe(b"x" * 131072)
    adapter = Adapter()
    settings = Settings("app", client_secret="secret")
    with requests.Session() as session:
        session.mount("https://", adapter)
        if endpoint == "api":
            operation = MSFAPIClient(settings, "access", session, max_bytes=65536).player_profile
        elif endpoint == "token":
            oauth = MSFOAuth2(settings, session, max_response_bytes=65536)
            operation = partial(oauth.refresh, "refresh")
        else:
            identity = MSFIdentity(settings)
            identity.oauth.session = session
            monkeypatch.setattr(identity.oauth, "exchange_code", lambda code: TokenSet("access"))
            operation = partial(identity.exchange, "code")
        caught = None
        try:
            operation()
        except (ValueError, requests.RequestException) as exc:
            caught = exc
        assert raw.bytes_read == 0
        assert raw.closed
        assert adapter.calls == 1
        assert caught is not None
        assert str(caught) == "Upstream redirect rejected"


def test_context_limit_preserves_existing_valid_file(tmp_path):
    from msf_assistant.advisor_context import ContextError, ContextStore

    path = tmp_path / "context.json"
    original = ContextStore(path).save_goal(
        expected_revision=0, title="Existing", description="", status="selected", provenance="test"
    )
    previous = path.read_bytes()
    limited = ContextStore(path, max_bytes=len(previous))
    with pytest.raises(ContextError, match="too large"):
        limited.save_goal(
            expected_revision=1,
            title="Existing",
            description="x" * 100,
            status="selected",
            provenance="test",
            record_id=original["goals"][0]["id"],
        )
    assert path.read_bytes() == previous
    assert limited.read() == original


def test_expired_msf_credentials_point_to_the_hosted_login(tmp_path, monkeypatch):
    import requests

    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("old", refresh_token="stale"))

    def rejected(self, token):
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(hosted_sync.MSFOAuth2, "refresh", rejected)
    sync = hosted_sync.HostedSync(store, Settings("test"), login_url="https://msf.example/login")
    with pytest.raises(hosted_sync.HostedSyncError) as failure:
        sync.refresh(player.id)
    assert "https://msf.example/login" in str(failure.value)
    assert "sync --characters" not in str(failure.value)
    # A player without stored credentials gets the same actionable message.
    other = store.player("issuer", "bob")
    with pytest.raises(hosted_sync.HostedSyncError, match="msf.example/login"):
        sync.refresh(other.id)


def lock_is_free(store, player_id):
    import fcntl
    import os

    fd = os.open(store.root / "locks" / (player_id + ".lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_query_runs_under_the_player_lock_with_a_bounded_client(tmp_path, monkeypatch):
    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("access", refresh_token="keep"))
    monkeypatch.setattr(
        hosted_sync.MSFOAuth2, "refresh", lambda *a, **k: pytest.fail("must not refresh")
    )
    sync = hosted_sync.HostedSync(store, Settings("test"))

    def fn(client):
        assert client.session.headers["Authorization"] == "Bearer access"
        assert client.remaining_bytes == hosted_sync.QUERY_BYTES
        # The player lock is held: a concurrent deletion or refresh must wait.
        assert not lock_is_free(store, player.id)
        return {"ok": True}

    assert sync.query(player.id, fn) == {"ok": True}
    assert lock_is_free(store, player.id)


def test_query_refresh_failure_and_missing_credentials_point_to_the_login(
    tmp_path, monkeypatch
):
    import requests

    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("old", refresh_token="stale"))

    def rejected(self, token):
        response = requests.Response()
        response.status_code = 400
        raise requests.HTTPError(response=response)

    def unauthorized(client):
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(hosted_sync.MSFOAuth2, "refresh", rejected)
    sync = hosted_sync.HostedSync(store, Settings("test"), login_url="https://msf.example/login")
    with pytest.raises(hosted_sync.HostedSyncError, match="msf.example/login"):
        sync.query(player.id, unauthorized)
    other = store.player("issuer", "bob")
    with pytest.raises(hosted_sync.HostedSyncError, match="msf.example/login"):
        sync.query(other.id, lambda client: pytest.fail("no credentials, no call"))


def test_query_failures_are_generic_and_capacity_bounded(tmp_path):
    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("access"))
    sync = hosted_sync.HostedSync(store, Settings("test"))

    def leaky(client):
        raise RuntimeError("secret upstream body")

    with pytest.raises(hosted_sync.HostedSyncError) as failure:
        sync.query(player.id, leaky)
    assert "secret" not in str(failure.value)
    assert "MSF query failed" in str(failure.value)
    sync.capacity.acquire()
    sync.capacity.acquire()
    with pytest.raises(hosted_sync.HostedSyncError, match="busy"):
        sync.query(player.id, lambda client: {"ok": True})
    sync.capacity.release()
    assert sync.query(player.id, lambda client: {"ok": True}) == {"ok": True}


def test_query_404_names_the_id_and_other_statuses_stay_generic(tmp_path):
    import requests

    from msf_assistant import hosted_sync

    store = HostedStore(tmp_path / "private", Fernet.generate_key())
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("access"))
    sync = hosted_sync.HostedSync(store, Settings("test"))

    def failing(status):
        def fn(client):
            response = requests.Response()
            response.status_code = status
            raise requests.HTTPError(response=response)

        return fn

    with pytest.raises(hosted_sync.HostedSyncError, match="character ID"):
        sync.query(player.id, failing(404))
    with pytest.raises(hosted_sync.HostedSyncError, match="MSF query failed"):
        sync.query(player.id, failing(403))
