"""Private stdio MCP adapter; paths and credentials are never tool arguments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from msf_assistant.advisor_context import ContextError, ContextStore
from msf_assistant.advisor_instructions import (
    ADVISOR_INSTRUCTIONS,
    GUIDE_WORKFLOW,
    TASK_PROMPTS,
    TaskPrompt,
)
from msf_assistant.client import MAX_GEAR_TIER, MAX_LEVEL, MAX_RED, MAX_YELLOW, MSFAPIError
from msf_assistant.projection import project_character as run_projection
from msf_assistant.snapshot import SnapshotError, SnapshotReader

Offset = Annotated[int, Field(ge=0, strict=True)]
Limit = Annotated[int, Field(ge=1, le=100, strict=True)]
Query = Annotated[str, Field(max_length=200)]
RecordId = Annotated[str, Field(min_length=1, max_length=128)]
ShortText = Annotated[str, Field(min_length=1, max_length=1_000)]
Description = Annotated[str, Field(max_length=5_000)]
Summary = Annotated[str, Field(min_length=1, max_length=10_000)]
Timestamp = Annotated[str, Field(min_length=1, max_length=64)]
ExpectedRevision = Annotated[int, Field(ge=0, strict=True)]
FactValue = str | int | float | bool | None
CharacterId = Annotated[str, Field(min_length=1, max_length=200)]
Level = Annotated[int, Field(ge=1, le=MAX_LEVEL, strict=True)]
Yellow = Annotated[int, Field(ge=1, le=MAX_YELLOW, strict=True)]
Red = Annotated[int, Field(ge=0, le=MAX_RED, strict=True)]
GearTier = Annotated[int, Field(ge=1, le=MAX_GEAR_TIER, strict=True)] | Literal["all"]
Query_ = Callable[[Callable[[Any], Any]], Any]
PLAN_UPGRADES_DESCRIPTION = (
    "Plan roster-based upgrades with saved goals and research in the connected host."
)


class AdvisorServer(MCPServer):
    """Reject misspelled arguments before the SDK can discard them and execute a write."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> Any:
        for tool in await self.list_tools():
            if tool.name == name and set(arguments) - set(tool.input_schema.get("properties", {})):
                raise ToolError("Unknown tool argument; check the tool schema before retrying")
        return await super().call_tool(name, arguments, context)


class RecommendationSource(BaseModel):
    """One dated HTTP(S) source used for a recommendation."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=500)]
    url: Annotated[str, Field(min_length=1, max_length=2_000)]
    retrieved_at: Timestamp


class CharacterPlan(BaseModel):
    """One character's observed and proposed build state."""

    model_config = ConfigDict(extra="forbid")

    character_id: Annotated[str, Field(min_length=1, max_length=200)]
    current_level: Annotated[int, Field(ge=1, le=200, strict=True)] | None
    current_gear_tier: Annotated[int, Field(ge=1, le=100, strict=True)] | None
    target_level: Annotated[int, Field(ge=1, le=200, strict=True)]
    target_gear_tier: Annotated[int, Field(ge=1, le=100, strict=True)]
    priority: Annotated[int, Field(ge=1, le=1_000, strict=True)]
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]
    locked: StrictBool


def create_server(
    snapshot: Path,
    *,
    refresh: Callable[[], None] | None = None,
    context_path: Path | None = None,
    read_only: bool = False,
    query: Query_ | None = None,
) -> MCPServer:
    """Create a server over one owner's fixed snapshot, with optional refresh and live queries."""
    reader = SnapshotReader(snapshot)
    context = ContextStore(context_path or snapshot.parent / "msf-advisor-context.json")
    server = AdvisorServer(
        "MSF Assistant",
        version="0.4.0",
        instructions=ADVISOR_INSTRUCTIONS,
        log_level="WARNING",
    )
    return register_tools(
        server, reader, context, refresh=refresh, read_only=read_only, query=query
    )


@dataclass(frozen=True)
class ToolMessages:
    """Player-facing error texts; the hosted service overrides the local CLI hints."""

    read_failed: str = "Lokale Daten konnten nicht gelesen werden. Status und sync prüfen."
    refresh_failed: str = (
        "Aktualisierung fehlgeschlagen. Lokal sync --characters ausführen; "
        "bei abgelaufener Anmeldung login starten. Vorherige Daten bleiben erhalten."
    )
    query_failed: str = (
        "MSF query failed. Check the connection; if the sign-in expired, run login again."
    )
    # Exceptions of this type carry a safe, already player-facing message.
    passthrough: type[BaseException] | None = None

    def refresh_error(self, exc: BaseException) -> str:
        return self._safe(exc, self.refresh_failed)

    def query_error(self, exc: BaseException) -> str:
        return self._safe(exc, self.query_failed)

    def _safe(self, exc: BaseException, fallback: str) -> str:
        if self.passthrough is not None and isinstance(exc, self.passthrough):
            return str(exc)
        return fallback


def register_tools(
    server: AdvisorServer, reader: Any, context: Any, *,
    refresh: Callable[[], None] | None = None, read_only: bool = False,
    messages: ToolMessages | None = None, query: Query_ | None = None,
) -> MCPServer:
    """Bind the unchanged tool definitions to local or request-scoped backends.

    ``query`` runs ``fn(client)`` against MSF with the owner's credentials; live
    read tools are only offered when it is given and the server is not read-only.
    """
    messages = messages or ToolMessages()
    read = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    live_read = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    )
    write = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    )
    destructive_write = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    )

    def safe_call(call: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        try:
            return call(*args)
        except SnapshotError as exc:
            raise ToolError(str(exc)) from None
        except Exception:
            raise ToolError(messages.read_failed) from None

    def safe_context_call(
        call: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return call(*args, **kwargs)
        except ContextError as exc:
            raise ToolError(str(exc)) from None
        except Exception:
            raise ToolError(
                "Advisor context could not be read or saved; check the local context file"
            ) from None

    register_prompts(server)

    @server.tool(annotations=read)
    def get_guide() -> dict[str, Any]:
        """Explain the advisory workflow and prompts; call this first in a new conversation."""
        return guide()

    @server.tool(annotations=read)
    def get_status() -> dict[str, Any]:
        """Check data availability and age (never refreshes); see get_guide for the workflow."""
        return safe_call(reader.status)

    @server.tool(annotations=read)
    def get_player_profile() -> dict[str, Any]:
        """Read the player's profile, account level and total power after get_status."""
        return safe_call(reader.profile)

    @server.tool(annotations=read)
    def get_player_roster(
        query: Query = "", offset: Offset = 0, limit: Limit = 50
    ) -> dict[str, Any]:
        """Page through owned characters, strongest first; filter by ID/name with query."""
        return safe_call(reader.roster, query, offset, limit)

    @server.tool(annotations=read)
    def get_inventory(query: Query = "", offset: Offset = 0, limit: Limit = 50) -> dict[str, Any]:
        """Read owned item quantities (search by ID/name) when materials matter."""
        return safe_call(reader.inventory, query, offset, limit)

    @server.tool(annotations=read)
    def get_game_characters(
        query: Query = "", offset: Offset = 0, limit: Limit = 50
    ) -> dict[str, Any]:
        """Search the character catalog by ID/name, owned or not; then use get_character."""
        return safe_call(reader.characters, query, offset, limit)

    @server.tool(annotations=read)
    def get_character(
        character_id: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> dict[str, Any]:
        """Get abilities and owned stats for one exact character ID from a roster/catalog row."""
        return safe_call(reader.character, character_id)

    @server.tool(annotations=read)
    def get_advisor_context() -> dict[str, Any]:
        """Read saved goals, facts and recommendations plus the revision every save needs."""
        return safe_context_call(context.read)

    if query is not None and not read_only:

        @server.tool(annotations=live_read)
        def project_character(
            character_id: CharacterId, level: Level, yellow: Yellow, red: Red, gear_tier: GearTier
        ) -> dict[str, Any]:
            """Project stats/power of a hypothetical build (live MSF call); gear_tier "all"
            returns the whole gear curve. Use the exact ID from the roster or catalog."""
            try:
                return query(
                    lambda client: run_projection(
                        client,
                        character_id,
                        level=level,
                        yellow=yellow,
                        red=red,
                        gear_tier=gear_tier,
                    )
                )
            except MSFAPIError as exc:
                raise ToolError(str(exc)) from None
            except Exception as exc:
                raise ToolError(messages.query_error(exc)) from None

    if not read_only:

        @server.tool(annotations=write)
        def save_goal(
            expected_revision: ExpectedRevision,
            title: Annotated[str, Field(min_length=1, max_length=300)],
            description: Description,
            status: Literal["proposed", "selected", "paused", "completed"],
            provenance: ShortText,
            record_id: RecordId | None = None,
        ) -> dict[str, Any]:
            """Create or correct a goal using the revision from get_advisor_context."""
            return safe_context_call(
                context.save_goal,
                expected_revision=expected_revision,
                title=title,
                description=description,
                status=status,
                provenance=provenance,
                record_id=record_id,
            )

        @server.tool(annotations=write)
        def save_player_fact(
            expected_revision: ExpectedRevision,
            key: Annotated[str, Field(min_length=1, max_length=200)],
            value: FactValue,
            provenance: ShortText,
            record_id: RecordId | None = None,
        ) -> dict[str, Any]:
            """Create or correct a scalar player fact; null records explicitly unknown values."""
            return safe_context_call(
                context.save_player_fact,
                expected_revision=expected_revision,
                key=key,
                value=value,
                provenance=provenance,
                record_id=record_id,
            )

        @server.tool(annotations=write)
        def save_recommendation(
            expected_revision: ExpectedRevision,
            goal_ids: Annotated[list[RecordId], Field(max_length=200)],
            summary: Summary,
            roster_retrieved_at: Timestamp,
            sources: Annotated[list[RecommendationSource], Field(max_length=30)],
            character_plans: Annotated[list[CharacterPlan], Field(max_length=100)],
            uncertainty: Description,
            provenance: ShortText,
            record_id: RecordId | None = None,
        ) -> dict[str, Any]:
            """Save a goal-linked recommendation with roster time, evidence, and build plan."""
            return safe_context_call(
                context.save_recommendation,
                expected_revision=expected_revision,
                goal_ids=goal_ids,
                summary=summary,
                roster_retrieved_at=roster_retrieved_at,
                sources=[source.model_dump() for source in sources],
                character_plans=[plan.model_dump() for plan in character_plans],
                uncertainty=uncertainty,
                provenance=provenance,
                record_id=record_id,
            )

        @server.tool(annotations=destructive_write)
        def delete_advisor_record(
            expected_revision: ExpectedRevision,
            record_type: Literal["goal", "fact", "recommendation"],
            record_id: RecordId,
        ) -> dict[str, Any]:
            """Delete one advisor record; referenced goals cannot be deleted."""
            return safe_context_call(
                context.delete,
                expected_revision=expected_revision,
                record_type=record_type,
                record_id=record_id,
            )

    if refresh is not None and not read_only:

        @server.tool(
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=True,
            )
        )
        def refresh_data() -> dict[str, Any]:
            """Refresh missing/stale data once when permitted, or on explicit request."""
            try:
                refresh()
            except Exception as exc:
                raise ToolError(messages.refresh_error(exc)) from None
            return safe_call(reader.status)

    return server


def guide() -> dict[str, Any]:
    """Workflow overview for hosts that do not surface server instructions."""
    prompts = {name: prompt.description for name, prompt in TASK_PROMPTS.items()}
    return {
        "workflow": list(GUIDE_WORKFLOW),
        "prompts": {**prompts, "plan_upgrades": PLAN_UPGRADES_DESCRIPTION},
        "instructions": ADVISOR_INSTRUCTIONS,
    }


def guide_text() -> str:
    """Plain-text form of the guide for the guide://advisor resource."""
    steps = "\n".join(f"{n}. {step}" for n, step in enumerate(GUIDE_WORKFLOW, 1))
    return f"Advisor workflow\n\n{steps}\n\n{ADVISOR_INSTRUCTIONS}"



def render_prompt(question: str, focus: str = "") -> str:
    """Instructions, optional task focus and the user's question as one prompt."""
    parts = [ADVISOR_INSTRUCTIONS]
    if focus:
        parts.append(focus)
    parts.append("The user's current question:\n" + (question or "(none given)"))
    return "\n\n".join(parts)


def register_prompts(server: AdvisorServer) -> None:
    """Expose the general prompt plus one narrowed prompt per common task."""

    @server.prompt(description=PLAN_UPGRADES_DESCRIPTION)
    def plan_upgrades(question: str) -> str:
        return render_prompt(question)

    for name, task in TASK_PROMPTS.items():
        server.prompt(name=name, description=task.description)(task_prompt(task))

    @server.resource("guide://advisor", name="Advisor guide", mime_type="text/plain")
    def advisor_guide() -> str:
        """Workflow guide and advisory instructions."""
        return guide_text()


def task_prompt(task: TaskPrompt) -> Callable[[str], str]:
    """Build the prompt function for one task (a factory avoids late binding)."""

    def prompt(question: str = "") -> str:
        return render_prompt(question, task.focus)

    return prompt
