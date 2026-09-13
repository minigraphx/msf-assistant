# Local MSF login and first snapshot

## Outcome

The user has configured ID, client secret and API key and approved implementing
the local login server. Add an executable workflow that opens a local page,
lets the user authorize MSF in their browser, handles the callback, and saves a
private profile/roster/inventory snapshot. No chat interface or MCP yet.

## Architecture

- Keep the requests-based API/OAuth library unchanged except hiding token values
  in repr. Add CLI commands `login`, `sync`, and `logout`.
- `login` binds only IPv4 loopback, using the configured HTTP localhost callback
  URL. Serve a small landing page, `/login`, the exact callback path, and
  `/privacy.html`. The browser must explicitly follow the MSF login link.
- Protect callbacks with a random state and a separate HttpOnly SameSite=Lax
  browser cookie, a five-minute expiry, strict Host/path checks, and one-use
  processing. Never log request URLs, token values, or raw upstream errors.
  Use no-cache and no-referrer responses; show sanitized errors.
- The server returns tokens to the CLI, then shuts down. Tokens are saved through
  an optional `keyring` dependency directly to the macOS Keychain backend; never
  silently fall back to plaintext. `login --no-save` supports a one-off run with
  tokens only in memory. Persistent CLI use on other OSes is outside this step.
- `sync` loads saved tokens and refreshes before use when a refresh token exists;
  retain an existing refresh token when a refresh response omits a replacement.
  `logout` deletes the local Keychain entry (not remote authorization).
- After login, fetch profile, roster and inventory, writing one atomic JSON
  snapshot (default `outputs/msf-snapshot.json`) with owner-only permissions.
  Include a UTC retrieval time but no tokens. If an API call fails, preserve any
  existing snapshot and report the affected operation without raw response data.
- CLI output shows safe status and output path, never roster contents or secrets.

## Privacy page

Describe the current local workflow, data categories, MSF/Scopely requests,
Keychain storage versus `--no-save`, local output files and deletion controls.
Do not claim legal compliance. The page is available while login is running;
local development is not public hosting.

## Validation

Unit tests use synthetic credentials and fake Keychain/network boundaries.
Exercise real loopback HTTP requests, invalid/duplicate/expired state, missing
cookie, wrong Host/path, callback errors, replay, secret-free pages/logs,
shutdown/timeout, refresh token retention, and private atomic file writing.
Run full pytest and Ruff under Python 3.12, review, commit and push. Start the
real local login page only after checks; user completes MSF login themselves.
