# MSF Assistant

A Marvel Strike Force advisor for AI assistants. The project provides an
[MCP](https://modelcontextprotocol.io) server that gives ChatGPT, Claude or any
other MCP-capable assistant read access to **your own** MSF profile, roster,
inventory and the character catalog, plus a private, durable advisor context
(goals, player facts, recommendations) and an advisory workflow. It contains no
chat UI and no LLM of its own; the reasoning happens in the assistant you connect.

Game data is provided by Scopely's Marvel Strike Force API and used under the
[MSF API Terms of Use](https://developer.marvelstrikeforce.com). This project is
not endorsed by, sponsored by or affiliated with Scopely or Marvel.

Two ways to run it:

| Mode | For | Transport | Credentials |
| --- | --- | --- | --- |
| **Local** | one player on their own Mac | stdio (the MCP host starts `serve`) | macOS Keychain |
| **Hosted** | several players, self-registration, ChatGPT and Claude | HTTPS Streamable HTTP with OAuth 2.1 | encrypted per player on the server |

## Features

- OAuth2 authorization-code flow against MSF with `state` checks and token refresh
- Player profile (`player/v1/card`), roster (`player/v1/roster`), inventory
  (`player/v1/inventory`) and paginated character catalog (`game/v1/characters`)
- MCP tools with search, pagination, data age and explicit refresh
- Durable advisor context: goals, player facts and recommendations with
  provenance, roster state and optimistic revision checks; full read-only mode
- Advisory instructions and an MCP prompt for roster analysis, source checking
  and concrete upgrade plans
- Hosted mode: verified MSF identity per player, per-client revocable grants,
  isolated player storage, bounded synchronization, consistent backups, English
  account/consent/privacy/terms pages with your operator identity

## Prerequisites

- Python 3.12 or newer
- An application registered in the [MSF Developer Portal](https://developer.marvelstrikeforce.com)
  (type **Server-Side**) with its client ID and client secret. The public
  `x-api-key` from the official API specification is built in; `MSF_API_KEY`
  overrides it if Scopely changes it.

## Local mode (macOS)

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install '.[local,mcp]'        # development: -e '.[dev,local,mcp]'
cp -n .env.example .env                     # then set MSF_CLIENT_ID, MSF_CLIENT_SECRET, MSF_REDIRECT_URI
```

Register `http://localhost:8000/oauth/callback` as the redirect for local use
(domain `localhost:8000`, path `/oauth/callback`, HTTPS off, privacy path
`/privacy.html`). The login command serves the callback and privacy page only
while a login is running.

```bash
python -m msf_assistant login              # browser login; tokens go to the Keychain
python -m msf_assistant sync --characters  # refresh profile/roster/inventory + catalog
python -m msf_assistant status             # data age and counts
python -m msf_assistant mcp-config         # JSON for your MCP host (add --read-only to disable writes)
python -m msf_assistant logout             # remove local tokens
```

`login --no-save` fetches once without storing tokens; `--timeout 900` extends
the login window; `--env-file PATH` selects another configuration. The snapshot
is written atomically to `outputs/msf-snapshot.json` (user-only permissions, no
tokens); the advisor context lives next to it unless `--context PATH` is given.
The login server binds to `127.0.0.1` only, validates host, path, state and a
browser cookie, accepts each callback once and exits afterwards.

Client secret handling: `client_secret_basic` on token exchange and refresh, never
in the browser URL or API headers; token requests do not follow redirects.
`.env` is git-ignored; credentials and tokens must never reach logs or the repo.

### MCP tools

| Tool | Result |
| --- | --- |
| `get_guide` | workflow overview, available prompts, full instructions |
| `get_status` | availability, data age, counts |
| `get_player_profile` | profile, level, total power |
| `get_player_roster` | own characters by power, name search |
| `get_inventory` | items and quantities, search by id/name |
| `get_game_characters` | compact catalog with name search |
| `get_character` | catalog entry plus your own build for one character id |
| `refresh_data` | fetch fresh data from MSF (write tool) |
| `get_advisor_context` | stored goals, facts, plans and revision |
| `save_goal`, `save_player_fact`, `save_recommendation`, `delete_advisor_record` | maintain the advisor context |

Lists take `query`, `offset` and `limit` (1–100) and report `total` and
`next_offset`. Responses carry the retrieval time; data older than 24 hours is
flagged stale. The catalog is fetched in small pages because MSF rejects large
responses with full ability data. The prompt `plan_upgrades` takes a concrete
game question; `next_upgrade`, `prepare_dark_dimension`, `long_term_value`,
`evaluate_new_team` and `data_check` narrow it to one task. The advisory
instructions are sent at initialization, returned by `get_guide` and exposed as
the resource `guide://advisor`.

More: [advisor guide](docs/advisor-guide.md),
[connecting the local server](docs/chatgpt-connection.md),
[data coverage](docs/data-coverage.md),
[acceptance checklist](docs/advisor-acceptance.md).

## Hosted mode (self-hosting for several players)

The hosted service is a single container behind nginx: players sign in with
their own MSF account (OIDC userinfo verified), get an isolated data area, and
connect ChatGPT and/or Claude through dynamic client registration and OAuth 2.1
with PKCE; each connection can be revoked on the account page. Game data
fetched from the MSF API expires after 30 days, is never written to backups and
is deleted immediately when a player deletes their account.

```bash
python -m pip install '.[hosted]'
python -m msf_assistant hosted --help   # `hosted` must be the first argument
```

Deployment in short (details, limits, backup/restore and operator duties in
[docs/server-operations.md](docs/server-operations.md)):

1. Choose a public name **without** a Scopely or Marvel mark (no "msf",
   "marvel", "strike force" in the host name or the service name — the MSF API
   terms forbid it and the configuration refuses such values). Point DNS at the
   host and issue a certificate for it.
2. Build the image on a workstation and load it on the host:
   `docker build --platform linux/amd64 -t msf-assistant:0.4.0-<commit> .`,
   `docker save`, copy, `docker load`.
3. Run `sudo PUBLIC_URL=https://<host> bash deploy/install-host.sh msf-assistant:0.4.0-<commit> --nginx`
   with the `deploy/` files next to the script. It creates the service user,
   private directories under `/var/lib/msf-assistant`, `/etc/msf-assistant/hosted.env`,
   the encryption key, the systemd unit and the nginx vhost.
4. Edit `/etc/msf-assistant/hosted.env`: operator name/address/email (rendered
   on the privacy and terms pages) and, after registering the MSF app with
   callback `https://<host>/oauth/callback`, the client ID; put the client
   secret into `/etc/msf-assistant/secrets/msf-client-secret` (uid 10001, mode
   0600). Restart with `systemctl restart msf-assistant.service`.
5. Walk through [docs/hosted-acceptance.md](docs/hosted-acceptance.md) with two
   players and both clients before inviting others.

Exactly one container with one worker is supported. `hosted backup`,
`hosted restore`, `hosted resume` and `hosted delete-player` are the operator
commands; the service never logs credentials, request parameters or player data.

## Library use

```python
from msf_assistant import MSFAPIClient, MSFOAuth2, Settings

settings = Settings.from_env()
oauth = MSFOAuth2(settings)
authorization_url, state = oauth.authorization_url()
# open authorization_url in a browser, then handle the callback:
code = oauth.parse_callback("http://localhost:8000/oauth/callback?code=...&state=...", expected_state=state)
tokens = oauth.exchange_code(code)

client = MSFAPIClient(settings, tokens.access_token)
profile = client.player_profile()
roster = client.player_roster()
inventory = client.inventory()
characters = client.game_characters()
```

`requests` HTTP errors propagate to the caller; `MSFAPIError` marks unexpected
JSON. Library methods do not persist anything.

## Development

```bash
python -m pip install -e '.[dev,local,mcp,hosted]'
pytest
ruff check .
```

Tests use synthetic data and cover OAuth, file safety, the advisor context, the
real MCP protocol flow (including a separate server process) and the hosted
service against a real ASGI app and SQLite. `outputs/` and `work/` are
git-ignored for personal artifacts.

## License

MIT — see [LICENSE](LICENSE).
