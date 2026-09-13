"""Safe, local queries over an atomically replaced MSF snapshot."""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
_STALE_AFTER = timedelta(hours=24)


class SnapshotError(RuntimeError):
    """A snapshot problem whose message is safe to show to a client."""


def validate_snapshot(snapshot: Any) -> None:
    """Validate an assembled snapshot in memory without reading or writing files."""
    try:
        if not isinstance(snapshot, dict):
            raise TypeError
        _reject_nonfinite(snapshot)
        _validate_timestamp(snapshot["retrieved_at"])
        _validate_envelope(snapshot["profile"], dict)
        _validate_envelope(snapshot["roster"], list)
        _validate_envelope(snapshot["inventory"], list)
        if not all(_valid_identified(row) for row in snapshot["roster"]["data"]):
            raise TypeError
        for row in snapshot["inventory"]["data"]:
            if not isinstance(row, dict) or "item" not in row:
                raise TypeError
        characters = snapshot.get("characters")
        if characters is not None:
            if not isinstance(characters, list) or not all(
                _valid_identified(row) for row in characters
            ):
                raise TypeError
            _validate_timestamp(snapshot["characters_retrieved_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotError("The snapshot data is malformed") from exc


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nonfinite(item)


def _validate_envelope(value: Any, data_type: type) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("data"), data_type):
        raise TypeError
    if "meta" in value and not isinstance(value["meta"], dict):
        raise TypeError


def _valid_identified(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("id"), str) and bool(value["id"])


def _validate_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed


class SnapshotReader:
    """Read and query a snapshot, reopening it for every operation."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def status(self) -> JsonObject:
        if not self.path.is_file():
            return {"available": False}
        snapshot = self._load()
        retrieved = self._freshness(snapshot["retrieved_at"])
        characters = snapshot.get("characters")
        character_time = snapshot.get("characters_retrieved_at")
        character_freshness = self._freshness(character_time) if characters is not None else None
        return {
            "available": True,
            "profile_available": True,
            "roster_count": len(snapshot["roster"]["data"]),
            "inventory_count": len(snapshot["inventory"]["data"]),
            "characters_count": len(characters) if characters is not None else 0,
            **retrieved,
            "characters_retrieved_at": character_time,
            "characters_age_seconds": (
                character_freshness["age_seconds"] if character_freshness else None
            ),
            "characters_stale": character_freshness["stale"] if character_freshness else None,
        }

    def profile(self) -> JsonObject:
        snapshot = self._load()
        return {
            "data": copy.deepcopy(snapshot["profile"]["data"]),
            **self._freshness(snapshot["retrieved_at"]),
        }

    def roster(self, query: str = "", offset: int = 0, limit: int = 50) -> JsonObject:
        self._validate_page(offset, limit)
        snapshot = self._load()
        catalogue = {
            item["id"].casefold(): item
            for item in snapshot.get("characters") or []
            if isinstance(item.get("id"), str)
        }
        rows = copy.deepcopy(snapshot["roster"]["data"])
        for row in rows:
            character = catalogue.get(row["id"].casefold())
            if character and isinstance(character.get("name"), str):
                row.setdefault("name", character["name"])
        needle = self._query(query)
        if needle:
            rows = [row for row in rows if self._matches_id_name(row, needle)]
        rows.sort(key=lambda row: (-self._power(row.get("power")), row["id"].casefold()))
        return self._page(rows, offset, limit, snapshot["retrieved_at"])

    def inventory(self, query: str = "", offset: int = 0, limit: int = 50) -> JsonObject:
        self._validate_page(offset, limit)
        snapshot = self._load()
        rows = copy.deepcopy(snapshot["inventory"]["data"])
        needle = self._query(query)
        if needle:
            rows = [row for row in rows if self._item_matches(row["item"], needle)]
        return self._page(rows, offset, limit, snapshot["retrieved_at"])

    def characters(self, query: str = "", offset: int = 0, limit: int = 50) -> JsonObject:
        self._validate_page(offset, limit)
        snapshot = self._load()
        rows, retrieved_at = self._catalogue(snapshot)
        needle = self._query(query)
        if needle:
            rows = [row for row in rows if self._matches_id_name(row, needle)]
        summaries = [
            {
                key: copy.deepcopy(row[key])
                for key in ("id", "name", "description", "traits", "status", "unlockStars")
                if key in row
            }
            for row in rows
        ]
        return self._page(summaries, offset, limit, retrieved_at)

    def character(self, character_id: str) -> JsonObject:
        if not isinstance(character_id, str) or not character_id.strip():
            raise SnapshotError("character_id must be a non-empty string")
        snapshot = self._load()
        characters, characters_retrieved_at = self._catalogue(snapshot)
        key = character_id.strip().casefold()
        character = next((row for row in characters if row["id"].casefold() == key), None)
        if character is None:
            raise SnapshotError("Character not found in the local catalogue")
        roster = next(
            (row for row in snapshot["roster"]["data"] if row["id"].casefold() == key), None
        )
        catalogue_freshness = self._freshness(characters_retrieved_at)
        roster_freshness = self._freshness(snapshot["retrieved_at"])
        return {
            "data": copy.deepcopy(character),
            "roster": copy.deepcopy(roster),
            **catalogue_freshness,
            "roster_retrieved_at": roster_freshness["retrieved_at"],
            "roster_age_seconds": roster_freshness["age_seconds"],
            "roster_stale": roster_freshness["stale"],
        }

    def _load(self) -> JsonObject:
        try:
            with self.path.open(encoding="utf-8") as handle:
                snapshot = json.load(
                    handle,
                    parse_constant=self._reject_constant,
                    parse_float=self._parse_float,
                )
        except FileNotFoundError as exc:
            raise SnapshotError(
                "No local snapshot is available; run login, then sync to create one"
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise SnapshotError(
                "The local snapshot is malformed or unreadable; run sync again"
            ) from exc
        try:
            validate_snapshot(snapshot)
        except SnapshotError as exc:
            raise SnapshotError(
                "The local snapshot is malformed or unreadable; run sync again"
            ) from exc
        return snapshot

    @staticmethod
    def _reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    @staticmethod
    def _parse_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    def _freshness(self, timestamp: str) -> JsonObject:
        parsed = _validate_timestamp(timestamp)
        age = max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
        return {
            "retrieved_at": timestamp,
            "age_seconds": age,
            "stale": age > _STALE_AFTER.total_seconds(),
        }

    @staticmethod
    def _validate_page(offset: int, limit: int) -> None:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise SnapshotError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SnapshotError("limit must be an integer from 1 through 100")

    @staticmethod
    def _query(query: str) -> str:
        if not isinstance(query, str):
            raise SnapshotError("query must be a string")
        return query.strip().casefold()

    @staticmethod
    def _matches_id_name(row: JsonObject, needle: str) -> bool:
        return any(
            needle in value.casefold()
            for value in (row.get("id"), row.get("name"))
            if isinstance(value, str)
        )

    @classmethod
    def _item_matches(cls, item: Any, needle: str) -> bool:
        if isinstance(item, str):
            return needle in item.casefold()
        if isinstance(item, dict):
            for key, value in item.items():
                if (
                    key.casefold() in {"id", "name"}
                    and isinstance(value, str)
                    and needle in value.casefold()
                ):
                    return True
                if isinstance(value, (dict, list)) and cls._item_matches(value, needle):
                    return True
        elif isinstance(item, list):
            return any(cls._item_matches(value, needle) for value in item)
        return False

    @staticmethod
    def _power(value: Any) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return 0
        return float(value)

    def _page(self, rows: list[JsonObject], offset: int, limit: int, timestamp: str) -> JsonObject:
        total = len(rows)
        next_offset = offset + limit if offset + limit < total else None
        return {
            "data": rows[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset,
            **self._freshness(timestamp),
        }

    @staticmethod
    def _catalogue(snapshot: JsonObject) -> tuple[list[JsonObject], str]:
        characters = snapshot.get("characters")
        retrieved_at = snapshot.get("characters_retrieved_at")
        if characters is None or retrieved_at is None:
            raise SnapshotError("Character catalogue is unavailable; run sync --characters")
        return characters, retrieved_at
