"""Compact stat/power projections; upstream objects are validated, never passed through."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from msf_assistant.client import MSFAPIClient, MSFAPIError

MAX_BUILDS = 100
ABILITIES = ("basic", "special", "ultimate", "passive")
BUILD_FIELDS = {
    "level": "level",
    "yellow": "activeYellow",
    "red": "activeRed",
    "gear_tier": "gearTier",
}


def project_character(
    client: MSFAPIClient,
    character_id: str,
    *,
    level: int,
    yellow: int,
    red: int,
    gear_tier: int | str,
) -> dict[str, Any]:
    """Return the projected builds for one character; one row, or a curve for "all"."""
    payload = client.character_instance(
        character_id, level=level, yellow=yellow, red=red, gear_tier=gear_tier
    )
    data = payload.get("data")
    rows = data if isinstance(data, list) else [data]
    if not rows:
        raise MSFAPIError("MSF projection response is empty")
    if len(rows) > MAX_BUILDS:
        raise MSFAPIError("MSF projection response has too many builds")
    return {
        "character_id": character_id,
        "builds": [compact_build(row) for row in rows],
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def compact_build(row: Any) -> dict[str, Any]:
    """Keep only build coordinates, ability levels, stats and power, all type-checked."""
    if not isinstance(row, dict) or not _is_int(row.get("power")):
        raise MSFAPIError("MSF projection response is invalid")
    stats = row.get("stats")
    if not isinstance(stats, dict) or not all(
        isinstance(key, str) and _is_int(value) for key, value in stats.items()
    ):
        raise MSFAPIError("MSF projection stats are invalid")
    coordinates = {name: row.get(source) for name, source in BUILD_FIELDS.items()}
    abilities = {name: row.get(name) for name in ABILITIES}
    for value in [*coordinates.values(), *abilities.values()]:
        if value is not None and not _is_int(value):
            raise MSFAPIError("MSF projection build fields are invalid")
    return {**coordinates, "abilities": abilities, "power": row["power"], "stats": dict(stats)}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
