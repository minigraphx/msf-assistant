"""Backup must serialize database and files, and never revive authorization."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from msf_assistant.hosted_store import HostedStore, HostedStoreError


def test_exclusive_maintenance_blocks_mutation_and_player_files(tmp_path):
    store = HostedStore(tmp_path / "data", Fernet.generate_key())
    player = store.player("issuer", "subject")
    store.LOCK_TIMEOUT_SECONDS = 0.05
    with store.maintenance(exclusive=True), ThreadPoolExecutor() as pool:
        for operation in (
            lambda: store.player("issuer", "new"),
            lambda: enter_player(store, player.id),
        ):
            with pytest.raises(HostedStoreError, match="busy"):
                pool.submit(contextvars.Context().run, operation).result()


def enter_player(store, player):
    with store.player_lock(player):
        pass


def test_inherited_lease_survives_parent_exit_and_stale_context_reacquires(tmp_path):
    store = HostedStore(tmp_path / "data", Fernet.generate_key())
    entered, release = threading.Event(), threading.Event()
    store.LOCK_TIMEOUT_SECONDS = 0.05

    def child():
        with store.maintenance():
            entered.set()
            release.wait(2)

    with ThreadPoolExecutor() as pool:
        with store.maintenance():
            inherited = contextvars.copy_context()
            stale = contextvars.copy_context()
            future = pool.submit(inherited.run, child)
            assert entered.wait(2)
        with pytest.raises(HostedStoreError, match="busy"), store.maintenance(exclusive=True):
            pass
        release.set()
        future.result()
        with store.maintenance(exclusive=True), pytest.raises(HostedStoreError, match="busy"):
            pool.submit(stale.run, lambda: store.player("issuer", "new")).result()


def test_backup_restore_invalidates_browser_and_oauth_state(tmp_path):
    from msf_assistant.hosted_backup import backup, restore
    from msf_assistant.hosted_oauth import HostedOAuthProvider
    from msf_assistant.hosted_web import BrowserSessions

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    HostedOAuthProvider(store, "https://msf.example")
    sessions = BrowserSessions(store)
    player = store.player("issuer", "subject")
    raw, _ = sessions.create({"player": player.id})
    path = store.player_dir(player.id) / "context.json"
    path.write_text('{"test":"synthetic"}')
    path.chmod(0o600)
    archive = backup(store, tmp_path / "backups")
    restored = restore(archive, tmp_path / "restored", key, maintenance=True)
    assert (restored.root / "RESTORE_MAINTENANCE").exists()
    with pytest.raises(ValueError, match="Expired"):
        BrowserSessions(restored).read(raw)
    assert (restored.player_dir(player.id) / "context.json").read_bytes() == path.read_bytes()
    with restored.transaction() as db:
        for table in (
            "oauth_clients",
            "oauth_requests",
            "oauth_grants",
            "oauth_codes",
            "oauth_tokens",
            "browser_sessions",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(ValueError, match="maintenance"):
        restore(archive, tmp_path / "other", key)
    with pytest.raises(ValueError, match="exist"):
        restore(archive, store.root, key, maintenance=True)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", "file"),
        ("/absolute", "file"),
        ("players/link", "symlink"),
        ("hosted.sqlite3", "hardlink"),
        ("secrets.env", "file"),
    ],
)
def test_restore_rejects_unsafe_archive(tmp_path, name, kind):
    import io
    import tarfile

    from msf_assistant.hosted_backup import restore

    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name)
        if kind != "file":
            info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            info.linkname = "/tmp/outside"
        tar.addfile(info, io.BytesIO())
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "restored", Fernet.generate_key(), maintenance=True)
    assert not (tmp_path / "restored").exists()


def test_backup_retention_and_excludes_unexpected_files(tmp_path):
    from msf_assistant.hosted_backup import backup

    store = HostedStore(tmp_path / "data", Fernet.generate_key())
    first = backup(store, tmp_path / "backups", retention=1)
    second = backup(store, tmp_path / "backups", retention=1)
    assert not first.exists() and second.exists()
    player = store.player("issuer", "subject")
    secret = store.player_dir(player.id) / ".env"
    secret.write_text("synthetic-secret")
    secret.chmod(0o600)
    with pytest.raises(ValueError, match="Unexpected"):
        backup(store, tmp_path / "backups")


def test_restore_rejects_wrong_key_schema_and_oversize(tmp_path, monkeypatch):
    import sqlite3

    from msf_assistant import hosted_backup

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    archive = hosted_backup.backup(store, tmp_path / "backups")
    with pytest.raises(ValueError):
        hosted_backup.restore(
            archive, tmp_path / "bad-key", Fernet.generate_key(), maintenance=True
        )
    with sqlite3.connect(store.database_path) as db:
        db.execute("CREATE TABLE unexpected (value TEXT)")
    bad = hosted_backup.backup(store, tmp_path / "backups")
    with pytest.raises(ValueError, match="schema"):
        hosted_backup.restore(bad, tmp_path / "bad-schema", key, maintenance=True)
    monkeypatch.setattr(hosted_backup, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(ValueError, match="large"):
        hosted_backup.restore(archive, tmp_path / "oversize", key, maintenance=True)


def test_restored_tokens_fail_and_fresh_grant_reads_preserved_data(tmp_path):
    from test_hosted_oauth import code_for, run, tokens_for

    from msf_assistant.hosted_backup import backup, restore, resume
    from msf_assistant.hosted_oauth import HostedOAuthProvider

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    provider = HostedOAuthProvider(store, "https://msf.example")
    player, tokens, client = tokens_for(provider, store)
    _, code, _ = code_for(provider, store)
    path = store.player_dir(player.id) / "context.json"
    path.write_text('{"synthetic":"preserved"}')
    path.chmod(0o600)
    restored = restore(
        backup(store, tmp_path / "backups"), tmp_path / "restore", key, maintenance=True
    )
    reopened = HostedOAuthProvider(restored, "https://msf.example")
    assert run(reopened.load_access_token(tokens.access_token)) is None
    assert run(reopened.load_refresh_token(client, tokens.refresh_token)) is None
    assert run(reopened.load_authorization_code(client, code)) is None
    with pytest.raises(ValueError):
        resume(restored)
    resume(restored, deletions_reconciled=True)
    fresh_player, fresh, _ = tokens_for(reopened, restored)
    assert fresh_player.id == player.id
    verified = run(reopened.load_access_token(fresh.access_token))
    assert (
        restored.player_dir(verified.subject) / "context.json"
    ).read_bytes() == path.read_bytes()


def test_backup_real_context_ignores_only_its_lock_file(tmp_path):
    from msf_assistant.advisor_context import ContextStore
    from msf_assistant.hosted_backup import backup, restore

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    player = store.player("issuer", "alice")
    context = ContextStore(store.player_dir(player.id) / "context.json")
    context.save_goal(
        title="Synthetic goal",
        expected_revision=0,
        description="test",
        status="selected",
        provenance="user",
    )
    recovered = restore(
        backup(store, tmp_path / "backups"), tmp_path / "restore", key, maintenance=True
    )
    assert ContextStore(recovered.player_dir(player.id) / "context.json").read() == context.read()


def test_backup_cannot_split_a_token_and_player_file_update(tmp_path):
    from msf_assistant.auth import TokenSet
    from msf_assistant.hosted_backup import backup, restore

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    player = store.player("issuer", "alice")
    halfway, release = threading.Event(), threading.Event()

    def mutation():
        with store.player_lock(player.id):
            store.save_tokens(player.id, TokenSet("synthetic-new"))
            halfway.set()
            assert release.wait(2)
            output = store.player_dir(player.id) / "snapshot.json"
            output.write_text('{"version":"new"}')
            output.chmod(0o600)

    with ThreadPoolExecutor() as pool:
        changing = pool.submit(mutation)
        assert halfway.wait(2)
        saving = pool.submit(backup, store, tmp_path / "backups")
        assert not saving.done()
        release.set()
        changing.result()
        archive = saving.result()
    recovered = restore(archive, tmp_path / "restore", key, maintenance=True)
    assert recovered.load_tokens(player.id).access_token == "synthetic-new"
    assert (recovered.player_dir(player.id) / "snapshot.json").read_text() == '{"version":"new"}'


def test_maintenance_blocks_a_separate_process(tmp_path):
    import subprocess
    import sys

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    code = (
        "import sys; from pathlib import Path; "
        "from msf_assistant.hosted_store import HostedStore; "
        "HostedStore.LOCK_TIMEOUT_SECONDS=.05; "
        "HostedStore(Path(sys.argv[1]),sys.stdin.buffer.read())"
    )
    with store.maintenance(exclusive=True):
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.root)], input=key, capture_output=True
        )
    assert result.returncode != 0
    assert b"maintenance is busy" in result.stderr


def test_restore_marker_exists_before_database_is_opened(tmp_path, monkeypatch):
    from msf_assistant import hosted_backup

    key = Fernet.generate_key()
    store = HostedStore(tmp_path / "data", key)
    archive = hosted_backup.backup(store, tmp_path / "backups")
    validate = hosted_backup._validate_database

    def inspect(path, key, staging):
        assert (staging / "RESTORE_MAINTENANCE").exists()
        return validate(path, key, staging)

    monkeypatch.setattr(hosted_backup, "_validate_database", inspect)
    hosted_backup.restore(archive, tmp_path / "restore", key, maintenance=True)


def test_forked_child_cannot_borrow_parent_exclusive_lease(tmp_path):
    import os

    store = HostedStore(tmp_path / "data", Fernet.generate_key())
    store.LOCK_TIMEOUT_SECONDS = 0.05
    with store.maintenance(exclusive=True):
        child = os.fork()
        if child == 0:
            try:
                store.player("issuer", "child")
            except HostedStoreError:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(1)
        _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
