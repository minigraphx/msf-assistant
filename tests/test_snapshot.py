import json
from datetime import UTC, datetime, timedelta

import pytest

from msf_assistant.snapshot import SnapshotError, SnapshotReader, validate_snapshot


def write_snapshot(tmp_path, **updates):
    retrieved_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    payload = {
        "profile": {"data": {"name": "Test Commander"}, "meta": {}},
        "roster": {
            "data": [
                {"id": "char_b", "level": 10, "power": 50},
                {"id": "CHAR_A", "level": 20, "power": 100},
                {"id": "char_c", "level": 5, "power": 100},
            ],
            "meta": {},
        },
        "inventory": {
            "data": [
                {"item": "Gear_Alpha", "quantity": 2},
                {"item": {"id": "orb_beta", "details": {"name": "Blue Orb"}}, "quantity": 3},
            ],
            "meta": {},
        },
        "retrieved_at": retrieved_at,
        "characters": [
            {"id": "char_a", "name": "Alpha"},
            {"id": "char_b", "name": "Beta"},
            {"id": "char_c", "name": "Charlie"},
        ],
        "characters_retrieved_at": retrieved_at,
    }
    payload.update(updates)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload))
    return path, payload


def test_status_reports_counts_and_freshness(tmp_path):
    path, payload = write_snapshot(tmp_path)

    result = SnapshotReader(path).status()

    assert result["available"] is True
    assert result["profile_available"] is True
    assert result["roster_count"] == 3
    assert result["inventory_count"] == 2
    assert result["characters_count"] == 3
    assert result["retrieved_at"] == payload["retrieved_at"]
    assert 0 <= result["age_seconds"] < 7200
    assert result["stale"] is False
    assert result["characters_retrieved_at"] == payload["characters_retrieved_at"]


def test_missing_status_is_unavailable_and_other_reads_explain_recovery(tmp_path):
    reader = SnapshotReader(tmp_path / "missing.json")

    assert reader.status() == {"available": False}
    with pytest.raises(SnapshotError, match="login.*sync"):
        reader.profile()


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"retrieved_at": NaN}', '{"retrieved_at": "2026-09-13"}'],
)
def test_malformed_snapshots_raise_sanitized_errors(tmp_path, content):
    path = tmp_path / "private-name.json"
    path.write_text(content)

    with pytest.raises(SnapshotError) as caught:
        SnapshotReader(path).status()

    assert "private-name" not in str(caught.value)
    assert content not in str(caught.value)


def test_profile_returns_copy_with_retrieval_metadata(tmp_path):
    path, payload = write_snapshot(tmp_path)

    result = SnapshotReader(path).profile()

    assert result["data"] == {"name": "Test Commander"}
    assert result["retrieved_at"] == payload["retrieved_at"]
    assert result["stale"] is False
    assert result["age_seconds"] >= 0


def test_roster_enriches_filters_sorts_and_pages_without_mutation(tmp_path):
    path, payload = write_snapshot(tmp_path)
    original = json.loads(json.dumps(payload))
    reader = SnapshotReader(path)

    result = reader.roster(query="  aLp  ", limit=1)

    assert result["data"] == [{"id": "CHAR_A", "level": 20, "power": 100, "name": "Alpha"}]
    assert result["total"] == 1
    assert result["next_offset"] is None
    assert payload == original
    assert [row["id"] for row in reader.roster()["data"]] == ["CHAR_A", "char_c", "char_b"]


def test_inventory_searches_id_and_name_inside_item_only(tmp_path):
    path, _ = write_snapshot(tmp_path)

    assert SnapshotReader(path).inventory(query=" blue ")["data"][0]["quantity"] == 3
    assert SnapshotReader(path).inventory(query="GEAR_ALPHA")["total"] == 1
    assert SnapshotReader(path).inventory(query="3")["total"] == 0


def test_characters_are_searchable_and_paged(tmp_path):
    path, _ = write_snapshot(tmp_path)

    result = SnapshotReader(path).characters(query="char", offset=1, limit=1)

    assert result["data"] == [{"id": "char_b", "name": "Beta"}]
    assert result["total"] == 3
    assert result["next_offset"] == 2


def test_characters_return_compact_summaries_but_character_keeps_abilities(tmp_path):
    ability_kit = {"basic": {"name": "Claws", "description": "Attack primary target"}}
    character = {
        "id": "char_a",
        "name": "Alpha",
        "description": "A test hero",
        "traits": ["Hero", "Mutant"],
        "status": "playable",
        "unlockStars": 3,
        "abilityKit": ability_kit,
        "portrait": "large-unused-field",
    }
    path, _ = write_snapshot(tmp_path, characters=[character])

    assert SnapshotReader(path).characters()["data"] == [
        {
            "id": "char_a",
            "name": "Alpha",
            "description": "A test hero",
            "traits": ["Hero", "Mutant"],
            "status": "playable",
            "unlockStars": 3,
        }
    ]
    assert SnapshotReader(path).character("char_a")["data"]["abilityKit"] == ability_kit


def test_character_returns_static_and_roster_data_with_separate_timestamps(tmp_path):
    catalogue_time = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    roster_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    path, payload = write_snapshot(
        tmp_path, retrieved_at=roster_time, characters_retrieved_at=catalogue_time
    )

    result = SnapshotReader(path).character("ChAr_A")

    assert result == {
        "data": {"id": "char_a", "name": "Alpha"},
        "roster": {"id": "CHAR_A", "level": 20, "power": 100},
        "retrieved_at": payload["characters_retrieved_at"],
        "age_seconds": pytest.approx(25 * 3600, abs=5),
        "stale": True,
        "roster_retrieved_at": payload["retrieved_at"],
        "roster_age_seconds": pytest.approx(5 * 60, abs=5),
        "roster_stale": False,
    }


def test_public_validation_checks_in_memory_snapshot_shape():
    with pytest.raises(SnapshotError, match="malformed"):
        validate_snapshot(
            {
                "profile": {"data": {}},
                "roster": {"data": [{"power": 100}]},
                "inventory": {"data": []},
                "retrieved_at": "2026-09-13T12:00:00+00:00",
            }
        )


def test_public_validation_rejects_nested_nonfinite_numbers():
    with pytest.raises(SnapshotError, match="malformed"):
        validate_snapshot(
            {
                "profile": {"data": {"total_power": float("nan")}},
                "roster": {"data": [{"id": "char_a"}]},
                "inventory": {"data": []},
                "retrieved_at": "2026-09-13T12:00:00+00:00",
            }
        )


def test_character_errors_explain_missing_catalogue_or_unknown_id(tmp_path):
    path, _ = write_snapshot(tmp_path, characters=None, characters_retrieved_at=None)
    with pytest.raises(SnapshotError, match="sync --characters"):
        SnapshotReader(path).characters()

    path, _ = write_snapshot(tmp_path)
    with pytest.raises(SnapshotError, match="not found"):
        SnapshotReader(path).character("unknown")


@pytest.mark.parametrize("method", ["roster", "inventory", "characters"])
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"offset": -1}, "offset"),
        ({"offset": True}, "offset"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": False}, "limit"),
    ],
)
def test_paging_validation(tmp_path, method, kwargs, message):
    path, _ = write_snapshot(tmp_path)

    with pytest.raises(SnapshotError, match=message):
        getattr(SnapshotReader(path), method)(**kwargs)


def test_reader_reloads_snapshot_for_every_call(tmp_path):
    path, _ = write_snapshot(tmp_path)
    reader = SnapshotReader(path)
    assert reader.roster()["total"] == 3

    write_snapshot(tmp_path, roster={"data": [], "meta": {}})

    assert reader.roster()["total"] == 0


def test_stale_threshold_is_24_hours(tmp_path):
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    path, _ = write_snapshot(tmp_path, retrieved_at=old)

    assert SnapshotReader(path).profile()["stale"] is True


def test_overflowing_json_number_is_rejected_as_nonfinite(tmp_path):
    path, _ = write_snapshot(tmp_path)
    content = path.read_text().replace('"quantity": 2', '"quantity": 1e400')
    path.write_text(content)

    with pytest.raises(SnapshotError, match="malformed"):
        SnapshotReader(path).inventory()
