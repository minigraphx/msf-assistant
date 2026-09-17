"""Stat/power projection for a hypothetical build via characterInstances."""

from unittest.mock import Mock

import pytest

from msf_assistant.client import MSFAPIClient, MSFAPIError
from msf_assistant.projection import project_character


@pytest.fixture
def client(settings, session) -> MSFAPIClient:
    return MSFAPIClient(settings, "token", session)


def instance(**overrides):
    return {
        "id": "Wolverine",
        "level": 90,
        "activeYellow": 7,
        "activeRed": 7,
        "gearTier": 17,
        "basic": 7,
        "special": 7,
        "ultimate": 7,
        "passive": 5,
        "stats": {
            "health": 120000,
            "damage": 9000,
            "armor": 3000,
            "focus": 2000,
            "resist": 2000,
            "speed": 120,
            "critChance": 10,
        },
        "power": 250000,
        "info": {"name": "must be dropped"},
        "gearSlots": [True] * 6,
        **overrides,
    }


def test_character_instance_requests_the_exact_build_without_metadata(client, session):
    session.get.return_value.json.return_value = {"data": instance()}
    client.character_instance("Wolverine", level=90, yellow=7, red=7, gear_tier=17)
    url = session.get.call_args.args[0]
    assert url.endswith("game/v1/characterInstances/Wolverine/90/7/7/17")
    params = session.get.call_args.kwargs["params"]
    assert params["charInfo"] == "none"
    assert params["itemFormat"] == "id"
    assert params["lang"] == "none"


def test_character_instance_escapes_ids_and_rejects_out_of_range_builds(client, session):
    session.get.return_value.json.return_value = {"data": instance()}
    for bad in (
        dict(level=0, yellow=1, red=0, gear_tier=1),
        dict(level=1, yellow=8, red=0, gear_tier=1),
        dict(level=1, yellow=1, red=11, gear_tier=1),
        dict(level=1, yellow=1, red=0, gear_tier=0),
        dict(level=1, yellow=1, red=0, gear_tier="some"),
    ):
        with pytest.raises(ValueError):
            client.character_instance("Wolverine", **bad)
    for bad_id in ("", ".", "..", "a b", "x/y", "x?y", "x#y", "x%2Fy"):
        with pytest.raises(ValueError):
            client.character_instance(bad_id, level=1, yellow=1, red=0, gear_tier=1)
    client.character_instance("Ms.Marvel_Kamala-2", level=1, yellow=1, red=0, gear_tier=1)
    assert "characterInstances/Ms.Marvel_Kamala-2/1/1/0/1" in session.get.call_args.args[0]


def test_project_character_returns_a_compact_build(client, session):
    session.get.return_value.json.return_value = {"data": instance()}
    result = project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier=17)
    assert result["character_id"] == "Wolverine"
    assert result["retrieved_at"]
    (build,) = result["builds"]
    assert build == {
        "level": 90,
        "yellow": 7,
        "red": 7,
        "gear_tier": 17,
        "abilities": {"basic": 7, "special": 7, "ultimate": 7, "passive": 5},
        "power": 250000,
        "stats": {
            "health": 120000,
            "damage": 9000,
            "armor": 3000,
            "focus": 2000,
            "resist": 2000,
            "speed": 120,
            "critChance": 10,
        },
    }
    assert "info" not in str(result)


def test_project_character_all_gear_tiers_gives_a_bounded_curve(client, session):
    rows = [instance(gearTier=tier, power=1000 * tier) for tier in range(1, 21)]
    session.get.return_value.json.return_value = {"data": rows}
    result = project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier="all")
    assert "characterInstances/Wolverine/90/7/7/all" in session.get.call_args.args[0]
    assert [b["gear_tier"] for b in result["builds"]] == list(range(1, 21))
    assert result["builds"][-1]["power"] == 20000
    too_many = [instance(gearTier=1)] * 101
    session.get.return_value.json.return_value = {"data": too_many}
    with pytest.raises(MSFAPIError, match="too many"):
        project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier="all")


@pytest.mark.parametrize(
    "data",
    [
        None,
        "text",
        {"power": "high"},
        {"power": 1, "stats": {"health": "lots"}},
        {"power": 1, "stats": {"health": float("nan")}},
        {"power": 1, "stats": {"health": True}},
        {"power": True, "stats": {}},
        {"power": 1, "stats": "csv"},
        {"power": 1, "stats": {}, "basic": "max"},
        [],
    ],
)
def test_project_character_rejects_malformed_upstream_data(client, session, data):
    session.get.return_value.json.return_value = {"data": data}
    with pytest.raises(MSFAPIError):
        project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier=17)


def test_project_character_accepts_fractional_stats(client, session):
    session.get.return_value.json.return_value = {
        "data": instance(stats={"critChance": 12.5, "health": 10})
    }
    result = project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier=17)
    assert result["builds"][0]["stats"] == {"critChance": 12.5, "health": 10}


def test_project_character_needs_only_the_character_instance_call(settings):
    client = Mock(spec=MSFAPIClient)
    client.character_instance.return_value = {"data": instance()}
    project_character(client, "Wolverine", level=90, yellow=7, red=7, gear_tier=17)
    client.character_instance.assert_called_once_with(
        "Wolverine", level=90, yellow=7, red=7, gear_tier=17
    )
