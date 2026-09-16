# Task 4 implementation report

Implementation commit: `b850a6e` (`feat: add bounded multi-player HTTP MCP service`).

## Delivered interfaces and behavior

- `create_hosted_app(store, provider, identity, settings, public_url, *, limits=None)` returns an ASGI application with lifespan forwarding. It shares the existing tool definitions through `register_tools`; `create_server` and local tool schemas remain unchanged.
- SDK 2.2 stateless Streamable HTTP uses JSON responses, `ProviderTokenVerifier`, explicit issuer/resource and `validate_token_resource=True`. Reviewed `provider.auth_routes()` adapters and account routes are mounted explicitly. Prompts, initialization instructions and all MCP requests require authentication/read scope; write tools additionally require write scope before SDK dispatch (HTTP 403).
- Immutable per-request player IDs propagate through ContextVar into SDK worker threads. Each backend operation takes the existing per-player cross-process lock around file reads/mutations. No player/path tool parameters or shared current-player value exist. Stateless foreign session/event/request identifiers cannot select another player; durable context survives app recreation and SDK reconnects.
- `HostedSync(store, settings).refresh(player_id)` is synchronous and runs in the SDK worker. It takes a shared capacity slot and player lock, refreshes credentials, preserves omitted refresh tokens, persists renewed tokens before fetching, and retains old snapshots on fetch/validation/size failure. HTTP admission reserves refresh capacity before executing the tool, so exhaustion is actual HTTP 503. Safe errors omit upstream details.
- Optional narrow limits: `ContextStore(..., max_bytes=...)`, `MSFAPIClient(..., max_bytes=...)`, `fetch_snapshot(..., max_bytes=...)`, `write_snapshot(..., max_bytes=...)`, and `MSFOAuth2(..., max_response_bytes=...)`. Existing local defaults are unchanged. The controller explicitly approved extending OAuth and hosted identity ingestion after identifying the integration gap.

## Bounds and deployment contract

- 1 MiB total public request body, 30-second body-read deadline; HTTP 413/408.
- 24 MiB buffered response ceiling; safe HTTP 503 before forwarding response headers.
- Four active public requests by default, admitted before authentication/body reads; cheap GET/HEAD health and metadata routes are exempt. `HostedLimits` allows configuration.
- Sixty authenticated MCP requests per minute per player; HTTP 429 plus Retry-After. Rate state expires after 60 seconds and is capped at 10,000 players; a full table returns 503 instead of allocating more.
- Two global synchronization slots for the shared HostedSync instance; no queue. HTTP 503 plus Retry-After on refresh admission exhaustion.
- 20 MiB cumulative decoded upstream data budget across personal resources and every catalog page, checked before JSON parsing/materialization; final serialized snapshot also capped at 20 MiB before replacement. This is deliberately conservative when whitespace/schema expansion differs.
- 2 MiB hosted context; existing local default remains 1,000,000 bytes. Oversize context writes fail before replacement.
- 64 KiB streaming limits for hosted token exchange/refresh and userinfo, before JSON parsing.
- Controller ruling: exactly one application process/Uvicorn worker and one service instance. Request/rate/sync admission is app-instance-wide and therefore service-wide under that contract. Multiple workers/replicas are unsupported without redesign. Persistent player locks remain cross-process for deletion/maintenance correctness. No SQLite transaction is held while waiting for player locks or network operations.

## RED/GREEN evidence

- Initial focused sync run: 2 failures (missing hosted_sync module and unsupported ContextStore max_bytes). Implemented; 2 passed.
- Snapshot/upstream budget tests: 2 new failures (unsupported max_bytes APIs); implemented; 4 passed.
- Initial HTTP fixture: missing hosted_server module error; implemented authenticated ASGI boundary; 1 passed.
- Slow-body deadline: unsupported body_timeout failed, then passed after bounded receive implementation.
- Malformed tool-name regression: unhashable list caused TypeError; fixed guard, then asserted SDK's actual JSON-RPC -32602 response (HTTP 200) rather than assuming HTTP 400.
- Initialization instructions regression: expected advisor instructions versus missing result; fixed; passed.
- Hosted token/userinfo limits: 2 failures (unsupported OAuth bound and wrong unbounded userinfo behavior); implemented; combined sync/identity/web suite 32 passed.
- Auth storage-failure regression: unhandled synthetic private exception; implemented outer safe boundary; passed.
- Additional adversarial/integration coverage: separate player snapshot/context results; insufficient scope; wrong resource; expired/revoked tokens; extra arguments; prompts; persisted context after app recreation; real localhost SDK clients with foreign session/event headers and reconnect; simultaneous context edits with exactly one conflict; bounded expiring rate state; response/body/capacity limits; parallel refresh isolation; deletion waiting for refresh without resurrection; slow refresh leaving another player responsive; cumulative catalog budget; old snapshot retention; secret-free errors.

## Final verification

- `PYTHONPATH=src ../../work/test-venv/bin/python -m pytest -q`: **261 passed**, 1 known upstream Starlette BlockingPortal deprecation warning, 5.98 seconds. Not suppressed.
- `../../work/test-venv/bin/python -m ruff check src tests`: **All checks passed**.
- `git diff --check`: clean before implementation commit.
- Real loopback SDK test ran through Uvicorn on an ephemeral 127.0.0.1 port with real SDK ClientSession messages and synthetic accounts/data. Sandbox-only runs correctly failed socket binding; approved escalation passed. One permission-review timeout was retried once as permitted, then succeeded. Full suite also ran with localhost-capable approval.

## Self-review and remaining limits

No external deployment, real MSF calls, personal-data copies, credential exports, or broad repository refactoring occurred. All network fixtures are synthetic or localhost. File byte bounds do not establish an exact peak RSS bound after Python JSON expansion; Task 5 still needs representative synthetic memory/concurrency measurements for the deployment size. The pre-existing generic tool error wording still references local recovery/context files, preserving shared tool behavior. Stateless transport intentionally offers no persistent HTTP session or server push; storage supplies durable player context. Independent task review remains required.
