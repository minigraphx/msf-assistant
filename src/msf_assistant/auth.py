"""OAuth2 authorization-code flow for Marvel Strike Force."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

import requests

from msf_assistant.config import Settings

DEFAULT_SCOPES = ("openid", "offline", "m3p.f.pr.pro", "m3p.f.pr.ros", "m3p.f.pr.inv")


class OAuthStateError(ValueError):
    """Raised when an OAuth callback does not match the originating request."""


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Tokens returned by the MSF OAuth server."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TokenSet:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("OAuth response did not contain an access_token")
        expires = payload.get("expires_in")
        return cls(
            access_token,
            str(payload.get("token_type", "Bearer")),
            int(expires) if expires is not None else None,
            payload.get("refresh_token"),
            payload.get("scope"),
        )


class MSFOAuth2:
    """Build authorization URLs and exchange or refresh tokens."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def authorization_url(
        self, *, state: str | None = None, scopes: tuple[str, ...] = DEFAULT_SCOPES
    ) -> tuple[str, str]:
        """Return the authorization URL and state that must be retained by the caller."""
        request_state = state or secrets.token_urlsafe(32)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "state": request_state,
                "scope": " ".join(scopes),
            }
        )
        return f"{self.settings.oauth_base_url}/auth?{query}", request_state

    @staticmethod
    def parse_callback(callback_url: str, *, expected_state: str) -> str:
        """Validate a callback URL and return its authorization code."""
        parameters = parse_qs(urlparse(callback_url).query)
        returned_state = parameters.get("state", [None])[0]
        if not returned_state or not secrets.compare_digest(returned_state, expected_state):
            raise OAuthStateError("OAuth callback state does not match")
        if error := parameters.get("error", [None])[0]:
            description = parameters.get("error_description", [error])[0]
            raise ValueError(f"OAuth authorization failed: {description}")
        code = parameters.get("code", [None])[0]
        if not code:
            raise ValueError("OAuth callback did not contain a code")
        return code

    def exchange_code(self, code: str) -> TokenSet:
        """Exchange a one-time authorization code for tokens."""
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        """Obtain a fresh access token from an OAuth refresh token."""
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    def _token_request(self, data: dict[str, str]) -> TokenSet:
        secret = self.settings.client_secret
        if not secret or not secret.strip():
            raise ValueError("MSF_CLIENT_SECRET is required for server-side OAuth")
        response = self.session.post(
            f"{self.settings.oauth_base_url}/token",
            data=data,
            # OAuth Basic credentials are form-encoded before Base64 encoding.
            auth=(quote_plus(self.settings.client_id), quote_plus(secret)),
            allow_redirects=False,
            timeout=self.settings.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OAuth response must be a JSON object")
        return TokenSet.from_payload(payload)
