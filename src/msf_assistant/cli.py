"""Local browser login and private MSF snapshots, without a chat interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from msf_assistant.auth import MSFOAuth2, TokenSet
from msf_assistant.client import MSFAPIClient, MSFAPIError
from msf_assistant.config import Settings
from msf_assistant.login import LoginError, browser_login
from msf_assistant.token_store import KeychainTokenStore, TokenStoreError


class SyncError(RuntimeError):
    """A safe-to-display resource download failure."""


def write_snapshot(output: Path, payload: dict[str, Any]) -> None:
    """Replace a snapshot atomically with an owner-only file; never store tokens."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".msf-",
            delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def fetch_snapshot(settings: Settings, tokens: TokenSet, output: Path) -> None:
    """Fetch all resources before replacing an existing snapshot."""
    payload: dict[str, Any] = {}
    with requests.Session() as session:
        client = MSFAPIClient(settings, tokens.access_token, session=session)
        for name, fetch in (
            ("profile", client.player_profile),
            ("roster", client.player_roster),
            ("inventory", client.inventory),
        ):
            try:
                payload[name] = fetch()
            except (requests.RequestException, MSFAPIError):
                raise SyncError(
                    f"MSF-Abruf für {name} fehlgeschlagen. Freigabe und Verbindung prüfen; "
                    "bei abgelaufener Anmeldung login erneut starten."
                ) from None
    payload["retrieved_at"] = datetime.now(UTC).isoformat()
    write_snapshot(output, payload)


def _refresh(settings: Settings, tokens: TokenSet, store: KeychainTokenStore) -> TokenSet:
    if not tokens.refresh_token:
        return tokens
    try:
        with requests.Session() as session:
            refreshed = MSFOAuth2(settings, session=session).refresh(tokens.refresh_token)
    except (requests.RequestException, ValueError):
        raise LoginError(
            "Token-Erneuerung bei MSF fehlgeschlagen. Bitte login erneut starten."
        ) from None
    if not refreshed.refresh_token:
        refreshed = replace(refreshed, refresh_token=tokens.refresh_token)
    store.save(refreshed)
    return refreshed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokaler MSF-Login und privater Roster-Abruf")
    parser.add_argument("--env-file", default=".env", help="Lokale Konfigurationsdatei")
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="MSF-Anmeldung im Browser und erster Datenabruf")
    login.add_argument("--no-browser", action="store_true", help="Startseite selbst öffnen")
    login.add_argument(
        "--no-save", action="store_true", help="Tokens nur im Arbeitsspeicher halten"
    )
    login.add_argument("--timeout", type=float, default=300, help="Anmeldefrist in Sekunden")
    sync = commands.add_parser("sync", help="Gespeicherte Anmeldung nutzen und Daten aktualisieren")
    for command in (login, sync):
        command.add_argument(
            "--output",
            type=Path,
            default=Path("outputs/msf-snapshot.json"),
            help="Private lokale JSON-Ausgabedatei",
        )
    commands.add_parser("logout", help="Lokale Tokens aus dem macOS-Schlüsselbund entfernen")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "logout":
        try:
            client_id, oauth_base_url = Settings.token_store_identity_from_env(args.env_file)
            KeychainTokenStore(client_id, oauth_base_url).clear()
            print(
                "Lokale Tokens entfernt. Die Freigabe bei MSF und Ausgabedateien bleiben bestehen."
            )
            return 0
        except ValueError:
            print("Konfiguration ungültig: MSF_CLIENT_ID in .env prüfen.", file=sys.stderr)
        except TokenStoreError as exc:
            print(str(exc), file=sys.stderr)
        return 1
    try:
        settings = Settings.from_env(env_file=args.env_file)
    except ValueError:
        print(
            "Konfiguration ungültig: MSF_CLIENT_ID, MSF_CLIENT_SECRET und Timeout in .env prüfen.",
            file=sys.stderr,
        )
        return 1
    try:
        store = (
            None
            if args.command == "login" and args.no_save
            else KeychainTokenStore(settings.client_id, settings.oauth_base_url)
        )
        if args.command == "login":
            tokens = browser_login(
                settings,
                open_browser=not args.no_browser,
                timeout=args.timeout,
                on_ready=lambda url: print(f"Anmeldeseite bereit: {url}", flush=True),
            )
            if store is not None:
                store.save(tokens)
                print("Anmeldung im macOS-Schlüsselbund gespeichert.", flush=True)
            else:
                print("Tokens werden nur für diesen Lauf verwendet.", flush=True)
        else:
            tokens = store.load()
            if tokens is None:
                raise LoginError(
                    "Keine gespeicherte Anmeldung. Zuerst msf-assistant login starten."
                )
            tokens = _refresh(settings, tokens, store)
        fetch_snapshot(settings, tokens, args.output)
        print(f"Profil, Roster und Inventar gespeichert: {args.output.resolve()}")
        return 0
    except (LoginError, TokenStoreError, SyncError) as exc:
        print(str(exc), file=sys.stderr)
    except requests.RequestException:
        print(
            "Token-Erneuerung bei MSF fehlgeschlagen. Bitte login erneut starten.", file=sys.stderr
        )
    except (OSError, ValueError):
        print("Die lokale Ausgabedatei konnte nicht sicher geschrieben werden.", file=sys.stderr)
    except KeyboardInterrupt:
        print("Anmeldung abgebrochen.", file=sys.stderr)
        return 130
    return 1
