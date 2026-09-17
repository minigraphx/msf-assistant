"""Typed HTTP client for the supported Marvel Strike Force resources."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

import requests

from msf_assistant.config import Settings
from msf_assistant.hosted_transport import bounded_request_options

JsonObject = dict[str, Any]
MAX_LEVEL = 200
MAX_YELLOW = 7
MAX_RED = 10  # 8-10 are diamonds
MAX_GEAR_TIER = 100
# One projection needs neither character metadata nor localized strings.
INSTANCE_PARAMS = {
    "lang": "none",
    "charInfo": "none",
    "abilityKits": "none",
    "gearTiers": "none",
    "pieceInfo": "none",
    "subPieceInfo": "none",
    "itemFormat": "id",
    "traitFormat": "id",
    "statsFormat": "object",
}


class MSFAPIError(RuntimeError):
    """Raised when an MSF endpoint returns an invalid response."""


class MSFAPIClient:
    """Small synchronous client for personal and static MSF data."""

    def __init__(
        self, settings: Settings, access_token: str, session: requests.Session | None = None,
        *, max_bytes: int | None = None
    ) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty")
        self.settings = settings
        self.remaining_bytes = max_bytes
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "APIClient/1.0 (Server)",
                "Authorization": f"Bearer {access_token}",
                "x-api-key": settings.api_key,
            }
        )

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> JsonObject:
        """Issue an authenticated GET and validate the JSON object response."""
        response = self.session.get(
            f"{self.settings.api_base_url}/{path.lstrip('/')}",
            params=params,
            timeout=self.settings.request_timeout,
            **(bounded_request_options() if self.remaining_bytes is not None else {}),
        )
        response.raise_for_status()
        try:
            if self.remaining_bytes is None:
                payload = response.json()
            else:
                raw = bytearray()
                try:
                    for chunk in response.iter_content(chunk_size=65536):
                        if len(chunk) > self.remaining_bytes:
                            raise MSFAPIError("MSF response is too large")
                        self.remaining_bytes -= len(chunk)
                        raw.extend(chunk)
                    payload = json.loads(raw)
                finally:
                    response.close()
        except (ValueError, UnicodeError) as exc:
            raise MSFAPIError("MSF API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MSFAPIError("MSF API response must be a JSON object")
        return payload

    def player_profile(self) -> JsonObject:
        """Return the authenticated player's profile/card."""
        return self.get("player/v1/card")

    def player_roster(self) -> JsonObject:
        """Return the authenticated player's character roster."""
        return self.get("player/v1/roster")

    def inventory(self) -> JsonObject:
        """Return the authenticated player's inventory."""
        return self.get("player/v1/inventory")

    def game_characters(
        self, *, ability_kits: str = "full", per_page: int = 10
    ) -> list[JsonObject]:
        """Fetch all pages of static game character data."""
        return list(self.iter_game_characters(ability_kits=ability_kits, per_page=per_page))

    def iter_game_characters(
        self, *, ability_kits: str = "full", per_page: int = 10
    ) -> Iterator[JsonObject]:
        """Yield static characters while transparently traversing numbered pages."""
        if per_page < 1:
            raise ValueError("per_page must be greater than zero")
        page = 1
        while True:
            payload = self.get(
                "game/v1/characters",
                params={"abilityKits": ability_kits, "page": page, "perPage": per_page},
            )
            records = payload.get("data", [])
            if not isinstance(records, list):
                raise MSFAPIError("MSF character response 'data' must be a list")
            for record in records:
                if not isinstance(record, dict):
                    raise MSFAPIError("MSF character entries must be JSON objects")
                yield record
            if len(records) < per_page:
                break
            page += 1

    def character_instance(
        self, character_id: str, *, level: int, yellow: int, red: int, gear_tier: int | str
    ) -> JsonObject:
        """Project stats and power for one build; gear_tier "all" returns the curve."""
        if not character_id or not isinstance(character_id, str):
            raise ValueError("character_id must not be empty")
        _check_range("level", level, 1, MAX_LEVEL)
        _check_range("yellow", yellow, 1, MAX_YELLOW)
        _check_range("red", red, 0, MAX_RED)
        if gear_tier != "all":
            _check_range("gear_tier", gear_tier, 1, MAX_GEAR_TIER)
        path = "/".join(
            str(part)
            for part in (
                "game/v1/characterInstances",
                quote(character_id, safe=""),
                level,
                yellow,
                red,
                gear_tier,
            )
        )
        return self.get(path, params=INSTANCE_PARAMS)


def _check_range(name: str, value: Any, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
