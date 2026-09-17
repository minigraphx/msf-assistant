"""Bounded synchronous refresh, always called in a worker thread."""

from dataclasses import replace
from threading import BoundedSemaphore

import requests

from msf_assistant.auth import MSFOAuth2
from msf_assistant.cli import fetch_snapshot

SNAPSHOT_BYTES = 20 * 1024 * 1024


class HostedSyncError(RuntimeError):
    """Safe refresh failure."""


class HostedSync:
    """Messages are player-facing tool errors; they name the hosted login page,
    never local CLI commands, and never include upstream response content."""

    def __init__(self, store, settings, *, login_url="/login"):
        self.store = store
        self.settings = settings
        self.login_url = login_url
        self.capacity = BoundedSemaphore(2)

    def _relogin(self):
        return HostedSyncError(
            f"MSF-Anmeldung fehlt oder ist abgelaufen. Erneut anmelden unter {self.login_url}, "
            "danach refresh_data erneut ausführen. Vorherige Daten bleiben erhalten."
        )

    def refresh(self, player_id, *, admitted=False):
        acquired = admitted or self.capacity.acquire(blocking=False)
        if not acquired:
            raise HostedSyncError(
                "Aktualisierung ist gerade ausgelastet; in einer Minute erneut versuchen."
            )
        try:
            with self.store.player_lock(player_id):
                tokens = self.store.load_tokens(player_id)
                if tokens is None:
                    raise self._relogin()
                if tokens.refresh_token:
                    try:
                        with requests.Session() as session:
                            renewed = MSFOAuth2(
                                self.settings, session=session, max_response_bytes=64 * 1024
                            ).refresh(tokens.refresh_token)
                    except requests.HTTPError as exc:
                        status = getattr(exc.response, "status_code", None)
                        if status in (400, 401, 403):
                            raise self._relogin() from None
                        raise
                    tokens = replace(
                        renewed, refresh_token=renewed.refresh_token or tokens.refresh_token
                    )
                    self.store.save_tokens(player_id, tokens)
                fetch_snapshot(
                    self.settings,
                    tokens,
                    self.store.player_dir(player_id) / "snapshot.json",
                    characters=True,
                    max_bytes=SNAPSHOT_BYTES,
                )
        except HostedSyncError:
            raise
        except Exception:
            raise HostedSyncError(
                "Aktualisierung fehlgeschlagen; später erneut versuchen. "
                "Vorherige Daten bleiben erhalten."
            ) from None
        finally:
            if not admitted:
                self.capacity.release()
