"""Private SQLite identities and encrypted credentials for the hosted service.

``database_path`` identifies the database for SQLite's backup API. Backups must
hold the service-wide maintenance lock before acquiring any player locks.
Player lock files live outside deletable player data and must never be unlinked
while the service is running. Storage is owned exclusively by the service UID;
other processes running as that UID must be trusted.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken

from msf_assistant.auth import TokenSet


class HostedStoreError(ValueError):
    """Storage cannot safely satisfy a request (never includes credentials)."""


@dataclass(frozen=True)
class Player:
    id: str
    issuer: str
    subject: str


class HostedStore:
    """One service-owned private root; independent connections per transaction."""

    LOCK_TIMEOUT_SECONDS = 10.0
    _VERIFIER = b"msf-assistant-hosted-store-key-v1"

    def __init__(self, root: Path, key: bytes):
        try:
            if not isinstance(key, bytes) or not key:
                raise ValueError
            self._cipher = Fernet(key)
        except (ValueError, TypeError):
            raise HostedStoreError("A valid encryption key is required") from None
        self.root = Path(os.path.abspath(root))
        self.database_path = self.root / "hosted.sqlite3"
        self._directory(self.root)
        self._directory(self.root / "players")
        self._directory(self.root / "locks")
        self._file(self.database_path, create=True)
        with self._transaction(initialize=True):
            pass

    @staticmethod
    def _check(path: Path, *, directory: bool) -> None:
        try:
            for ancestor in path.parents:
                if ancestor.is_symlink():
                    raise HostedStoreError("Unsafe storage path")
            info = path.lstat()
            kind = stat.S_ISDIR if directory else stat.S_ISREG
            if (
                not kind(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                or (not directory and info.st_nlink != 1)
            ):
                raise HostedStoreError("Unsafe storage path or permissions")
        except OSError:
            raise HostedStoreError("Storage path is unavailable") from None

    def _directory(self, path: Path) -> None:
        if any(ancestor.is_symlink() for ancestor in path.parents):
            raise HostedStoreError("Unsafe storage path")
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise HostedStoreError("Storage directory is unavailable") from None
        self._check(path, directory=True)

    def _file(self, path: Path, *, create: bool = False) -> None:
        if create:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            except OSError:
                raise HostedStoreError("Storage file is unavailable") from None
            else:
                os.close(fd)
        self._check(path, directory=False)

    def _validate_storage(self) -> None:
        self._check(self.root, directory=True)
        self._file(self.database_path)
        for suffix in ("-journal", "-wal", "-shm"):
            side = Path(str(self.database_path) + suffix)
            if os.path.lexists(side):
                self._file(side)

    def _verify_key(self, db: sqlite3.Connection, *, initialize: bool) -> None:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables and initialize:
            db.execute(
                "CREATE TABLE key_binding (id INTEGER PRIMARY KEY CHECK (id=1), "
                "encrypted BLOB NOT NULL)"
            )
            db.execute(
                "INSERT INTO key_binding VALUES (1, ?)", (self._cipher.encrypt(self._VERIFIER),)
            )
            db.execute(
                "CREATE TABLE players (id TEXT PRIMARY KEY, issuer TEXT NOT NULL, "
                "subject TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)))"
            )
            db.execute(
                "CREATE UNIQUE INDEX active_identity ON players(issuer, subject) WHERE active=1"
            )
            db.execute(
                "CREATE TABLE msf_tokens (player_id TEXT PRIMARY KEY "
                "REFERENCES players(id), encrypted BLOB NOT NULL)"
            )
        try:
            row = db.execute("SELECT encrypted FROM key_binding WHERE id=1").fetchone()
            if row is None or self._cipher.decrypt(row[0]) != self._VERIFIER:
                raise ValueError
        except (sqlite3.Error, InvalidToken, TypeError, ValueError):
            raise HostedStoreError("Encryption key or storage verification failed") from None

    @contextmanager
    def _transaction(self, *, initialize: bool = False) -> Iterator[sqlite3.Connection]:
        self._validate_storage()
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            self._verify_key(db, initialize=initialize)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def transaction(self):
        """Fresh BEGIN IMMEDIATE transaction, also available to future OAuth tables."""
        return self._transaction()

    @staticmethod
    def _require(db: sqlite3.Connection, player_id: str) -> Player:
        try:
            if str(UUID(player_id)) != player_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise HostedStoreError("Unknown or inactive player") from None
        row = db.execute(
            "SELECT id, issuer, subject FROM players WHERE id=? AND active=1", (player_id,)
        ).fetchone()
        if row is None:
            raise HostedStoreError("Unknown or inactive player")
        return Player(*row)

    def player(self, issuer: str, subject: str) -> Player:
        if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
            raise HostedStoreError("Verified issuer and subject are required")
        with self.transaction() as db:
            row = db.execute(
                "SELECT id, issuer, subject FROM players WHERE issuer=? AND subject=? AND active=1",
                (issuer, subject),
            ).fetchone()
            if row is not None:
                return Player(*row)
            player = Player(str(uuid4()), issuer, subject)
            db.execute(
                "INSERT INTO players VALUES (?, ?, ?, 1)",
                (player.id, player.issuer, player.subject),
            )
            return player

    def require_player(self, player_id: str) -> Player:
        with self.transaction() as db:
            return self._require(db, player_id)

    def player_dir(self, player_id: str) -> Path:
        with self.transaction() as db:
            player = self._require(db, player_id)
            self._check(self.root / "players", directory=True)
            path = self.root / "players" / player.id
            self._directory(path)
            return path

    def save_tokens(self, player_id: str, tokens: TokenSet) -> None:
        with self.transaction() as db:
            self._require(db, player_id)
            encrypted = self._cipher.encrypt(
                json.dumps({"player_id": player_id, "tokens": asdict(tokens)}).encode()
            )
            db.execute(
                "INSERT INTO msf_tokens VALUES (?, ?) ON CONFLICT(player_id) "
                "DO UPDATE SET encrypted=excluded.encrypted",
                (player_id, encrypted),
            )

    def load_tokens(self, player_id: str) -> TokenSet | None:
        with self.transaction() as db:
            self._require(db, player_id)
            row = db.execute(
                "SELECT encrypted FROM msf_tokens WHERE player_id=?", (player_id,)
            ).fetchone()
            if row is None:
                return None
            try:
                payload = json.loads(self._cipher.decrypt(row[0]))
                if payload["player_id"] != player_id:
                    raise ValueError
                return TokenSet.from_payload(payload["tokens"])
            except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
                raise HostedStoreError("Stored credentials could not be verified") from None

    def deactivate_player(self, player_id: str) -> None:
        with self.transaction() as db:
            self._require(db, player_id)
            db.execute("DELETE FROM msf_tokens WHERE player_id=?", (player_id,))
            db.execute("UPDATE players SET active=0 WHERE id=?", (player_id,))

    @contextmanager
    def player_lock(self, player_id: str) -> Iterator[None]:
        """Bounded cross-process lock; acquire maintenance lock before this lock.

        Never hold a SQLite transaction while waiting for a player lock. Callers
        must hold this lock around sync/delete. Deactivation remains atomic and
        deliberately does not acquire this lock, allowing deletion to hold it.
        """
        self.require_player(player_id)
        lock_dir = self.root / "locks"
        self._check(lock_dir, directory=True)
        path = lock_dir / (player_id + ".lock")
        self._file(path, create=True)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            deadline = time.monotonic() + self.LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise HostedStoreError("Player storage is busy") from None
                    time.sleep(0.025)
            self.require_player(player_id)
            yield
        finally:
            os.close(fd)
