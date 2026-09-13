import json
import sys

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
            "get_status",
            "get_player_profile",
            "get_player_roster",
            "get_inventory",
            "get_game_characters",
            "get_character",
        }
        for tool in listing.tools:
            assert tool.annotations.read_only_hint
            assert not tool.annotations.open_world_hint
            assert tool.output_schema
        for tool, arguments in [
            ("get_status", {}),
            ("get_player_profile", {}),
            ("get_player_roster", {"query": "wolv"}),
            ("get_inventory", {"query": "gold"}),
            ("get_game_characters", {}),
            ("get_character", {"character_id": "Wolverine"}),
        ]:
            result = await client.call_tool(tool, arguments)
            assert not result.is_error, result
            assert result.structured_content
        invalid = await client.call_tool("get_player_roster", {"limit": 101})
        assert invalid.is_error


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
