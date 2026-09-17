"""Run one authenticated MSF query, refreshing the token at most once on 401."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import requests

from msf_assistant.auth import MSFOAuth2, TokenSet
from msf_assistant.client import MSFAPIClient
from msf_assistant.config import Settings

TOKEN_RESPONSE_BYTES = 64 * 1024
QUERY_BYTES = 1024 * 1024  # one projection or catalog lookup
REFRESH_REJECTED = (400, 401, 403)


class CredentialsRejected(RuntimeError):
    """The stored sign-in no longer works; the player has to sign in again."""


def run_query(
    settings: Settings,
    tokens: TokenSet,
    fn: Callable[[MSFAPIClient], Any],
    *,
    save_tokens: Callable[[TokenSet], None],
    max_bytes: int | None = None,
) -> Any:
    """Call fn with a client; after a 401 refresh once, persist, and retry once."""
    budget = {"max_bytes": max_bytes} if max_bytes is not None else {}
    with requests.Session() as session:
        try:
            return fn(MSFAPIClient(settings, tokens.access_token, session=session, **budget))
        except requests.HTTPError as exc:
            if _status(exc) != 401:
                raise
            if not tokens.refresh_token:
                raise CredentialsRejected from None
        try:
            renewed = MSFOAuth2(
                settings, session=session, max_response_bytes=TOKEN_RESPONSE_BYTES
            ).refresh(tokens.refresh_token)
        except requests.HTTPError as exc:
            if _status(exc) in REFRESH_REJECTED:
                raise CredentialsRejected from None
            raise
        renewed = replace(renewed, refresh_token=renewed.refresh_token or tokens.refresh_token)
        save_tokens(renewed)
        try:
            return fn(MSFAPIClient(settings, renewed.access_token, session=session, **budget))
        except requests.HTTPError as exc:
            if _status(exc) == 401:
                raise CredentialsRejected from None
            raise


def _status(exc: requests.HTTPError) -> int | None:
    return getattr(exc.response, "status_code", None)
