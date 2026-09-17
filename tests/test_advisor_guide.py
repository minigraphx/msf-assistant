"""Discoverability: the model must find the workflow from the tool list alone."""

import pytest
from mcp import Client

from msf_assistant.advisor_instructions import ADVISOR_INSTRUCTIONS, TASK_PROMPTS
from msf_assistant.mcp_server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_instructions_are_english_and_transport_neutral():
    # The hosted service is English-first; the model answers in the player's language.
    assert ADVISOR_INSTRUCTIONS.startswith("You are")
    assert "Du bist" not in ADVISOR_INSTRUCTIONS
    assert "answer in the user's language" in ADVISOR_INSTRUCTIONS.lower()
    for tool in ("get_status", "get_advisor_context", "refresh_data", "save_recommendation"):
        assert tool in ADVISOR_INSTRUCTIONS


@pytest.mark.anyio
async def test_get_guide_is_read_only_and_available_without_data(tmp_path):
    # ChatGPT ignores server instructions; the guide must be reachable as a tool.
    snapshot = tmp_path / "absent" / "snapshot.json"
    async with Client(create_server(snapshot, read_only=True), raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        guide = tools["get_guide"]
        assert guide.annotations.read_only_hint
        assert not guide.annotations.open_world_hint
        assert "get_guide" in tools["get_status"].description
        result = await client.call_tool("get_guide", {})
        content = result.structured_content
        assert content["workflow"][0].startswith("Call get_status")
        assert "refresh_data" in " ".join(content["workflow"])
        assert content["instructions"] == ADVISOR_INSTRUCTIONS
        assert set(content["prompts"]) == set(TASK_PROMPTS) | {"plan_upgrades"}
    assert not snapshot.parent.exists()


@pytest.mark.anyio
async def test_task_prompts_render_their_focus_and_the_question(tmp_path):
    snapshot = tmp_path / "absent" / "snapshot.json"
    expected = {
        "plan_upgrades",
        "next_upgrade",
        "prepare_dark_dimension",
        "long_term_value",
        "evaluate_new_team",
        "data_check",
    }
    async with Client(create_server(snapshot, read_only=True), raise_exceptions=True) as client:
        prompts = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}
        assert set(prompts) == expected
        for name in expected - {"plan_upgrades"}:
            assert prompts[name].description
            argument_names = {argument.name for argument in prompts[name].arguments or []}
            assert argument_names == {"question"}
            assert not any(argument.required for argument in prompts[name].arguments)
            result = await client.get_prompt(name, {"question": "my exact question"})
            text = "\n".join(message.content.text for message in result.messages)
            assert "my exact question" in text
            assert TASK_PROMPTS[name].focus in text
            assert ADVISOR_INSTRUCTIONS in text
            assert all(message.role == "user" for message in result.messages)
        bare = await client.get_prompt("data_check", {})
        assert TASK_PROMPTS["data_check"].focus in bare.messages[0].content.text


@pytest.mark.anyio
async def test_guide_resource_lists_the_workflow(tmp_path):
    snapshot = tmp_path / "absent" / "snapshot.json"
    async with Client(create_server(snapshot, read_only=True), raise_exceptions=True) as client:
        resources = {str(resource.uri) for resource in (await client.list_resources()).resources}
        assert "guide://advisor" in resources
        content = await client.read_resource("guide://advisor")
        text = content.contents[0].text
        assert "get_status" in text
        assert ADVISOR_INSTRUCTIONS in text
