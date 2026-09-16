"""Bounded, coherent backups. Installation secrets are backed up separately."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from msf_assistant.hosted_store import HostedStore

MARKER = "RESTORE_MAINTENANCE"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 20000
AUTH_TABLES = (
    "oauth_tokens",
    "oauth_codes",
    "oauth_requests",
    "oauth_grants",
    "oauth_clients",
    "browser_sessions",
)


def _allowed(name):
    path = PurePosixPath(name)
    if name == "hosted.sqlite3":
        return 128 * 1024 * 1024
    if len(path.parts) == 3 and path.parts[0] == "players":
        try:
            valid = str(UUID(path.parts[1])) == path.parts[1]
        except ValueError:
            valid = False
        if valid and path.parts[2] in ("snapshot.json", "context.json") and str(path) == name:
            return 24 * 1024 * 1024
    raise ValueError("Unexpected backup member")


def backup(store: HostedStore, directory: Path, *, retention: int = 7) -> Path:
    """Hold exclusive maintenance until both SQLite and player files are archived."""
    if not 1 <= retention <= 30:
        raise ValueError("Retention must be between 1 and 30")
    directory = Path(os.path.abspath(directory))
    if directory == store.root or store.root in directory.parents:
        raise ValueError("Backups must be outside the data root")
    store._directory(directory)
    name = datetime.now(UTC).strftime("backup-%Y%m%dT%H%M%S-") + uuid4().hex + ".tar"
    output = directory / name
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=directory) as temporary:
        staging = Path(temporary)
        archive = staging / "archive.tar"
        with store.maintenance(exclusive=True):
            store._validate_storage()
            database = staging / "hosted.sqlite3"
            database.touch(mode=0o600)
            with (
                closing(sqlite3.connect(store.database_path)) as source,
                closing(sqlite3.connect(database)) as target,
            ):
                source.backup(target)
            files = [(database, "hosted.sqlite3")]
            store._check(store.root / "players", directory=True)
            for player in (store.root / "players").iterdir():
                store._check(player, directory=True)
                for item in player.iterdir():
                    if item.name == ".context.json.lock":
                        store._file(item)
                        continue
                    name = item.relative_to(store.root).as_posix()
                    _allowed(name)
                    store._file(item)
                    files.append((item, name))
                    if len(files) > MAX_MEMBERS:
                        raise ValueError("Backup has too many files")
            total = 0
            with archive.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                    for item, name in files:
                        size = item.stat().st_size
                        total += size + 1024
                        if size > _allowed(name) or total > MAX_ARCHIVE_BYTES - 10240:
                            raise ValueError("Backup is too large")
                        info = tarfile.TarInfo(name)
                        info.size, info.mode = size, 0o600
                        with item.open("rb") as source:
                            tar.addfile(info, source)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(archive, output)  # Exclusive publication; never overwrite an archive.
            archive.unlink()
            archives = sorted(directory.glob("backup-*.tar"), key=lambda p: p.stat().st_mtime_ns)
            for old in archives[:-retention]:
                store._file(old)
                old.unlink()
    return output


def _schema(db):
    return db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()


def _validate_database(path, key, staging):
    # Only the exact supported schema is accepted, including indexes; reject
    # views/triggers/virtual tables before executing any restored SQL.
    from msf_assistant.hosted_oauth import HostedOAuthProvider
    from msf_assistant.hosted_web import BrowserSessions

    reference = HostedStore(staging / "schema", key)
    with closing(sqlite3.connect(reference.database_path)) as db:
        core_schema = _schema(db)
    HostedOAuthProvider(reference, "https://restore.invalid")
    with closing(sqlite3.connect(reference.database_path)) as db:
        oauth_schema = _schema(db)
    BrowserSessions(reference)
    with (
        closing(sqlite3.connect(reference.database_path)) as expected,
        closing(sqlite3.connect(path)) as db,
    ):
        db.execute("PRAGMA trusted_schema=OFF")
        actual = _schema(db)
        # A store backed up before first HTTP startup has only core tables.
        supported = _schema(expected)
        browser_schema = [
            row for row in supported if row in core_schema or row[2] == "browser_sessions"
        ]
        if actual not in (core_schema, oauth_schema, browser_schema, supported):
            raise ValueError("Unsupported backup schema")
        core = {"key_binding", "players", "msf_tokens"}
        tables = {row[1] for row in actual if row[0] == "table"}
        if not core <= tables:
            raise ValueError("Incomplete backup schema")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Backup database integrity failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Backup database references failed")
        for table in AUTH_TABLES:
            if table in tables:
                db.execute(f"DELETE FROM {table}")
        db.commit()
    shutil.rmtree(reference.root)


def restore(archive: Path, destination: Path, key: bytes, *, maintenance=False) -> HostedStore:
    """Restore to a new root; require reconciliation before a server can start."""
    if not maintenance:
        raise ValueError("Restore requires explicit maintenance mode")
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise ValueError("Restore destination must not exist")
    if any(p.is_symlink() for p in destination.parents):
        raise ValueError("Unsafe restore destination")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup is too large")
    destination.mkdir(mode=0o700)
    try:
        marker = destination / MARKER
        with marker.open("x") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write("Reconcile all account deletions since this backup before resuming.\n")
            stream.flush()
            os.fsync(stream.fileno())
        seen, total = set(), 0
        with tarfile.open(archive, "r:") as tar:
            for member in tar:
                limit = _allowed(member.name)
                total += member.size
                if (
                    not member.isfile()
                    or member.name in seen
                    or member.size < 0
                    or member.size > limit
                    or total > MAX_ARCHIVE_BYTES
                    or len(seen) >= MAX_MEMBERS
                ):
                    raise ValueError("Unsafe or oversized backup member")
                seen.add(member.name)
                output = destination / member.name
                if member.name.startswith("players/"):
                    (destination / "players").mkdir(exist_ok=True, mode=0o700)
                    output.parent.mkdir(exist_ok=True, mode=0o700)
                with tar.extractfile(member) as source, output.open("xb") as target:
                    os.fchmod(target.fileno(), 0o600)
                    shutil.copyfileobj(source, target, 1024 * 1024)
        if "hosted.sqlite3" not in seen:
            raise ValueError("Backup database is missing")
        _validate_database(destination / "hosted.sqlite3", key, destination)
        store = HostedStore(destination, key)
        with store.transaction() as db:
            ids = {row[0] for row in db.execute("SELECT id FROM players")}
        if any(name.split("/")[1] not in ids for name in seen if name.startswith("players/")):
            raise ValueError("Backup contains an unknown player")
        return store
    except BaseException:
        shutil.rmtree(destination)
        raise


def resume(store: HostedStore, *, deletions_reconciled=False):
    if not deletions_reconciled:
        raise ValueError("Post-backup deletions must be reconciled before resuming")
    with store.maintenance(exclusive=True):
        marker = store.root / MARKER
        store._file(marker)
        marker.unlink()
