"""Bounded, private persistence for advisor goals, facts, and recommendations."""

from __future__ import annotations

import copy
import json
import math
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

JsonObject = dict[str, Any]

SCHEMA_VERSION = 1
_MAX_FILE_BYTES = 1_000_000
_MAX_GOALS = 200
_MAX_FACTS = 500
_MAX_RECOMMENDATIONS = 200
_MAX_SOURCES = 30
_MAX_PLANS = 100
_GOAL_STATUSES = {"proposed", "selected", "paused", "completed"}
_TOP_LEVEL_FIELDS = {"schema_version", "revision", "goals", "facts", "recommendations"}
_GOAL_FIELDS = {
    "id",
    "title",
    "description",
    "status",
    "provenance",
    "created_at",
    "updated_at",
}
_FACT_FIELDS = {"id", "key", "value", "provenance", "created_at", "updated_at"}
_RECOMMENDATION_FIELDS = {
    "id",
    "goal_ids",
    "summary",
    "roster_retrieved_at",
    "sources",
    "character_plans",
    "uncertainty",
    "provenance",
    "created_at",
    "updated_at",
}
_SOURCE_FIELDS = {"title", "url", "retrieved_at"}
_PLAN_FIELDS = {
    "character_id",
    "current_level",
    "current_gear_tier",
    "target_level",
    "target_gear_tier",
    "priority",
    "rationale",
    "locked",
}


class ContextError(RuntimeError):
    """An advisor-context problem with a message safe to show to callers."""


class ContextConflictError(ContextError):
    """A write based on a stale revision."""


class ContextCommitUncertainError(ContextError):
    """The revision is visible, but syncing its directory could not be confirmed."""


def _empty_context() -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "goals": [],
        "facts": [],
        "recommendations": [],
    }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value.strip())
    ):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ContextError(f"{field} must be {qualifier} of at most {maximum} characters")
    return value


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContextError(f"{field} must be an integer from {minimum} through {maximum}")
    return value


def _optional_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, field, minimum=minimum, maximum=maximum)


def _datetime(value: Any, field: str) -> str:
    value = _text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextError(f"{field} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextError(f"{field} must be a timezone-aware ISO 8601 timestamp")
    return value


def _exact_fields(value: Any, expected: set[str], label: str) -> JsonObject:
    if not isinstance(value, dict) or set(value) != expected:
        raise ContextError(f"The advisor context is malformed ({label} fields)")
    return value


def _sequence(value: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContextError(f"{field} must be a list with at most {maximum} entries")
    return value


def _validate_goal(value: Any) -> JsonObject:
    goal = _exact_fields(value, _GOAL_FIELDS, "goal")
    _text(goal["id"], "goal id", maximum=128)
    _text(goal["title"], "goal title", maximum=300)
    _text(goal["description"], "goal description", maximum=5_000, allow_empty=True)
    if goal["status"] not in _GOAL_STATUSES:
        raise ContextError("goal status must be proposed, selected, paused, or completed")
    _text(goal["provenance"], "goal provenance", maximum=1_000)
    _datetime(goal["created_at"], "goal created_at")
    _datetime(goal["updated_at"], "goal updated_at")
    return goal


def _validate_fact_value(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _text(value, "fact value", maximum=5_000, allow_empty=True)
        return
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 10**15:
        return
    if isinstance(value, float) and math.isfinite(value) and abs(value) <= 10**15:
        return
    raise ContextError("fact value must be a bounded scalar or null for explicitly unknown")


def _validate_fact(value: Any) -> JsonObject:
    fact = _exact_fields(value, _FACT_FIELDS, "fact")
    _text(fact["id"], "fact id", maximum=128)
    _text(fact["key"], "fact key", maximum=200)
    _validate_fact_value(fact["value"])
    _text(fact["provenance"], "fact provenance", maximum=1_000)
    _datetime(fact["created_at"], "fact created_at")
    _datetime(fact["updated_at"], "fact updated_at")
    return fact


def _validate_source(value: Any) -> JsonObject:
    source = _exact_fields(value, _SOURCE_FIELDS, "source")
    _text(source["title"], "source title", maximum=500)
    url = _text(source["url"], "source url", maximum=2_000)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContextError("source url must be an absolute HTTP(S) URL")
    _datetime(source["retrieved_at"], "source retrieved_at")
    return source


def _validate_plan(value: Any) -> JsonObject:
    plan = _exact_fields(value, _PLAN_FIELDS, "character plan")
    _text(plan["character_id"], "character plan character_id", maximum=200)
    _optional_integer(plan["current_level"], "current_level", minimum=1, maximum=200)
    _optional_integer(plan["current_gear_tier"], "current_gear_tier", minimum=1, maximum=100)
    _integer(plan["target_level"], "target_level", minimum=1, maximum=200)
    _integer(plan["target_gear_tier"], "target_gear_tier", minimum=1, maximum=100)
    _integer(plan["priority"], "priority", minimum=1, maximum=1_000)
    _text(plan["rationale"], "character plan rationale", maximum=2_000)
    if not isinstance(plan["locked"], bool):
        raise ContextError("character plan locked must be a boolean")
    return plan


def _validate_recommendation(value: Any) -> JsonObject:
    recommendation = _exact_fields(value, _RECOMMENDATION_FIELDS, "recommendation")
    _text(recommendation["id"], "recommendation id", maximum=128)
    goal_ids = _sequence(recommendation["goal_ids"], "goal_ids", _MAX_GOALS)
    if len(set(goal_ids)) != len(goal_ids):
        raise ContextError("goal_ids must not contain duplicates")
    for goal_id in goal_ids:
        _text(goal_id, "goal id", maximum=128)
    _text(recommendation["summary"], "recommendation summary", maximum=10_000)
    _datetime(recommendation["roster_retrieved_at"], "roster timestamp")
    for source in _sequence(recommendation["sources"], "sources", _MAX_SOURCES):
        _validate_source(source)
    for plan in _sequence(recommendation["character_plans"], "character_plans", _MAX_PLANS):
        _validate_plan(plan)
    _text(
        recommendation["uncertainty"],
        "recommendation uncertainty",
        maximum=5_000,
        allow_empty=True,
    )
    _text(recommendation["provenance"], "recommendation provenance", maximum=1_000)
    _datetime(recommendation["created_at"], "recommendation created_at")
    _datetime(recommendation["updated_at"], "recommendation updated_at")
    return recommendation


def _validate_context(value: Any) -> JsonObject:
    context = _exact_fields(value, _TOP_LEVEL_FIELDS, "top-level")
    if context["schema_version"] != SCHEMA_VERSION:
        raise ContextError("The advisor context uses an unsupported schema version")
    revision = context["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ContextError("The advisor context is malformed (revision)")
    goals = _sequence(context["goals"], "goals", _MAX_GOALS)
    facts = _sequence(context["facts"], "facts", _MAX_FACTS)
    recommendations = _sequence(context["recommendations"], "recommendations", _MAX_RECOMMENDATIONS)
    for goal in goals:
        _validate_goal(goal)
    for fact in facts:
        _validate_fact(fact)
    for recommendation in recommendations:
        _validate_recommendation(recommendation)
    all_records = [*goals, *facts, *recommendations]
    ids = [record["id"] for record in all_records]
    if len(set(ids)) != len(ids):
        raise ContextError("The advisor context is malformed (duplicate record id)")
    goal_id_set = {goal["id"] for goal in goals}
    if any(
        goal_id not in goal_id_set
        for recommendation in recommendations
        for goal_id in recommendation["goal_ids"]
    ):
        raise ContextError("The advisor context is malformed (unknown goal reference)")
    return context


class ContextStore:
    """Read and atomically mutate one fixed advisor-context file."""

    def __init__(self, path: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes

    def read(self) -> JsonObject:
        """Return a detached validated context, or an empty context when absent."""
        return copy.deepcopy(self._read_unlocked())

    def save_goal(
        self,
        *,
        expected_revision: int,
        title: str,
        description: str,
        status: str,
        provenance: str,
        record_id: str | None = None,
    ) -> JsonObject:
        """Create or correct one goal and return the updated full context."""
        now = _timestamp()
        candidate = {
            "id": self._record_id(record_id),
            "title": title,
            "description": description,
            "status": status,
            "provenance": provenance,
            "created_at": now,
            "updated_at": now,
        }
        _validate_goal(candidate)
        return self._save_record("goals", candidate, expected_revision, record_id)

    def save_player_fact(
        self,
        *,
        expected_revision: int,
        key: str,
        value: str | int | float | bool | None,
        provenance: str,
        record_id: str | None = None,
    ) -> JsonObject:
        """Create or correct one bounded scalar player fact."""
        now = _timestamp()
        candidate = {
            "id": self._record_id(record_id),
            "key": key,
            "value": value,
            "provenance": provenance,
            "created_at": now,
            "updated_at": now,
        }
        _validate_fact(candidate)
        return self._save_record("facts", candidate, expected_revision, record_id)

    def save_recommendation(
        self,
        *,
        expected_revision: int,
        goal_ids: list[str],
        summary: str,
        roster_retrieved_at: str,
        sources: list[JsonObject],
        character_plans: list[JsonObject],
        uncertainty: str,
        provenance: str,
        record_id: str | None = None,
    ) -> JsonObject:
        """Create or correct a recommendation with evidence and a structured build plan."""
        now = _timestamp()
        candidate = {
            "id": self._record_id(record_id),
            "goal_ids": copy.deepcopy(goal_ids),
            "summary": summary,
            "roster_retrieved_at": roster_retrieved_at,
            "sources": copy.deepcopy(sources),
            "character_plans": copy.deepcopy(character_plans),
            "uncertainty": uncertainty,
            "provenance": provenance,
            "created_at": now,
            "updated_at": now,
        }
        _validate_recommendation(candidate)

        def mutate(context: JsonObject) -> None:
            known_goals = {goal["id"] for goal in context["goals"]}
            if set(candidate["goal_ids"]) - known_goals:
                raise ContextError(
                    "Recommendation references an unknown goal; read context and use a saved "
                    "goal id"
                )
            self._put_record(context["recommendations"], candidate, record_id)

        return self._mutate(expected_revision, mutate)

    def delete(self, *, record_type: str, record_id: str, expected_revision: int) -> JsonObject:
        """Delete one record, rejecting goal removals that would leave dangling links."""
        collections = {
            "goal": "goals",
            "fact": "facts",
            "recommendation": "recommendations",
        }
        if record_type not in collections:
            raise ContextError("record_type must be goal, fact, or recommendation")
        _text(record_id, "record_id", maximum=128)

        def mutate(context: JsonObject) -> None:
            collection = context[collections[record_type]]
            index = next(
                (index for index, record in enumerate(collection) if record["id"] == record_id),
                None,
            )
            if index is None:
                raise ContextError("Advisor record not found; read context and use an existing id")
            if record_type == "goal" and any(
                record_id in recommendation["goal_ids"]
                for recommendation in context["recommendations"]
            ):
                raise ContextError(
                    "Goal is referenced by a recommendation; update or delete that "
                    "recommendation first"
                )
            del collection[index]

        return self._mutate(expected_revision, mutate)

    def _save_record(
        self,
        collection_name: str,
        candidate: JsonObject,
        expected_revision: int,
        record_id: str | None,
    ) -> JsonObject:
        return self._mutate(
            expected_revision,
            lambda context: self._put_record(context[collection_name], candidate, record_id),
        )

    @staticmethod
    def _put_record(
        records: list[JsonObject], candidate: JsonObject, record_id: str | None
    ) -> None:
        if record_id is None:
            records.append(candidate)
            return
        index = next(
            (index for index, record in enumerate(records) if record["id"] == record_id), None
        )
        if index is None:
            raise ContextError("Advisor record not found; read context and use an existing id")
        candidate["created_at"] = records[index]["created_at"]
        records[index] = candidate

    def _mutate(self, expected_revision: int, mutate: Callable[[JsonObject], None]) -> JsonObject:
        try:
            import fcntl
        except ImportError:
            raise ContextError(
                "Context writes need Unix file locking; use read-only mode on this platform"
            ) from None
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ContextError("expected_revision must be a non-negative integer")
        self._refuse_symlink()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_fd = self._open_lock()
        except OSError as exc:
            raise ContextError("The advisor context could not be locked safely") from exc
        try:
            with os.fdopen(lock_fd, "a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                context = self._read_unlocked()
                if context["revision"] != expected_revision:
                    raise ContextConflictError(
                        f"Context is at revision {context['revision']}; read the latest context "
                        "and retry"
                    )
                mutate(context)
                context["revision"] += 1
                _validate_context(context)
                self._write_unlocked(context)
                return copy.deepcopy(context)
        except ContextError:
            raise
        except OSError as exc:
            raise ContextError("The advisor context could not be saved safely") from exc

    def _read_unlocked(self) -> JsonObject:
        self._refuse_symlink()
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return _empty_context()
        except OSError as exc:
            raise ContextError("The advisor context is unreadable or unsafe") from exc
        try:
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > self.max_bytes:
                    raise ContextError("The advisor context is unreadable or too large")
                raw = stream.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ContextError("The advisor context is unreadable or too large")
            value = json.loads(raw, parse_constant=self._reject_constant)
            return _validate_context(value)
        except ContextError:
            raise
        except (UnicodeError, json.JSONDecodeError, ValueError, OSError) as exc:
            raise ContextError("The advisor context is malformed or unreadable") from exc

    def _write_unlocked(self, context: JsonObject) -> None:
        try:
            serialized = (
                json.dumps(context, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContextError("The advisor context contains invalid values") from exc
        if len(serialized) > self.max_bytes:
            raise ContextError("The advisor context is too large to save")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=".msf-context-",
                delete=False,
            ) as stream:
                temporary = stream.name
                os.fchmod(stream.fileno(), 0o600)
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            self._refuse_symlink()
            os.replace(temporary, self.path)
            temporary = None
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as exc:
                raise ContextCommitUncertainError(
                    f"Context revision {context['revision']} was written, but durability "
                    "could not be confirmed; read the context before retrying"
                ) from exc
        except ContextError:
            raise
        except OSError as exc:
            raise ContextError("The advisor context could not be saved safely") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    Path(temporary).unlink(missing_ok=True)

    def _open_lock(self) -> int:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        return descriptor

    def _refuse_symlink(self) -> None:
        try:
            if self.path.is_symlink():
                raise ContextError("The advisor context path must not be a symbolic link")
        except OSError as exc:
            raise ContextError("The advisor context path is unreadable or unsafe") from exc

    @staticmethod
    def _record_id(record_id: str | None) -> str:
        if record_id is None:
            return str(uuid4())
        return _text(record_id, "record_id", maximum=128)

    @staticmethod
    def _reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")
