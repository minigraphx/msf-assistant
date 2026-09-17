# Connecting the local server

The local app is a personal MCP server without a chat UI. Login and refresh
need macOS because of the Keychain; pure queries of stored data also work on
other operating systems with Python 3.12+.

For several players, self-registration and a permanent HTTPS endpoint use the
hosted service instead: see [server-operations.md](server-operations.md) and
[hosted-acceptance.md](hosted-acceptance.md). The connection steps for ChatGPT
and Claude are shown on the hosted service's landing page.

## Local MCP host

Run in the project folder with the installed Python environment:

```bash
python -m msf_assistant mcp-config
```

The JSON contains absolute start paths; hosts with an `mcpServers` format can
take the entry as is. The host starts and stops the process. `--read-only`
suppresses refresh and all write tools for goals, facts and recommendations.
Data and context queries need neither `.env` nor Keychain access. The advisor
context lives next to the snapshot as `msf-advisor-context.json` by default;
`--context` fixes a private file, which later starts must reuse so goals remain
available.

For Codex the equivalent TOML entry is:

```toml
[mcp_servers.msf_assistant]
command = "/ABSOLUTE/PROJECT/PATH/.venv/bin/python"
args = ["-m", "msf_assistant", "--env-file", "/ABSOLUTE/PROJECT/PATH/.env", "serve", "--snapshot", "/ABSOLUTE/PROJECT/PATH/outputs/msf-snapshot.json"]
tool_timeout_sec = 180
```

Replace the paths with the values from `mcp-config`. Never copy MSF secrets into
a host configuration. See the
[official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## ChatGPT through a private tunnel

OpenAI documents a private connection path via **Secure MCP Tunnel**. It needs
a tunnel id, a dedicated tunnel runtime key and matching account/workspace
permissions, all independent of the MSF credentials. Current instructions and
the client download are in the
[official tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

1. Create a tunnel in the platform's tunnel area and assign it to your ChatGPT
   workspace.
2. On macOS install the official client with
   `brew install openai/tools/tunnel-client`. Create a separate **Restricted**
   runtime key with **Tunnels: Read + Use** — never an admin key. Provide it
   locally as `CONTROL_PLANE_API_KEY` or through a `file:/absolute/path`
   reference (file mode `0600`); never commit it.
3. Set up a stdio profile whose MCP start command is the Python binary and
   arguments from `mcp-config`; quote paths with spaces individually.
4. Check the profile with `tunnel-client doctor --explain`. Use
   `tunnel-client run` for a foreground session, or
   `tunnel-client runtimes connect` for a longer-running managed process and
   verify with `tunnel-client runtimes status <alias> --json` that it reports
   `healthy` and `ready`. The Mac and the tunnel must be running while in use;
   only one stdio instance per tunnel id.

Full options: `tunnel-client init --help` and
`tunnel-client runtimes connect --help`. With a custom profile directory always
pass `--profile-dir` (or `--profile-file`) to `doctor` and `run` as well. See the
[client documentation](https://github.com/openai/tunnel-client) and
[key permissions](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md).

In ChatGPT enable developer mode under **Settings → Security and sign-in**, then
under Plugins use **+ → Connection → Tunnel**, pick your tunnel, review the
discovered tools and enable them in a new conversation. Availability depends on
the account and workspace rules
([official guide](https://developers.openai.com/plugins/deploy/connect-chatgpt)).
After updating the package, restart the server (and the tunnel if needed) and
reload the tool list in ChatGPT.

A good first test: "Check the MSF data state and show my three strongest
characters." Expect `get_status` first, then `get_player_roster` with
`limit: 3`. ChatGPT does not show the server instructions; the assistant
should call `get_guide` on its own in a new conversation — if it does not, ask
for it once. Name search requires one prior `sync --characters`. Then let the
assistant open a current public guide page in the same conversation — only that
proves research and the private MCP work together in your host.

The local app deliberately opens no public HTTP port; the MSF callback on
localhost is for login only and is not an MCP endpoint. A public service needs
HTTPS, access control and permanent operation — that is the hosted mode.

## Common start-up problems

- **`No module named msf_assistant`:** run `python -m pip install '.[local,mcp]'`
  again from the project folder with the same Python environment. A regular
  install avoids macOS issues with hidden `.pth` files of editable installs.
- **No data:** run `login` once, then `sync --characters`.
- **No stored login:** log in again locally; a Keychain dialog must be confirmed
  on the Mac, which the assistant cannot do for you.
- **Refresh already running:** let the other operation finish and retry; the
  existing data stays readable meanwhile.
- **Source changes:** with a regular install, reinstall the package and restart
  the MCP host.

## Advice workflow

Use the MCP prompt `plan_upgrades` (or one of the task prompts `next_upgrade`,
`prepare_dark_dimension`, `long_term_value`, `evaluate_new_team`, `data_check`)
if the host offers prompts, or the opening request from the
[advisor guide](advisor-guide.md). The server instructions are delivered at MCP
initialisation and returned by `get_guide`. The [acceptance checklist](advisor-acceptance.md)
separates protocol tests from the four real game questions.
