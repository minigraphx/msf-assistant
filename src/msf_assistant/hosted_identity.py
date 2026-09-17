"""Identity comes exclusively from the fixed MSF HTTPS userinfo endpoint."""

import math

from msf_assistant.auth import MSFOAuth2, TokenSet, bounded_response_json
from msf_assistant.config import DEFAULT_OAUTH_BASE_URL, Settings
from msf_assistant.hosted_transport import bounded_request_options

IDENTITY_RESPONSE_BYTES = 64 * 1024
ISSUER = "https://hydra-public.prod.m3.scopelypv.com/"


class MSFIdentity:
    def __init__(self, settings: Settings):
        if settings.oauth_base_url != DEFAULT_OAUTH_BASE_URL:
            raise ValueError("The official MSF issuer is required")
        if not math.isfinite(settings.request_timeout) or settings.request_timeout <= 0:
            raise ValueError("A finite positive timeout is required")
        self.oauth = MSFOAuth2(settings, max_response_bytes=IDENTITY_RESPONSE_BYTES)

    def begin(self, state: str) -> str:
        return self.oauth.authorization_url(state=state)[0]

    def exchange(self, code: str) -> tuple[str, str, TokenSet]:
        tokens = self.oauth.exchange_code(code)
        response = self.oauth.session.get(
            ISSUER + "userinfo",
            headers={"Authorization": "Bearer " + tokens.access_token},
            timeout=self.oauth.settings.request_timeout,
            **bounded_request_options(),
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("MSF userinfo response was not successful")
        payload = bounded_response_json(response, IDENTITY_RESPONSE_BYTES)
        subject = payload.get("sub") if isinstance(payload, dict) else None
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("MSF identity could not be verified")
        return ISSUER, subject, tokens
