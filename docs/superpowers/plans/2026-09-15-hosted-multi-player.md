# Hosted Multi-player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the hosted MSF Assistant for public self-registration, ChatGPT and Claude, then audit completion and record outstanding work in Linear.

**Architecture:** One Python HTTP/MCP service behind nginx. Official MSF login identifies players, while separate revocable OAuth grants authorize clients. SQLite and private per-player files persist under `/var/lib/msf-assistant`; the existing local stdio path remains usable.

**Tech Stack:** Python >=3.12, MCP SDK >=2.2,<3, Starlette, SQLite, cryptography Fernet, existing requests-based MSF client, Docker/nginx. HTTP tests use httpx/Starlette TestClient and real MCP messages. Version the release as 0.4.0 after integration.

**Spec:** `docs/superpowers/specs/2026-09-15-hosted-multi-player-design.md`

## Global Constraints

- Python >=3.12. Preserve the existing local stdio CLI and macOS Keychain behavior.
- ChatGPT and Claude are the target clients; every player may self-register.
- Identify players only from the trusted MSF issuer and authenticated userinfo subject.
- Do not expose MSF access tokens, refresh tokens, client secrets or player paths to MCP clients.
- Persistent host data: `/var/lib/msf-assistant` on the separately mounted `/var` filesystem.
- Client grants are distinct; both clients of one player share that player's data and context.
- Preserve existing atomic snapshots and optimistic context revisions.
- Never migrate personal data, change host services, or publish an endpoint as a substitute for the required verification.
- Commit and push authored changes to GitHub. Keep private credentials and live evidence out of git and Linear.

## Execution decisions

The user's instruction to finish the project authorizes proceeding with the written design. Prior approval covered the overall architecture; storage placement is clarified in the spec. Use the existing ignored `work/hosted` worktree on `codex/hosted-multi-player`. Existing baseline: 148 passing tests. Push feature commits while keeping main's accepted local runtime intact until integration.

Use SDK OAuth handlers for protocol parsing and PKCE. Implement the provider's durable state, not a parallel custom OAuth protocol stack. Start with DCR, supported by both clients; do not add CIMD fetching and its SSRF surface in this release. Accept only HTTPS callback authorities for the named hosted clients and store exact callback URIs; never equate a public client's display name with verified identity. The separate official MSF flow uses its existing registered client credentials and trusted userinfo endpoint.

The implementation is sequenced below. Each task owns its files until review. Extend earlier interfaces only with a recorded ruling and matching tests. No parallel implementation agents; independent read-only deployment preparation can run in the controller.

## Task 1: Private player storage and encrypted MSF credentials

**Files:** Create `src/msf_assistant/hosted_store.py`, `tests/test_hosted_store.py`; modify `pyproject.toml` to add the hosted extra.

**Interfaces:**
```python
@dataclass(frozen=True)
class Player:
    id: str
    issuer: str
    subject: str

class HostedStore:
    def __init__(self, root: Path, key: bytes): ...
    def player(self, issuer: str, subject: str) -> Player: ...
    def require_player(self, player_id: str) -> Player: ...
    def player_dir(self, player_id: str) -> Path: ...
    def save_tokens(self, player_id: str, tokens: TokenSet) -> None: ...
    def load_tokens(self, player_id: str) -> TokenSet | None: ...
    def deactivate_player(self, player_id: str) -> None: ...
    def transaction(self) -> ContextManager[sqlite3.Connection]: ...
    def player_lock(self, player_id: str) -> ContextManager[None]: ...
```

The private schema uses `players(id, issuer, subject, active)` with a unique active issuer/subject pair, and `msf_tokens(player_id, encrypted)`. Deactivation prevents old identifiers being reused; a fresh verified registration may obtain a new internal ID. `player()` never accepts a caller-supplied internal ID. IDs are UUIDs, paths are rooted only in verified active IDs. `transaction()` owns a fresh SQLite connection and uses BEGIN IMMEDIATE/commit/rollback with foreign keys and a busy timeout, allowing later OAuth tables to share the same atomic persistence boundary. Document the database path/property needed for backup. Each connection closes after use.

- [ ] Add tests showing two subjects stay separate while reopening the store returns the same ID for the same issuer/subject; concurrent registrations return one identity.
```python
alice = store.player("https://issuer.example/", "alice")
bob = store.player("https://issuer.example/", "bob")
store.save_tokens(alice.id, TokenSet("alice-access", refresh_token="alice-refresh"))
assert store.load_tokens(bob.id) is None
assert store.player("https://issuer.example/", "alice").id == alice.id
```
- [ ] Run `PYTHONPATH=src ../../work/test-venv/bin/python -m pytest tests/test_hosted_store.py -q` and record expected missing-feature failure.
- [ ] Implement encrypted token persistence using Fernet with authenticated key binding: persist a key verifier so opening with another key fails instead of silently appearing empty. Reject invalid/missing keys and tampered ciphertext with safe errors. Do not print tokens in exceptions/reprs.
- [ ] Add failure tests for wrong key, modified ciphertext, traversal/unknown/deactivated ID, symlinked root/database/player directory and transaction rollback. Ensure no raw token text appears in SQLite bytes, JSON files or reprs. Use mode 0700 for owned directories and 0600 for database/token-related files, including SQLite side files. Fail closed on unsafe pre-existing state rather than changing ownership of arbitrary paths.
- [ ] Provide a bounded file-lock implementation per player, outside the deletable player data folder. Resolve/validate the active ID before and after acquiring its lock. Avoid an unbounded in-memory lock dictionary. The caller holds this lock around sync/delete; deactivation itself is atomic in SQLite.
- [ ] Run focused tests, Ruff and the full suite once. Commit and push the task. Report exact RED/GREEN evidence and any residual concerns.

## Task 2: Durable MCP OAuth provider

**Files:** Create `src/msf_assistant/hosted_oauth.py`, `tests/test_hosted_oauth.py`.

**Consumes:** HostedStore and its transaction context. **Produces:** `HostedOAuthProvider(store: HostedStore, public_url: str)` implementing SDK `OAuthAuthorizationServerProvider`; `complete_login(request_id: str, player_id: str) -> str` returns an internal consent URL; `approve(request_id: str, player_id: str) -> str` issues a code and returns the registered callback. Browser-session binding is supplied and checked by Task 3 before either transition; provider also binds the player to the request. Expose explicit `list_grants(player_id)`, `revoke_grant(player_id, grant_id)`, `revoke_player(player_id)` and `invalidate_all()` for the account/restore paths.

- [ ] Write failing provider tests with real SQLite state, SDK client/params models and clock control: exact callback/resource/scopes, wrong-client code exchange, expired/replayed code, and separate grants for one player.
```python
assert await provider.load_access_token("invented") is None
await provider.revoke_player(alice.id)
assert await provider.load_access_token(alice_access) is None
assert (await provider.load_access_token(bob_access)).subject == bob.id
```
- [ ] Implement DCR with HTTPS redirect authorities `chatgpt.com` and `claude.ai`, no userinfo/fragments, no wildcard redirects; reject unsupported grants, token auth modes and scopes. Public-client code flow with S256 is required. Resource is exactly `public_url + '/mcp'`. Use scopes `msf:read`, `msf:write`, `offline_access`; read is required for all tools, write for context mutation/refresh. Bound registration/request counts and expiry; registrations used by active grants remain valid.
- [ ] SDK 2.2.0's default metadata omits public-client authentication despite supporting it. Provide `provider.auth_routes()` using the SDK route handlers, replacing only metadata with advertised token/revocation auth method `none`; keep S256 and exact resource values. Test the actual metadata and token HTTP routes. Do not change installed SDK files. The SDK token handler also ignores the submitted resource and does not pass it to provider exchange methods: wrap only that route with an exact, single-resource check before delegating to the unchanged handler. Reject missing, wrong or duplicate resource values for code and refresh without consuming valid credentials; cover real HTTP requests. SDK 2.2 revocation parsing incorrectly requires the optional client_secret field even for public clients: narrowly normalize its absence to an empty value before delegating to the SDK handler, preserving supplied values and rejecting duplicate ambiguity. Test a normal public-client revoke and wrong-client isolation. Bound inactive client registrations to 1,000 and pending authorizations to 1,000; remove only expired/unreferenced records and reject new work on exhausted capacity. Active grants keep client registrations valid.
- [ ] Store only hashes of raw random tokens/codes. Store private request metadata encrypted with the store cipher via an explicit helper, not copied custom crypto. Add an explicit helper to HostedStore if required and cover it. Store grant/client/player/resource/expiry relationships transactionally. Access tokens last 15 minutes, rotating refresh tokens 30 days, codes 60 seconds and pending authorizations 10 minutes. Retain consumed refresh hashes through family expiry to detect reuse; a replay revokes only that family's tokens. Reject refresh scope escalation and wrong clients before rotation.
- [ ] Test concurrent code exchange and refresh using separate database connections, revocation across reopened providers, family replay, subject/resource fields on SDK AccessToken, deleted-player rejection and stale registrations. Verify SDK handlers reject missing/wrong PKCE through protocol tests in Task 4 rather than duplicating that responsibility.
- [ ] Run focused tests and Ruff; commit/push, then receive task review.

## Task 3: MSF identity bridge and account web pages

**Files:** Create `src/msf_assistant/hosted_identity.py`, `src/msf_assistant/hosted_web.py`, `tests/test_hosted_identity.py`, `tests/test_hosted_web.py`; add templates only if splitting HTML materially improves readability.

**Consumes:** HostedStore/HostedOAuthProvider. **Produces:** `MSFIdentity(settings: Settings)` with `begin(state: str) -> str` and `exchange(code: str) -> tuple[str, str, TokenSet]`; `account_routes(store, provider, identity, public_url) -> list[Route]`. The bridge's fixed issuer is the existing official MSF issuer; userinfo URL comes from a fixed trusted config, never from a browser request or decoded JWT.

- [ ] Write failing boundary tests with mocked HTTP transport only: callback wrong/replayed state, userinfo missing/empty subject, wrong issuer config, upstream failure, HTML escaping and browser-cookie mismatch. Real store/provider remain in tests.
- [ ] Use existing MSF code exchange and direct HTTPS userinfo with the exchanged token, redirects disabled and finite timeouts. Do not forward its token anywhere else. Add MSF PKCE only if required and supported by the actual app; keep upstream state independent from downstream PKCE. Do not treat an unverified ID token as identity. Persist tokens after identity verification, under the player lock, without overwriting a concurrently refreshed token outside that lock.
- [ ] Implement server-side random browser sessions stored hashed, Secure/HttpOnly/SameSite=Lax cookies, session rotation at successful authentication, one-use callback state and CSRF-protected consent. Preserve binding to the initiating browser and authorization request throughout. Cookies never contain upstream tokens. Only validated registered callbacks may leave the application.
- [ ] Provide German landing/help page, `/account`, explicit permission display, connection revocation, and account deletion with confirmation. Consent and connection lists must identify ChatGPT/Claude from the selected validated callback origin, retain this association per grant, and use German permission descriptions. Opaque client IDs or unverified DCR display names are not sufficient; test distinct clients, misleading names and a client registering both allowed callbacks. Include `/privacy.html` with factual data-use information matching hosted behavior (own MSF data/context, encrypted upstream credentials, client grants, deletion and delayed backup expiry), linked from help/account pages; do not reuse local-only privacy claims or invent legal/operator contact details. Delete under the same player lock as sync, deactivate before removing files, revoke all grants, and prevent a running sync from resurrecting data. All session/account POSTs require CSRF and same-origin checks; GETs never delete/revoke. Error pages are safe and actionable without reflecting provider secrets.
- [ ] Test Alice/Bob and two-client consent, account listings without foreign grants, forged POSTs, deletion-vs-refresh and session fixation. First-login setup does not perform the full slow roster/catalog sync inside the OAuth token endpoint; users can trigger refresh once connected.
- [ ] Run focused tests and Ruff, commit/push and receive task review.

## Task 4: Multi-player HTTP MCP and synchronization

**Files:** Create `src/msf_assistant/hosted_server.py`, `src/msf_assistant/hosted_sync.py`, `tests/test_hosted_server.py`, `tests/test_hosted_sync.py`; modify `mcp_server.py` only at its binding seam to share tool definitions; extend `advisor_context.py`, `client.py` and the existing snapshot write seam in `cli.py` narrowly if needed for configurable hosted size limits, preserving local defaults.

**Consumes:** all preceding modules; existing SnapshotReader/ContextStore/fetch_snapshot. **Produces:** `create_hosted_app(store, provider, identity, settings, public_url)` returns the ASGI app; `HostedSync(store, settings).refresh(player_id)` refreshes one active player's credentials and snapshot.

- [ ] Write failing HTTP protocol tests against the real ASGI app and SDK client: unauthenticated 401 with resource metadata, scoped reads/writes, two players/two clients, wrong resource, expired/revoked token, foreign session/request IDs, extra tool parameters, prompt access and reconnect after application recreation.
- [ ] Refactor tool registration to resolve a current request's player backend rather than capturing one global player's snapshot. Keep the existing create_server API and all local tests. Enforce authorization before reading prompt/context/tool results; no user-selected paths. Reuse existing tool implementations, schemas and strict extra-argument checking. Set SDK auth issuer/resource validation explicitly. Use stateless Streamable HTTP with JSON responses because current tools need no server-to-client requests; durable player context remains in storage. Use finite/bounded request state and no global mutable current-player variable. Mount the reviewed provider.auth_routes() adapters explicitly rather than recreating SDK default OAuth routes; use ProviderTokenVerifier for the MCP surface.
- [ ] Implement synchronization with a per-player file lock and a service-wide concurrency limit. Persist a renewed MSF token before fetching data; preserve its refresh token if upstream omits one. Existing snapshot remains on failed fetch. Offload blocking HTTP/file operations without blocking the event loop; preserve request identity across offloading. Keep token expiry/relogin errors safe and local-mode behavior unchanged.
- [ ] Apply response/body, request-rate and concurrency limits to the public surface. Defaults: 1 MiB request body, 60 authenticated requests per minute per player, 4 active MCP requests total, 2 concurrent global MSF synchronizations. Metadata/health remain cheap; rate-limit state expires. Bound snapshots to 20 MiB and contexts to 2 MiB without silently truncating. Reject oversized writes before replacing existing data. Bound hosted upstream response ingestion and cumulative catalog fetching before materializing unbounded data; preserve local client defaults. Return 429/Retry-After for rate limits and safe 503 for unavailable refresh capacity.
- [ ] Exercise two simultaneous context edits to prove conflict reporting, parallel refresh isolation, deletion races, slow upstream calls without blocking unrelated users, and secret-free errors. Include an actual loopback HTTP test so TestClient alone does not stand in for a network-capable server.
- [ ] Run the full test suite/Ruff, commit/push and receive task review.

## Task 5: Hosted CLI, backup and deployment bundle

**Files:** Create `src/msf_assistant/hosted_cli.py`, `src/msf_assistant/hosted_backup.py`, `tests/test_hosted_cli.py`, `tests/test_hosted_backup.py`, `Dockerfile`, `deploy/compose.yaml`, `deploy/nginx.conf.example`, `deploy/msf-assistant.service`, `deploy/hosted.env.example`; modify cli.py/pyproject.toml/README and add `docs/server-operations.md`; complete public connection help in `hosted_web.py` alongside the deployment documentation.

**Produces:** `msf-assistant hosted serve`, `hosted backup`, `hosted restore` and `hosted generate-key`. Credentials are file references; key generation creates an exclusive 0600 file and never prints its value. Configuration validates HTTPS origin, trusted MSF issuer, key file permissions and data path. `hosted serve` binds loopback by default; container deployment explicitly binds its container interface and publishes only host loopback.

- [ ] Write failing CLI/config tests: missing/unsafe secret files, wrong mount, invalid public URL, unavailable key, restore without maintenance, unsafe archive paths/symlinks and attempts to overwrite a live data root.
- [ ] Implement validated configuration and CLI dispatch without eagerly importing hosted dependencies for local commands. Pin the deployment dependency resolution and Python image digest after checking available official releases; runtime builds must not silently drift. Run the container as a non-root user with a read-only root filesystem, persistent data volume, tmpfs scratch, finite logging and memory limit. Health checks disclose no player state. The systemd wrapper requires `/var` mounted and verifies it before Compose starts.
- [ ] Implement a service-wide maintenance/file lock shared by sync/context writes/account state changes and backup. Back up SQLite and player files consistently into an archive without secrets/key material; key backup is a separate operator action. Restore into a new private directory with path/type/size validation, run integrity/schema checks, invalidate all restored grants/codes/browser sessions and stay in maintenance mode. Operator reconciles post-backup deletions before registration is reopened. Test restored data through a fresh authenticated session; restored tokens must fail.
- [ ] Add retention for local backups and a documented off-host copy/restore procedure; do not claim same-host copies protect against server failure. Document exact host data, key, nginx and Compose paths, migration of the existing user's snapshot only after verified identity, rollback and key rotation. The upstream MSF redirect URI must match the deployed origin's callback. Complete the German public help page with the configured origin + `/mcp` address and concise ChatGPT/Claude connection steps, linked to current official client instructions. The existing landing text alone does not explain how to add the connector; use automatic registration/OAuth and never ask players for MSF client secrets.
- [ ] Run CLI/subprocess/backup tests, full suite/Ruff and a container build plus smoke test when Docker is available. If build uses remote infrastructure, stage privately and do not expose personal data. Commit/push and receive task review.

## Task 6: Deployment, real clients and completion audit

**Files:** Update `docs/server-operations.md`, `docs/server-readiness.md`, `docs/advisor-acceptance.md`, plan status and private ignored evidence under outputs/ only. No credentials or identifying roster data in tracked files.

- [ ] Conduct whole-branch review of Tasks 1–5; address material findings and run affected tests. Build/install the release and exercise CLI/stdin compatibility. Merge verified changes and push as required by the user's repository instruction.
- [ ] Recheck SSH access, current host services, capacity and mounted /var. Prepare the exact nginx/DNS/TLS callback and runtime artifact. Identify the existing MSF app's callback configuration and permitted user audience through its actual UI. Complete all safe preparation before asking for an account permission or external action that is genuinely missing.
- [ ] Resolve host maintenance before public service: verify an active supported update path. Do not silently perform a major OS upgrade across existing web/mail applications. If that requires user/provider action, record the exact blocker while continuing private release verification.
- [ ] Deploy the verified image and protected runtime files to /var, test localhost health/MCP auth, nginx/TLS discovery and restart. Verify invalid tokens and manipulated player identifiers cannot cross accounts. Compare existing service state and host memory before/after under load.
- [ ] Connect both real ChatGPT and Claude using two authorized test players. Verify each player's context is shared across their clients and isolated from the other player; exercise refresh, writes, revoke and restart. If a second player or client account is unavailable, explicitly record that real acceptance remains unproven rather than treating synthetic fixtures as acceptance.
- [ ] Exercise backup/restore in a private copy and verify revocation. Test real clients with the local Mac runtime stopped only after server tests pass; preserve the previous connection until the migration succeeds.
- [ ] Audit every spec requirement against current code, test output and actual runtime evidence. Add missing requirements, provider dependencies or failed acceptance checks to Linear as actionable issues. Mark MIN-118 complete only when all required behavior is proven; otherwise keep the full goal active and continue resolving it. Keep the overall Linear project open, as the user explicitly requested.

## Progress

- Baseline in isolated worktree: 148 tests passed.
- Task 1 complete at 21bc9ec: 27 focused tests, previous full suite 174 tests, Ruff and independent review passed. Latest commit pending GitHub destination confirmation.
- Task 2 complete at 2130b6e: durable OAuth, 211 tests passed before the two added refresh-resource cases; scoped HTTP checks and independent review passed. One upstream Starlette deprecation warning remains visible. GitHub destination confirmation is pending.
- Task 3 complete at 220f574: verified MSF identity, protected browser sessions, German consent/account pages and per-client revocation. Focused tests and independent review passed; real account acceptance remains pending.
- Implementation progress is recorded in MIN-118; project remains open.
- Implementation and deployment are not yet complete.
