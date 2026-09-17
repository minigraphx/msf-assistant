# Personal MSF advice

The server provides your roster and stores goals, relevant player facts and
recommendations. The connected AI does the research and writes the advice. This
package contains no chatbot and no web crawler of its own.

## Getting started in a connected conversation

> Check my MSF data and my stored goals. Research the decisive current sources
> and propose one main goal and two alternatives. Only ask for information that
> would change your decision. After I pick a goal, create a prioritised upgrade
> plan per character with current and target level and gear tier. Plan
> free-to-play by default and store my chosen goal and the reasoned plan.

Hosts that support MCP prompts can call `plan_upgrades` with the concrete
question instead. The same workflow is delivered as instructions when the
server initialises, and the read-only tool `get_guide` returns it on demand for
hosts that do not show server instructions (ChatGPT). Whether the AI applies
these instructions correctly must be checked in your host against the
[acceptance cases](advisor-acceptance.md).

You can ask directly whom to upgrade next, how to prepare for DD8, which
characters look useful long-term, or whether a new team is worth it. For a
supposedly "newest" team, the current release state has to be established first.
Hosts that list prompts offer these as `next_upgrade`, `prepare_dark_dimension`,
`long_term_value`, `evaluate_new_team` and `data_check` (the last one only
reports data state and freshness, without advice). Each takes an optional
`question`.

## What answers should look like

The advice names the roster state, backs requirements with direct sources and
separates those from assessments. Marvel.Church is the preferred guide source;
official announcements and cross-checked community reports complement it. Age,
contradictions and uncertainty stay visible. Without research there is no claim
about the current meta.

After you pick a goal, the plan lists per character: team, current level and
gear, target level and target gear tier, priority and reasoning. For the steps
that matter the assistant can quantify the gain with `project_character`
(projected stats and power at the target build, or the whole gear curve). Material stock
does not cap the goal. Locked characters, sensible transition teams and reasoned
detours are taken into account. A paid option stays separate from the free path.
Missing information is only requested when needed; a strong roster does not
prove an event completion.

## What is stored permanently

Default file: `outputs/msf-advisor-context.json`, next to the snapshot. The file
is private, git-ignored and written by the server with owner-only permissions. A
new server process reads it again, so a new chat can resume goals. The store
belongs to exactly one player; use separate snapshot and context files for
several accounts. (The hosted service keeps one context per verified player.)

| Tool | Use |
| --- | --- |
| `get_advisor_context` | read goals, player facts, recommendations and the current revision |
| `save_goal` | create a proposal or a chosen goal and correct it later |
| `save_player_fact` | store one relevant fact; `null` means explicitly unknown |
| `save_recommendation` | store goal reference, roster timestamp, sources, character plan and uncertainty |
| `delete_advisor_record` | remove an entry at your request |

Goals distinguish `proposed`, `selected`, `paused` and `completed`. An AI
recommendation does not automatically become your decision. Every entry carries
provenance and creation/modification time; recommendations additionally keep the
roster state used and dated sources.

For corrections the AI uses the existing `record_id`. Every write needs the last
read `expected_revision`. If another conversation has saved in the meantime, the
stale write is rejected: read again, take the changes into account and apply only
the intended correction. A referenced goal can only be deleted after its
recommendations were corrected or removed.

Errors before the atomic replace leave the previous file untouched. If the new
revision was written but the on-disk confirmation failed, the server reports
exactly that uncertainty with the revision; read again first and do not blindly
repeat a create. A corrupt file is never silently replaced by an empty store.

## Operation

`serve --read-only` and `mcp-config --read-only` allow reading only; refresh,
save and delete are not offered and the AI must disclose the missing write
access. Pure queries need no login. Context writes need Unix file locks
(macOS/Linux); other hosts can use read-only mode.

`--context /private/path/context.json` lets the local operator fix the storage
location. Use a direct file path, not a symlink. Tool calls cannot choose paths.
Outside `outputs/` the operator must keep the file private and excluded from any
repository.

To reset completely, stop the server and back up or remove the context file
locally. The roster snapshot and the login are separate. Never copy private data
into GitHub or issue trackers.

During use the AI checks the data age and, for missing or stale data, tries one
permitted refresh. After a failure the old state stays marked. The package sets
up no daily background service. See [connecting](chatgpt-connection.md) and
[data coverage](data-coverage.md) for details.
