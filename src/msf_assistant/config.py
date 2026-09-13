"""Environment-backed configuration without import-time side effects."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Credentials and endpoints required by the MSF API."""

    client_id: str
    api_key: str
    redirect_uri: str = "http://localhost:8000/oauth/callback"
    api_base_url: str = "https://api.marvelstrikeforce.com"
    oauth_base_url: str = "https://hydra-public.prod.m3.scopelypv.com/oauth2"
    request_timeout: float = 30.0

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> Settings:
        """Load settings from an optional dotenv file and validate secrets."""
        if env_file:
            load_dotenv(env_file)
        client_id = os.getenv("MSF_CLIENT_ID", "").strip()
        api_key = os.getenv("MSF_API_KEY", "").strip()
        missing = [
            name
            for name, value in (("MSF_CLIENT_ID", client_id), ("MSF_API_KEY", api_key))
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
            api_key=api_key,
            redirect_uri=os.getenv(
                "MSF_REDIRECT_URI", "http://localhost:8000/oauth/callback"
            ).strip(),
            api_base_url=os.getenv("MSF_API_BASE_URL", "https://api.marvelstrikeforce.com").rstrip(
                "/"
            ),
            oauth_base_url=os.getenv(
                "MSF_OAUTH_BASE_URL", "https://hydra-public.prod.m3.scopelypv.com/oauth2"
            ).rstrip("/"),
            request_timeout=timeout,
        )
