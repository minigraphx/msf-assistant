import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from msf_assistant.mcp_server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "profile": {"data": {"name": "Commander"}},
                "roster": {"data": [{"id": "Wolverine", "power": 150}]},
                "inventory": {"data": [{"item": "Gold", "quantity": 9}]},
                "characters": [{"id": "Wolverine", "name": "Wolverine"}],
                "retrieved_at": "2026-09-13T12:00:00+00:00",
                "characters_retrieved_at": "2026-09-13T12:00:00+00:00",
            }
        )
    )
    return path


@pytest.mark.anyio
async def test_protocol_tools_and_queries(snapshot):
    async with Client(create_server(snapshot), raise_exceptions=True) as client:
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        assert names == {
            "get_guide",
            "get_status",
            "get_player_profile",
            "get_player_roster",
            "get_inventory",
            "get_game_characters",
            "get_character",
            "get_advisor_context",
            "save_goal",
            "save_player_fact",
            "save_recommendation",
            "delete_advisor_record",
        }
        read_names = {
            "get_guide",
            "get_status",
            "get_player_profile",
            "get_player_roster",
            "get_inventory",
            "get_game_characters",
            "get_character",
            "get_advisor_context",
        }
        for tool in (tool for tool in listing.tools if tool.name in read_names):
            assert tool.annotations.read_only_hint
            assert not tool.annotations.open_world_hint
            assert tool.output_schema
        writes = {tool.name: tool for tool in listing.tools if tool.name not in read_names}
        assert all(not tool.annotations.read_only_hint for tool in writes.values())
        assert all(not tool.annotations.open_world_hint for tool in writes.values())
        assert not writes["save_goal"].annotations.destructive_hint
        assert writes["delete_advisor_record"].annotations.destructive_hint
        assert writes["save_recommendation"].input_schema["properties"]["sources"]
        assert writes["save_recommendation"].input_schema["properties"]["character_plans"]
        for tool, arguments in [
            ("get_guide", {}),
            ("get_status", {}),
            ("get_player_profile", {}),
            ("get_player_roster", {"query": "wolv"}),
            ("get_inventory", {"query": "gold"}),
            ("get_game_characters", {}),
            ("get_character", {"character_id": "Wolverine"}),
            ("get_advisor_context", {}),
        ]:
            result = await client.call_tool(tool, arguments)
            assert not result.is_error, result
            assert result.structured_content
        invalid = await client.call_tool("get_player_roster", {"limit": 101})
        assert invalid.is_error

        goal_result = await client.call_tool(
            "save_goal",
            {
                "expected_revision": 0,
                "title": "Prepare DD8",
                "description": "Build city characters",
                "status": "selected",
                "provenance": "user selected",
            },
        )
        goal_id = goal_result.structured_content["goals"][0]["id"]
        recommendation = await client.call_tool(
            "save_recommendation",
            {
                "expected_revision": 1,
                "goal_ids": [goal_id],
                "summary": "Build Wolverine only as a temporary option.",
                "roster_retrieved_at": "2026-09-13T12:00:00+00:00",
                "sources": [
                    {
                        "title": "Official roster data",
                        "url": "https://marvelstrikeforce.com/example",
                        "retrieved_at": "2026-09-14T12:00:00+00:00",
                    }
                ],
                "character_plans": [
                    {
                        "character_id": "Wolverine",
                        "current_level": None,
                        "current_gear_tier": None,
                        "target_level": 100,
                        "target_gear_tier": 19,
                        "priority": 1,
                        "rationale": "Available transition character",
                        "locked": False,
                    }
                ],
                "uncertainty": "Level and gear are absent from the fixture.",
                "provenance": "assistant recommendation",
            },
        )
        assert not recommendation.is_error, recommendation
        assert recommendation.structured_content["revision"] == 2
        assert (snapshot.parent / "msf-advisor-context.json").is_file()


@pytest.mark.anyio
async def test_refresh_explicit_and_sanitized(snapshot):
    def broken():
        raise RuntimeError("secret-token-must-not-leak")

    async with Client(create_server(snapshot, refresh=broken)) as client:
        tools = (await client.list_tools()).tools
        refresh = next(t for t in tools if t.name == "refresh_data")
        assert not refresh.annotations.read_only_hint
        assert refresh.annotations.open_world_hint
        result = await client.call_tool("refresh_data", {})
        assert result.is_error
        assert "secret-token-must-not-leak" not in result.model_dump_json()


@pytest.mark.anyio
async def test_read_only_suppresses_every_mutation(snapshot, tmp_path):
    context = tmp_path / "private-context.json"
    async with Client(
        create_server(
            snapshot,
            refresh=lambda: pytest.fail("must not refresh"),
            context_path=context,
            read_only=True,
        )
    ) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "get_advisor_context" in names
        assert not names.intersection(
            {
                "refresh_data",
                "save_goal",
                "save_player_fact",
                "save_recommendation",
                "delete_advisor_record",
            }
        )
        result = await client.call_tool("get_advisor_context", {})
        assert result.structured_content["revision"] == 0
    assert not context.exists()


@pytest.mark.anyio
async def test_missing_snapshot_is_actionable(tmp_path):
    async with Client(create_server(tmp_path / "missing")) as client:
        result = await client.call_tool("get_player_profile", {})
        assert result.is_error
        assert "login" in result.model_dump_json() or "sync" in result.model_dump_json()


@pytest.mark.anyio
async def test_real_stdio_without_credentials(snapshot, tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "msf_assistant",
            "--env-file",
            str(tmp_path / "absent.env"),
            "serve",
            "--read-only",
            "--snapshot",
            str(snapshot),
        ],
    )
    async with Client(params, read_timeout_seconds=10) as client:
        result = await client.call_tool("get_player_roster", {})
        assert not result.is_error
        assert result.structured_content["total"] == 1


@pytest.mark.anyio
async def test_context_round_trip_across_stdio_server_processes(snapshot, tmp_path):
    context = tmp_path / "advisor.json"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    writable = StdioServerParameters(
        command=sys.executable,
        env=environment,
        args=[
            "-m",
            "msf_assistant",
            "--env-file",
            str(tmp_path / "absent.env"),
            "serve",
            "--snapshot",
            str(snapshot),
            "--context",
            str(context),
        ],
    )
    async with Client(writable, read_timeout_seconds=10) as client:
        saved = await client.call_tool(
            "save_goal",
            {
                "expected_revision": 0,
                "title": "Prepare DD8",
                "description": "Build a city team",
                "status": "selected",
                "provenance": "user selected",
            },
        )
        assert not saved.is_error, saved
        assert saved.structured_content["revision"] == 1

    read_only = StdioServerParameters(
        command=sys.executable,
        env=environment,
        args=[
            "-m",
            "msf_assistant",
            "serve",
            "--read-only",
            "--snapshot",
            str(snapshot),
            "--context",
            str(context),
        ],
    )
    async with Client(read_only, read_timeout_seconds=10) as client:
        loaded = await client.call_tool("get_advisor_context", {})
        assert loaded.structured_content["goals"][0]["title"] == "Prepare DD8"


@pytest.mark.anyio
async def test_context_tool_rejects_bad_inputs_conflicts_and_sanitizes_errors(
    snapshot, tmp_path, monkeypatch
):
    context = tmp_path / "advisor.json"
    async with Client(create_server(snapshot, context_path=context)) as client:
        invalid = await client.call_tool(
            "save_player_fact",
            {
                "expected_revision": 0,
                "key": "preference",
                "value": {"arbitrary": "json"},
                "provenance": "test",
            },
        )
        assert invalid.is_error
        assert not context.exists()

        saved = await client.call_tool(
            "save_player_fact",
            {
                "expected_revision": 0,
                "key": "spending_preference",
                "value": "free-to-play",
                "provenance": "user statement",
            },
        )
        record_id = saved.structured_content["facts"][0]["id"]
        conflict = await client.call_tool(
            "delete_advisor_record",
            {"expected_revision": 0, "record_type": "fact", "record_id": record_id},
        )
        assert conflict.is_error
        assert "revision 1" in conflict.model_dump_json()

    context.write_text("private malformed detail")
    async with Client(create_server(snapshot, context_path=context)) as client:
        malformed = await client.call_tool("get_advisor_context", {})
        assert malformed.is_error
        serialized = malformed.model_dump_json()
        assert "private malformed detail" not in serialized
        assert str(context) not in serialized


@pytest.mark.anyio
async def test_refresh_returns_new_snapshot_status(snapshot, capsys):
    calls = []

    def refresh():
        payload = json.loads(snapshot.read_text())
        payload["roster"]["data"].append({"id": "Storm", "power": 200})
        snapshot.write_text(json.dumps(payload))
        calls.append(True)

    async with Client(create_server(snapshot, refresh=refresh)) as client:
        result = await client.call_tool("refresh_data", {})
        assert not result.is_error
        assert result.structured_content["roster_count"] == 2
        assert calls == [True]
    assert capsys.readouterr().out == ""


@pytest.mark.anyio
async def test_unknown_tool_argument_cannot_write_context(snapshot, tmp_path):
    context = tmp_path / "advisor.json"
    async with Client(create_server(snapshot, context_path=context)) as client:
        response = await client.call_tool(
            "save_goal",
            {"expected_revision": 0, "title": "DD8", "description": "Prepare",
             "status": "proposed", "provenance": "test", "unexpected": "ignored?"},
        )
        assert response.is_error
        assert not context.exists()
        listing = await client.list_tools()
        assert all(tool.input_schema.get("additionalProperties") is False for tool in listing.tools)


@pytest.mark.anyio
async def test_stdio_context_symlink_is_refused(snapshot, tmp_path):
    import os

    from msf_assistant.advisor_context import ContextStore

    target = tmp_path / "private.json"
    ContextStore(target).save_player_fact(
        expected_revision=0, key="secret", value="private-value", provenance="test"
    )
    link = tmp_path / "link.json"
    link.symlink_to(target)
    params = StdioServerParameters(
        command=sys.executable,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        args=["-m", "msf_assistant", "serve", "--read-only", "--snapshot", str(snapshot),
              "--context", str(link)],
    )
    async with Client(params, read_timeout_seconds=10) as client:
        result = await client.call_tool("get_advisor_context", {})
        assert result.is_error
        assert "private-value" not in result.model_dump_json()


@pytest.mark.anyio
async def test_project_character_runs_a_live_query_and_is_read_only(snapshot):
    from msf_assistant.client import MSFAPIClient

    seen = []

    def query(fn):
        seen.append(fn)
        client = type("FakeClient", (), {})()
        client.character_instance = lambda *a, **k: {
            "data": {"gearTier": 18, "power": 4200, "stats": {"health": 5}, "basic": 7}
        }
        assert not isinstance(client, MSFAPIClient)
        return fn(client)

    async with Client(create_server(snapshot, query=query), raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        tool = tools["project_character"]
        assert tool.annotations.read_only_hint
        assert tool.annotations.open_world_hint  # contacts MSF
        assert not tool.annotations.destructive_hint
        result = await client.call_tool(
            "project_character",
            {"character_id": "Wolverine", "level": 90, "yellow": 7, "red": 7, "gear_tier": 18},
        )
        assert not result.is_error, result
        build = result.structured_content["builds"][0]
        assert build["gear_tier"] == 18 and build["power"] == 4200
        assert result.structured_content["character_id"] == "Wolverine"
        curve = await client.call_tool(
            "project_character",
            {"character_id": "Wolverine", "level": 90, "yellow": 7, "red": 7, "gear_tier": "all"},
        )
        assert not curve.is_error, curve
        for bad in (
            {"character_id": "", "level": 90, "yellow": 7, "red": 7, "gear_tier": 18},
            {"character_id": "W", "level": 0, "yellow": 7, "red": 7, "gear_tier": 18},
            {"character_id": "W", "level": 90, "yellow": 8, "red": 7, "gear_tier": 18},
            {"character_id": "W", "level": 90, "yellow": 7, "red": 11, "gear_tier": 18},
            {"character_id": "W", "level": 90, "yellow": 7, "red": 7, "gear_tier": "some"},
            {"character_id": "W", "level": 90, "yellow": 7, "red": 7, "gear_tier": 0},
        ):
            assert (await client.call_tool("project_character", bad)).is_error, bad
    assert len(seen) == 2


@pytest.mark.anyio
async def test_project_character_is_absent_without_query_and_sanitizes_failures(snapshot):
    async with Client(create_server(snapshot), raise_exceptions=True) as client:
        assert "project_character" not in {t.name for t in (await client.list_tools()).tools}
    async with Client(create_server(snapshot, read_only=True, query=lambda fn: fn(None))) as client:
        assert "project_character" not in {t.name for t in (await client.list_tools()).tools}

    def failing(fn):
        raise RuntimeError("secret-upstream-body")

    async with Client(create_server(snapshot, query=failing)) as client:
        result = await client.call_tool(
            "project_character",
            {"character_id": "Wolverine", "level": 90, "yellow": 7, "red": 7, "gear_tier": 18},
        )
        assert result.is_error
        text = result.model_dump_json()
        assert "secret-upstream-body" not in text
        assert "login" in text  # local remedy
