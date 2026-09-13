"""Environment-backed configuration without import-time side effects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Public shared header value, not a personal credential:
# https://developer.marvelstrikeforce.com/beta/msf-api.json (checked 2026-09-13).
DEFAULT_API_KEY = "17wMKJLRxy3pYDCKG5ciP7VSU45OVumB2biCzzgw"
DEFAULT_OAUTH_BASE_URL = "https://hydra-public.prod.m3.scopelypv.com/oauth2"


@dataclass(frozen=True, slots=True)
class Settings:
    """Credentials and endpoints required by the MSF API."""

    client_id: str
    api_key: str = DEFAULT_API_KEY
    redirect_uri: str = "http://localhost:8000/oauth/callback"
    api_base_url: str = "https://api.marvelstrikeforce.com"
    oauth_base_url: str = DEFAULT_OAUTH_BASE_URL
    request_timeout: float = 30.0
    client_secret: str | None = field(default=None, repr=False)

    @classmethod
    def token_store_identity_from_env(
        cls, env_file: str | None = ".env"
    ) -> tuple[str, str]:
        """Load only the stable identity used to namespace locally stored tokens."""
        if env_file:
            load_dotenv(env_file)
        client_id = os.getenv("MSF_CLIENT_ID", "").strip()
        if not client_id:
            raise ValueError("Missing required environment variable: MSF_CLIENT_ID")
        oauth_base_url = os.getenv("MSF_OAUTH_BASE_URL", DEFAULT_OAUTH_BASE_URL).rstrip("/")
        return client_id, oauth_base_url

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> Settings:
        """Load server-application credentials from an optional dotenv file."""
        if env_file:
            load_dotenv(env_file)
        client_id = os.getenv("MSF_CLIENT_ID", "").strip()
        client_secret = os.getenv("MSF_CLIENT_SECRET", "").strip()
        api_key = os.getenv("MSF_API_KEY", "").strip() or DEFAULT_API_KEY
        missing = [
            name
            for name, value in (
                ("MSF_CLIENT_ID", client_id), ("MSF_CLIENT_SECRET", client_secret)
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")
        try:
            timeout = float(os.getenv("MSF_REQUEST_TIMEOUT", "30"))
        except ValueError as exc:
            raise ValueError("MSF_REQUEST_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise ValueError("MSF_REQUEST_TIMEOUT must be greater than zero")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            api_key=api_key,
            redirect_uri=os.getenv(
                "MSF_REDIRECT_URI", "http://localhost:8000/oauth/callback"
            ).strip(),
            api_base_url=os.getenv("MSF_API_BASE_URL", "https://api.marvelstrikeforce.com").rstrip(
                "/"
            ),
            oauth_base_url=os.getenv(
                "MSF_OAUTH_BASE_URL", DEFAULT_OAUTH_BASE_URL
            ).rstrip("/"),
            request_timeout=timeout,
        )
