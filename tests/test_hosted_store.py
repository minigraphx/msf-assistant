import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from msf_assistant.auth import TokenSet
from msf_assistant.hosted_store import HostedStore, HostedStoreError


@pytest.fixture
def key():
    return Fernet.generate_key()


@pytest.fixture
def store(tmp_path, key):
    return HostedStore(tmp_path / "private", key)


def test_separate_players_persist_across_reopen(store, key):
    alice = store.player("https://issuer.example/", "alice")
    bob = store.player("https://issuer.example/", "bob")
    tokens = TokenSet("alice-access", refresh_token="alice-refresh")
    store.save_tokens(alice.id, tokens)
    assert store.load_tokens(bob.id) is None
    reopened = HostedStore(store.root, key)
    assert reopened.player(alice.issuer, alice.subject) == alice
    assert reopened.load_tokens(alice.id) == tokens
    assert reopened.player("another-issuer", "alice").id != alice.id
    assert "alice-access" not in repr(tokens)
    assert "alice-refresh" not in repr(tokens)
    for path in store.root.rglob("*"):
        if path.is_file():
            assert b"alice-access" not in path.read_bytes()
            assert b"alice-refresh" not in path.read_bytes()


def test_concurrent_registration_is_unique(tmp_path, key):
    def register(_):
        return HostedStore(tmp_path / "private", key).player("issuer", "alice").id

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len(set(pool.map(register, range(24)))) == 1


@pytest.mark.parametrize("key", [None, b"", b"bad-key", "not-bytes"])
def test_invalid_key_rejected(tmp_path, key):
    with pytest.raises(HostedStoreError):
        HostedStore(tmp_path / "private", key)


def test_wrong_key_rejected_even_without_tokens(store):
    with pytest.raises(HostedStoreError):
        HostedStore(store.root, Fernet.generate_key())


def test_tampered_tokens_and_cross_player_ciphertext_rejected(store):
    alice = store.player("issuer", "alice")
    bob = store.player("issuer", "bob")
    store.save_tokens(alice.id, TokenSet("secret-access"))
    with store.transaction() as db:
        encrypted = db.execute("SELECT encrypted FROM msf_tokens").fetchone()[0]
        db.execute("INSERT INTO msf_tokens VALUES (?, ?)", (bob.id, encrypted))
    with pytest.raises(HostedStoreError):
        store.load_tokens(bob.id)
    with store.transaction() as db:
        db.execute("UPDATE msf_tokens SET encrypted = ?", (b"tampered-secret",))
    with pytest.raises(HostedStoreError) as error:
        store.load_tokens(alice.id)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("player_id", ["../escape", "/tmp/escape", str(uuid4())])
def test_unverified_ids_rejected(store, player_id):
    for operation in [
        store.require_player,
        store.player_dir,
        store.load_tokens,
        store.deactivate_player,
    ]:
        with pytest.raises(HostedStoreError):
            operation(player_id)
    with pytest.raises(HostedStoreError), store.player_lock(player_id):
        pass
    with pytest.raises(HostedStoreError):
        store.save_tokens(player_id, TokenSet("secret"))


def test_deactivation_revokes_old_id_and_tokens(store):
    player = store.player("issuer", "alice")
    store.save_tokens(player.id, TokenSet("secret"))
    store.deactivate_player(player.id)
    with pytest.raises(HostedStoreError):
        store.player_dir(player.id)
    with pytest.raises(HostedStoreError):
        store.load_tokens(player.id)
    replacement = store.player("issuer", "alice")
    assert replacement.id != player.id
    assert store.load_tokens(replacement.id) is None
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM msf_tokens").fetchone()[0] == 0


def test_rollback_foreign_keys_and_connection_closed(store):
    with pytest.raises(RuntimeError), store.transaction() as db:
        db.execute("INSERT INTO players VALUES (?, 'issuer', 'rolled-back', 1)", (str(uuid4()),))
        raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO msf_tokens VALUES (?, ?)", (str(uuid4()), b"ciphertext"))


def test_private_permissions_including_live_journal(store):
    player = store.player("issuer", "alice")
    store.player_dir(player.id)
    with store.player_lock(player.id), store.transaction() as db:
        db.execute("UPDATE players SET subject = 'changed'")
        for path in [store.root, *store.root.rglob("*")]:
            assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize("target", ["root", "database", "player", "locks"])
def test_symlinked_storage_rejected(tmp_path, key, target):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root = tmp_path / "private"
    if target == "root":
        root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(HostedStoreError):
            HostedStore(root, key)
        return
    store = HostedStore(root, key)
    player = store.player("issuer", "alice")
    path = {
        "database": store.database_path,
        "player": store.player_dir(player.id),
        "locks": root / "locks",
    }[target]
    if path.is_dir():
        path.rmdir()
    else:
        path.unlink()
    path.symlink_to(outside)
    with pytest.raises(HostedStoreError):
        if target == "database":
            HostedStore(root, key)
        elif target == "player":
            store.player_dir(player.id)
        else:
            with store.player_lock(player.id):
                pass


def test_unsafe_existing_permissions_rejected(tmp_path, key):
    root = tmp_path / "private"
    root.mkdir(mode=0o755)
    with pytest.raises(HostedStoreError):
        HostedStore(root, key)
    assert root.stat().st_mode & 0o777 == 0o755


def test_lock_serializes_and_rechecks_deactivation(store):
    player = store.player("issuer", "alice")
    started = Event()
    entered = Event()

    def wait_for_lock():
        started.set()
        with store.player_lock(player.id):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.player_lock(player.id):
            future = pool.submit(wait_for_lock)
            assert started.wait(2)
            assert not entered.wait(0.1)
            store.deactivate_player(player.id)
        with pytest.raises(HostedStoreError):
            future.result(timeout=3)
    assert not entered.is_set()


def test_lock_timeout_is_bounded(store):
    store.LOCK_TIMEOUT_SECONDS = 0.05
    player = store.player("issuer", "alice")
    with (
        store.player_lock(player.id),
        pytest.raises(HostedStoreError, match="busy"),
        store.player_lock(player.id),
    ):
        pytest.fail("A second lock cannot enter")


def test_missing_key_binding_fails_closed(store, key):
    with store.transaction() as db:
        db.execute("DELETE FROM key_binding")
    with pytest.raises(HostedStoreError):
        HostedStore(store.root, key)


@pytest.mark.parametrize("target", ["database", "journal", "player"])
def test_unsafe_preexisting_file_permissions_rejected(store, key, target):
    if target == "database":
        path = store.database_path
    elif target == "journal":
        path = Path(str(store.database_path) + "-journal")
        path.touch(mode=0o600)
    else:
        path = store.player_dir(store.player("issuer", "alice").id)
    path.chmod(0o777)
    with pytest.raises(HostedStoreError):
        if target == "player":
            store.player_dir(path.name)
        else:
            HostedStore(store.root, key)
    assert path.stat().st_mode & 0o777 == 0o777


def test_symlinked_ancestor_does_not_create_storage(tmp_path, key):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(HostedStoreError):
        HostedStore(link / "private", key)
    assert not (target / "private").exists()
