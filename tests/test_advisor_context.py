import json
import multiprocessing
import os
import stat
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from msf_assistant.advisor_context import (
    ContextConflictError,
    ContextError,
    ContextStore,
)


def _save_concurrently(path: str, start: str, title: str, results) -> None:
    while not Path(start).exists():
        time.sleep(0.005)
    try:
        result = ContextStore(Path(path)).save_goal(
            expected_revision=0,
            title=title,
            description="Concurrent goal",
            status="proposed",
            provenance="concurrency test",
        )
        results.put(("saved", result["revision"]))
    except ContextConflictError as exc:
        results.put(("conflict", str(exc)))


def test_empty_context(tmp_path):
    result = ContextStore(tmp_path / "context.json").read()
    assert result["schema_version"] == 1
    assert result["revision"] == 0
    assert result["goals"] == []
    assert result["facts"] == []
    assert result["recommendations"] == []
    assert not (tmp_path / "context.json").exists()


def test_persistence_correction_and_detached_results(tmp_path):
    path = tmp_path / "context.json"
    first = ContextStore(path).save_goal(
        expected_revision=0,
        title="Prepare DD8",
        description="Build the city section",
        status="proposed",
        provenance="assistant suggestion",
    )
    goal = first["goals"][0]
    assert first["revision"] == 1
    assert goal["created_at"].endswith("+00:00")
    assert goal["updated_at"] == goal["created_at"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    goal["title"] = "detached mutation"
    persisted = ContextStore(path).read()
    assert persisted["goals"][0]["title"] == "Prepare DD8"

    corrected = ContextStore(path).save_goal(
        expected_revision=1,
        record_id=persisted["goals"][0]["id"],
        title="Prepare DD8 efficiently",
        description="Build the city section first",
        status="selected",
        provenance="user correction",
    )
    assert corrected["revision"] == 2
    assert corrected["goals"][0]["id"] == persisted["goals"][0]["id"]
    assert corrected["goals"][0]["created_at"] == persisted["goals"][0]["created_at"]
    assert corrected["goals"][0]["updated_at"] >= persisted["goals"][0]["updated_at"]


def test_stale_revision_is_actionable_and_preserves_file(tmp_path):
    path = tmp_path / "context.json"
    store = ContextStore(path)
    store.save_player_fact(
        expected_revision=0,
        key="spending_preference",
        value="free-to-play",
        provenance="user statement",
    )
    previous = path.read_bytes()

    with pytest.raises(ContextConflictError, match=r"revision 1.*read.*retry"):
        store.save_player_fact(
            expected_revision=0,
            key="spending_preference",
            value="unknown",
            provenance="stale writer",
        )

    assert path.read_bytes() == previous


def test_two_processes_cannot_lose_an_update(tmp_path):
    path = tmp_path / "context.json"
    start = tmp_path / "start"
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    processes = [
        context.Process(target=_save_concurrently, args=(str(path), str(start), title, results))
        for title in ("A", "B")
    ]
    for process in processes:
        process.start()
    start.touch()
    outcomes = [results.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "saved"]
    saved = ContextStore(path).read()
    assert saved["revision"] == 1
    assert len(saved["goals"]) == 1


def test_atomic_failure_preserves_valid_existing_context(tmp_path, monkeypatch):
    path = tmp_path / "context.json"
    store = ContextStore(path)
    store.save_player_fact(
        expected_revision=0,
        key="event_completion",
        value=None,
        provenance="user has not confirmed",
    )
    previous = path.read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("private path detail")))

    with pytest.raises(ContextError, match="could not be saved") as caught:
        store.save_player_fact(
            expected_revision=1,
            key="event_completion",
            value="DD7",
            provenance="user correction",
        )

    assert "private path detail" not in str(caught.value)
    assert path.read_bytes() == previous
    assert not [item for item in tmp_path.iterdir() if item.name.startswith(".msf-context-")]


def test_recommendation_references_and_goal_deletion_are_validated(tmp_path):
    path = tmp_path / "context.json"
    store = ContextStore(path)
    goal_context = store.save_goal(
        expected_revision=0,
        title="Enter DD8",
        description="Prepare the first section",
        status="selected",
        provenance="user selected",
    )
    goal_id = goal_context["goals"][0]["id"]
    recommendation = store.save_recommendation(
        expected_revision=1,
        goal_ids=[goal_id],
        summary="Build Robbie Reyes first.",
        roster_retrieved_at="2026-09-14T08:00:00+00:00",
        sources=[
            {
                "title": "DD8 guide",
                "url": "https://www.marvel.church/example",
                "retrieved_at": "2026-09-14T09:00:00+00:00",
            }
        ],
        character_plans=[
            {
                "character_id": "GhostRiderRobbie",
                "current_level": None,
                "current_gear_tier": None,
                "target_level": 100,
                "target_gear_tier": 19,
                "priority": 1,
                "rationale": "Core city damage",
                "locked": False,
            }
        ],
        uncertainty="Current level is absent from the roster snapshot.",
        provenance="assistant recommendation",
    )
    assert recommendation["revision"] == 2

    with pytest.raises(ContextError, match="referenced by a recommendation"):
        store.delete(record_type="goal", record_id=goal_id, expected_revision=2)

    with pytest.raises(ContextError, match="unknown goal"):
        store.save_recommendation(
            expected_revision=2,
            goal_ids=["missing-goal"],
            summary="No evidence",
            roster_retrieved_at="2026-09-14T08:00:00+00:00",
            sources=[],
            character_plans=[],
            uncertainty="Need goal confirmation.",
            provenance="assistant",
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda store: store.save_goal(
                expected_revision=0,
                title="x",
                description="x",
                status="invented",
                provenance="test",
            ),
            "status",
        ),
        (
            lambda store: store.save_player_fact(
                expected_revision=0,
                key="x",
                value={"arbitrary": "json"},
                provenance="test",
            ),
            "scalar",
        ),
        (
            lambda store: store.save_recommendation(
                expected_revision=0,
                goal_ids=[],
                summary="x",
                roster_retrieved_at="made up",
                sources=[],
                character_plans=[],
                uncertainty="unknown",
                provenance="test",
            ),
            "timestamp",
        ),
    ],
)
def test_bad_mutation_inputs_do_not_create_a_file(tmp_path, mutate, message):
    with pytest.raises(ContextError, match=message):
        mutate(ContextStore(tmp_path / "context.json"))
    assert not (tmp_path / "context.json").exists()


def test_malformed_unknown_fields_and_symlinks_are_rejected_without_replacement(tmp_path):
    path = tmp_path / "context.json"
    malformed = json.dumps(
        {
            "schema_version": 1,
            "revision": 0,
            "goals": [],
            "facts": [],
            "recommendations": [],
            "secret_extra": True,
        }
    )
    path.write_text(malformed)
    with pytest.raises(ContextError, match="malformed"):
        ContextStore(path).read()
    assert path.read_text() == malformed

    target = tmp_path / "target.json"
    target.write_text("private")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ContextError, match="symbolic link"):
        ContextStore(link).read()
    assert target.read_text() == "private"


def test_post_commit_durability_error_reports_written_revision(tmp_path, monkeypatch):
    path = tmp_path / "context.json"
    store = ContextStore(path)
    original_fsync = os.fsync

    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("private filesystem detail")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    with pytest.raises(ContextError, match=r"revision 1.*written.*durability"):
        store.save_player_fact(expected_revision=0, key="test", value=True, provenance="test")
    assert store.read()["revision"] == 1


def test_fifo_context_is_rejected_without_blocking(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "context.json"
    os.mkfifo(path)
    probe = """
import sys
from pathlib import Path
from msf_assistant.advisor_context import ContextError, ContextStore
try:
    ContextStore(Path(sys.argv[1])).read()
except ContextError:
    print('rejected')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(path)],
        text=True, capture_output=True, timeout=2, check=True,
    )
    assert result.stdout.strip() == "rejected"


def test_read_only_context_works_when_file_locking_is_unavailable(tmp_path):
    import subprocess
    import sys

    # The core must remain importable and readable on hosts without Unix flock.
    probe = """
import builtins
import sys
from pathlib import Path
# Load platform-aware third-party transport before simulating its unavailable primitive.
import mcp.server
original_import = builtins.__import__
def unavailable(name, *args, **kwargs):
    if name == 'fcntl':
        raise ImportError('not installed on this platform')
    return original_import(name, *args, **kwargs)
builtins.__import__ = unavailable
from msf_assistant.advisor_context import ContextError, ContextStore
from msf_assistant.mcp_server import create_server
path = Path(sys.argv[1])
assert ContextStore(path).read()['revision'] == 0
create_server(path.with_name('snapshot.json'), read_only=True)
try:
    ContextStore(path).save_player_fact(
        expected_revision=0, key='test', value=True, provenance='test'
    )
except ContextError as exc:
    assert 'read-only' in str(exc)
else:
    raise AssertionError('unsupported write succeeded')
assert not path.exists()
print('read-only available')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path / "context.json")],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "read-only available"
