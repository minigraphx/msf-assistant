# Local MSF Login Implementation Plan

**Goal:** Browser OAuth login, macOS token persistence and private roster snapshot.

**Architecture:** CLI composes a bounded loopback callback server, a Keychain
token-store adapter and the existing MSF clients. Core API dependencies stay
small; keyring is an optional `local` extra.

**Tech Stack:** Python 3.12+, stdlib HTTPServer, requests, python-dotenv,
optional keyring 25.x, pytest, Ruff.

## Tasks and interfaces

1. Token storage (`token_store.py`, `tests/test_token_store.py`):
   `TokenStoreError(RuntimeError)` and
   `KeychainTokenStore(client_id: str, oauth_base_url: str, *, backend=None)`
   with `load() -> TokenSet | None`, `save(tokens: TokenSet) -> None`,
   `clear() -> None`. Default backend is explicitly macOS Keychain. Scope entries
   by client ID and OAuth base URL. Validate stored JSON and sanitize failures.
   Use backend injection in tests; never access actual Keychain from tests.
2. Local callback (`login.py`, `tests/test_login.py`):
   `LoginError(RuntimeError)` and
   `browser_login(settings: Settings, *, open_browser: bool = True,
   timeout: float = 300, on_ready: Callable[[str], None] | None = None) -> TokenSet`.
   Validate a loopback HTTP callback, implement one-use state/cookie checks,
   static pages, sanitized errors and timeout. Expose internal server/application
   objects for compositional HTTP tests with injected OAuth client and clock.
3. CLI/snapshot (`cli.py`, `__main__.py`, tests, pyproject, README):
   `main(argv: list[str] | None = None) -> int` with login/sync/logout and
   `--env-file`, `--output`, `--no-browser`, `--no-save`, `--timeout` as applicable.
   Compose token store and callback, save tokens before fetching, atomically
   write owner-only output only after all three calls succeed. Catch expected
   configuration/network/storage/OS errors without displaying secret-bearing
   exception strings. Add optional local dependency and console entry point.
4. Review complete diff and tests. Commit/push and update the existing PR around
   the full final implementation. Start the local login page for the user.

## Test sequence

For each component, write tests and run them before implementation; confirm
failure is caused by missing behavior. Implement and rerun covering tests.
Finally run `work/test-venv/bin/python -m pytest -q -o cache_dir=work/pytest-cache`,
`work/test-venv/bin/ruff check .`, and `git diff --check`.

## Execution status

- Token storage: implemented; 20 tests passed; independent review approved after
  strict version, idempotent deletion and save-validation fixes.
- Callback server: implemented; real loopback HTTP callback and timeout tests pass.
- CLI and snapshot: implemented; first full suite passed 80 tests.
- Final review: approved after logout was changed to load only the Keychain
  identity, without requiring OAuth secret or request timeout. Final suite:
  81 tests passed under Python 3.12.13, Ruff and diff whitespace checks passed.
- Local startup: server started on loopback port 8000 with a 15-minute timeout;
  the browser reached Scopely sign-in. The user must complete authorization.
  No successful live token exchange or snapshot is claimed by these tests.
- Publication: implementation and validation are committed and pushed together;
  the Git history and PR record the delivery result.
