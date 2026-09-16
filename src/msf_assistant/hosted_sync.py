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
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.capacity = BoundedSemaphore(2)

    def refresh(self, player_id, *, admitted=False):
        acquired = admitted or self.capacity.acquire(blocking=False)
        if not acquired:
            raise HostedSyncError("Refresh capacity unavailable")
        try:
            with self.store.player_lock(player_id):
                tokens = self.store.load_tokens(player_id)
                if tokens is None:
                    raise HostedSyncError("Refresh failed; sign in again")
                if tokens.refresh_token:
                    with requests.Session() as session:
                        renewed = MSFOAuth2(
                            self.settings, session=session, max_response_bytes=64 * 1024
                        ).refresh(tokens.refresh_token)
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
        except Exception:
            raise HostedSyncError(
                "Refresh failed; retry or sign in again. Previous data is retained."
            ) from None
        finally:
            if not admitted:
                self.capacity.release()
