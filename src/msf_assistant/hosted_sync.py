"""Bounded synchronous refresh, always called in a worker thread."""

import logging
from dataclasses import replace
from threading import BoundedSemaphore

import requests

from msf_assistant.auth import MSFOAuth2
from msf_assistant.cli import fetch_snapshot
from msf_assistant.live_query import QUERY_BYTES, CredentialsRejected, run_query

SNAPSHOT_BYTES = 20 * 1024 * 1024
logger = logging.getLogger("msf_assistant.hosted")


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

    def _relogin(self, action="run refresh_data again"):
        return HostedSyncError(
            f"Your MSF sign-in is missing or expired. Sign in again at {self.login_url}, "
            f"then {action}. Your previous data is retained."
        )

    def refresh(self, player_id, *, admitted=False):
        acquired = admitted or self.capacity.acquire(blocking=False)
        if not acquired:
            raise HostedSyncError("Refresh is busy right now; try again in a minute.")
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
        except Exception as exc:
            logger.warning("refresh failed: %s", type(exc).__name__)
            raise HostedSyncError(
                "Refresh failed; try again later. Your previous data is retained."
            ) from None
        finally:
            if not admitted:
                self.capacity.release()

    def query(self, player_id, fn):
        """Run fn(client) with the player's credentials; shares the refresh capacity."""
        if not self.capacity.acquire(blocking=False):
            raise HostedSyncError("MSF is busy right now; try again in a minute.")
        try:
            with self.store.player_lock(player_id):
                tokens = self.store.load_tokens(player_id)
                if tokens is None:
                    raise self._relogin("retry the query")
                try:
                    return run_query(
                        self.settings,
                        tokens,
                        fn,
                        save_tokens=lambda renewed: self.store.save_tokens(player_id, renewed),
                        max_bytes=QUERY_BYTES,
                    )
                except CredentialsRejected:
                    raise self._relogin("retry the query") from None
                except requests.HTTPError as exc:
                    if getattr(exc.response, "status_code", None) == 404:
                        raise HostedSyncError(
                            "MSF has no data for that request; check the character ID."
                        ) from None
                    raise
        except HostedSyncError:
            raise
        except Exception as exc:
            logger.warning("query failed: %s", type(exc).__name__)
            raise HostedSyncError("MSF query failed; try again later.") from None
        finally:
            self.capacity.release()
