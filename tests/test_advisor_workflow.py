"""Check the client-facing advice prompt, without claiming LLM acceptance."""

import pytest
from mcp import Client

from msf_assistant.mcp_server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_advice_prompt_preserves_question_without_reading_or_writing_data(tmp_path):
    # A missing prompt or dropping the user's question breaks host integration.
    snapshot = tmp_path / "absent" / "snapshot.json"
    async with Client(create_server(snapshot, read_only=True)) as client:
        listing = await client.list_prompts()
        assert "plan_upgrades" in {prompt.name for prompt in listing.prompts}
        question = "Wie bereite ich DD8 mit meinem aktuellen Roster vor?"
        result = await client.get_prompt("plan_upgrades", {"question": question})
        assert any(question in message.content.text for message in result.messages)
        assert all(message.role == "user" for message in result.messages)
    assert not snapshot.parent.exists()
