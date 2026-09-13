# MSF Credentials Implementation Plan

**Goal:** Allow the registered server application to use its ID and secret with
the public API key supplied by default.

**Architecture:** Preserve the existing configuration, OAuth and API-client
boundaries. Correct only configuration and token authentication; retain the
current resource methods and optional API-key override.

**Tech Stack:** Python 3.12+, requests, python-dotenv, pytest, Ruff.

## Constraints

- No new runtime dependencies, UI, database, or LLM calls.
- Personal credentials and local artifacts must not enter Git.
- Live login is not part of this patch and must not be claimed as verified.

## Task: Correct server credentials

Files: `src/msf_assistant/config.py`, `src/msf_assistant/auth.py`,
`tests/test_config.py`, `tests/test_auth.py`, `tests/test_client.py`,
`tests/conftest.py`, `.env.example`, `.gitignore`, `README.md`.

- [x] Add regression tests: ID/secret alone load successfully with the public
  API key; empty or absent ID/secret fail; API-key overrides remain supported;
  representations and browser URLs exclude the secret. Capture prepared token
  requests for code exchange and refresh and assert OAuth Basic authentication,
  encoded credentials, unchanged grant payloads, and disabled redirects.
- [x] Run the new tests against the old implementation and confirm failures
  reflect the missing secret support and mandatory API key.
- [x] Add `DEFAULT_API_KEY` from the official specification. Append
  `client_secret: str | None = field(default=None, repr=False)` to `Settings`.
  Load ID and secret from `.env`, report missing names without their values, and
  default empty/missing `MSF_API_KEY` to the documented public value.
- [x] In the common token-request method, reject a missing secret and supply
  `auth=(quote_plus(client_id), quote_plus(client_secret))` with
  `allow_redirects=False`. Remove redundant client IDs from token bodies.
- [x] Update `.env.example` and README to explain the two personal credentials,
  shared public API key, server-side app registration, and remaining login work.
  Exclude `.env.*` backup/swap files and explicitly allow `.env.example`.
- [x] Run `work/test-venv/bin/python -m pytest -q -o cache_dir=work/pytest-cache`,
  `work/test-venv/bin/ruff check .`, and `git diff --check`.

Delivery: commit only the listed source/docs/test files, push the correction
branch, and report the distinction between tests and live login.

## Verification

The initial suite passed 13 tests. New regression tests failed against the old
implementation (13 failures), then the complete suite passed 25 tests with the
fix. Ruff and `git diff --check` pass. Independent review found two missing
backup-file ignore patterns; `.env~` and `..env.sw?` were added and verified
with `git check-ignore`. No live OAuth request was made.
