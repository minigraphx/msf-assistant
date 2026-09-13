"""Serialize local account operations across CLI and MCP processes in one checkout."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SyncError(RuntimeError):
    """Safe-to-display download or local operation failure."""


@contextmanager
def operation_lock(env_file: Path) -> Iterator[None]:
    """Fail promptly if another login/refresh/logout owns this checkout's token store."""
    import fcntl

    directory = env_file.resolve().parent / "work"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / "msf-account.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError(
                "Ein Login oder Datenabruf läuft bereits. Danach erneut versuchen."
            ) from None
        yield
    finally:
        os.close(fd)
