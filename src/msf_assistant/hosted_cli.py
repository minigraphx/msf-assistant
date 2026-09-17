"""Explicit hosted commands; never load a local dotenv file or Keychain."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from msf_assistant.config import DEFAULT_OAUTH_BASE_URL, Settings
from msf_assistant.hosted_backup import MARKER, backup, restore, resume
from msf_assistant.hosted_pages import Operator, contains_protected_mark, service_name_from_env
from msf_assistant.hosted_store import HostedStore


def read_secret(path: Path) -> bytes:
    try:
        path = Path(os.path.abspath(path))
        if any(parent.is_symlink() for parent in path.parents):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 0 < info.st_size <= 16384
            ):
                raise ValueError
            value = stream.read(16385).strip()
            if not value or len(value) > 16384:
                raise ValueError
            return value
    except (OSError, ValueError):
        raise ValueError("A private service-owned 0600 secret file is required") from None


def generate_key(path: Path):
    path = Path(os.path.abspath(path))
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Unsafe key file path")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(Fernet.generate_key())
        stream.flush()
        os.fsync(stream.fileno())


def storage_config(env):
    root = Path(env.get("MSF_HOSTED_DATA", "/var/lib/msf-assistant/state"))
    mount = Path(env.get("MSF_HOSTED_MOUNT", "/var"))
    if (
        not root.is_absolute()
        or mount == Path("/")
        or ".." in root.parts
        or ".." in mount.parts
        or not mount.is_absolute()
        or root == mount
        or mount not in root.parents
        or not os.path.ismount(mount)
        or any(p.is_symlink() for p in (root, *root.parents))
    ):
        raise ValueError("Hosted data requires its configured persistent mount")
    key_file = env.get("MSF_HOSTED_KEY_FILE")
    if not key_file:
        raise ValueError("Encryption key secret file is required")
    if root in Path(os.path.abspath(key_file)).parents:
        raise ValueError("Encryption key must be outside the state root")
    key = read_secret(Path(key_file))
    try:
        Fernet(key)
    except (ValueError, TypeError):
        raise ValueError("Encryption key secret is invalid") from None
    return root, key


@dataclass(frozen=True)
class HostedConfig:
    root: Path
    public_url: str
    key: bytes = field(repr=False)
    settings: Settings = field(repr=False)
    operator: Operator
    service_name: str
    active_requests: int = 2

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        public = env.get("MSF_PUBLIC_URL", "")
        parsed = urlsplit(public)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in public)
            or parsed.port is not None
        ):
            raise ValueError("Public URL must be an HTTPS origin")
        public = public.rstrip("/")
        if contains_protected_mark(parsed.hostname):
            raise ValueError(
                "Public URL must not contain a Scopely or Marvel mark (MSF, Marvel, Strike "
                "Force, Scopely) per the MSF API Terms of Use"
            )
        if env.get("MSF_OAUTH_BASE_URL", DEFAULT_OAUTH_BASE_URL) != DEFAULT_OAUTH_BASE_URL:
            raise ValueError("The official MSF issuer is required")
        root, key = storage_config(env)
        client_id = env.get("MSF_CLIENT_ID", "").strip()
        secret_file = env.get("MSF_CLIENT_SECRET_FILE")
        if not client_id or not secret_file:
            raise ValueError("MSF client ID and secret file are required")
        if root in Path(os.path.abspath(secret_file)).parents:
            raise ValueError("Client secret must be outside the state root")
        settings = Settings(
            client_id=client_id,
            client_secret=read_secret(Path(secret_file)).decode("utf-8"),
            redirect_uri=public + "/oauth/callback",
        )
        active_requests = int(env.get("MSF_HOSTED_ACTIVE_REQUESTS", "2"))
        if not 1 <= active_requests <= 4:
            raise ValueError("Active requests must be between 1 and 4")
        return cls(
            root,
            public,
            key,
            settings,
            Operator.from_env(env),
            service_name_from_env(env),
            active_requests,
        )


def delete_player(root, key, player_id):
    """Operator deletion, identical to the account page: lock, deactivate, revoke, remove."""
    import anyio

    from msf_assistant.hosted_oauth import HostedOAuthProvider

    store = HostedStore(root, key)
    provider = HostedOAuthProvider(store, "https://operator.invalid")
    with store.player_lock(player_id):
        path = store.player_dir(player_id)
        store.deactivate_player(player_id)
        anyio.run(provider.revoke_player, player_id)
        shutil.rmtree(path)


def serve(config, *, host="127.0.0.1", port=8000):
    if os.path.lexists(config.root / MARKER):
        raise ValueError("Restore maintenance: reconcile deletions and resume before serving")
    import uvicorn

    from msf_assistant.hosted_identity import MSFIdentity
    from msf_assistant.hosted_oauth import HostedOAuthProvider
    from msf_assistant.hosted_server import HostedLimits, create_hosted_app

    store = HostedStore(config.root, config.key)
    provider = HostedOAuthProvider(store, config.public_url)
    app = create_hosted_app(
        store,
        provider,
        MSFIdentity(config.settings),
        config.settings,
        config.public_url,
        limits=HostedLimits(active_requests=config.active_requests),
        operator=config.operator,
        service_name=config.service_name,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        workers=1,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        log_level="warning",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="msf-assistant hosted")
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("generate-key", help="Create an exclusive private key file")
    key.add_argument("path", type=Path)
    server = commands.add_parser("serve", help="Serve one hosted application process")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    save = commands.add_parser("backup", help="Create a coherent private backup")
    save.add_argument("directory", type=Path)
    save.add_argument("--retention", type=int, default=7)
    save.add_argument("--max-age-days", type=int, default=30)
    recover = commands.add_parser("restore", help="Restore into a NEW root in maintenance")
    recover.add_argument("archive", type=Path)
    recover.add_argument("--maintenance", action="store_true", required=True)
    reopen = commands.add_parser("resume", help="Release restored-data maintenance")
    reopen.add_argument("--deletions-reconciled", action="store_true", required=True)
    remove = commands.add_parser("delete-player", help="Delete one player's data and grants")
    remove.add_argument("player_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-key":
            generate_key(args.path)
            print("Private encryption key file created.")
        elif args.command == "serve":
            serve(HostedConfig.from_env(), host=args.host, port=args.port)
        else:
            root, key = storage_config(os.environ)
            if args.command == "restore":
                restore(args.archive, root, key, maintenance=args.maintenance)
                print("Restored in maintenance. Reconcile post-backup deletions before resume.")
            elif args.command == "backup":
                print(
                    backup(
                        HostedStore(root, key),
                        args.directory,
                        retention=args.retention,
                        max_age_days=args.max_age_days,
                    )
                )
            elif args.command == "delete-player":
                delete_player(root, key, args.player_id)
                print("Player deleted; connections revoked.")
            else:
                resume(HostedStore(root, key), deletions_reconciled=args.deletions_reconciled)
                print("Restore maintenance released.")
        return 0
    except (sqlite3.Error, tarfile.TarError):
        print("Hosted operation failed: invalid backup or database", file=sys.stderr)
        return 1
    except (ValueError, OSError, RuntimeError) as exc:
        # Exception details can contain paths but never credential values.
        print(f"Hosted operation failed: {exc}", file=sys.stderr)
        return 1
