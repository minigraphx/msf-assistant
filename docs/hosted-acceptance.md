# Hosted acceptance test (two players, two clients)

Real-client acceptance before opening an instance to other players. Automated tests prove the protocol; this
script proves the deployment with the actual ChatGPT and Claude clients. Record
the outcome of every step (pass/fail, date, who) in your tracker. Do not paste tokens,
codes or callback URLs into notes.

## Prerequisites

- Service healthy at `https://<host>/health`, image tag noted.
- MSF app registered with callback `https://<host>/oauth/callback`;
  `MSF_CLIENT_ID` and `/etc/msf-assistant/secrets/msf-client-secret` set to the
  real values; `systemctl restart msf-assistant.service` done; no placeholder
  left (`sudo grep -c PENDING /etc/msf-assistant/hosted.env` prints 0).
- Two MSF accounts: **Player A** (operator) and **Player B** (a second, consenting
  tester). Each has ChatGPT (developer-mode/plugins enabled) and Claude with
  custom connectors allowed.
- The local Mac stdio connection stays configured until step 9 passes.

## 1. First connection — Player A, Claude

1. Claude → Customize → Connectors → Add custom connector →
   `https://<host>/mcp` (shown under your `MSF_SERVICE_NAME`). If asked for the OAuth client, choose
   **Register automatically**.
2. Browser opens `<host>` → "Sign in with MSF" → MSF login as A →
   consent page shows "Claude (claude.ai)", the connection ID, **Connected MSF
   account** = first 8 characters of A's subject, and three permissions.
3. Allow → Claude reports the connector as connected.
4. In Claude: "Call get_status." → `available: false` (no snapshot yet).
5. "Run refresh_data." → status now `available: true` with a fresh timestamp.
6. "Show my profile." → A's real profile (name/level/power).
   "What would my strongest character gain at gear tier 18?" → a
   `project_character` call with a power figure (live MSF query, read scope).
7. New conversation: "Who should I upgrade next?" → the assistant follows the
   guide unprompted: reads `get_status`/`get_advisor_context` (or `get_guide`)
   before advising, states the roster age, and asks only decisive questions.

Pass: steps 2–7 as described. Fail if consent lacks the account line, if
scopes are only read (refresh_data returns a 403 "write scope" error), or if
the client asks for a client secret.

## 2. Same player, second client — Player A, ChatGPT

1. ChatGPT → Settings → Security and sign-in → Developer mode → Plugins → add
   `https://<host>/mcp` → Connect → MSF login as A (may be skipped if
   the browser session is still valid) → consent shows "ChatGPT (chatgpt.com)".
2. "Show my profile." → same data as in Claude **without** running refresh_data.
3. "Save a goal: unlock Apocalypse." (`save_goal`) → success with a revision.
4. Back in Claude: "Show my advisor context." → the goal saved from ChatGPT is
   present.

Pass: context is shared across A's clients; snapshot fetched once.

## 3. Isolation — Player B, either client

1. Connect B's own client (Claude or ChatGPT) to the same address; sign in as
   **B** at MSF; consent shows B's account prefix.
2. "Show my advisor context." → **empty**, no trace of A's goal.
3. "Show my profile." → prompts to run refresh_data (no data yet); after
   refresh_data → B's profile, not A's.
4. In A's client: "Show my advisor context." → still only A's goal.
5. Manipulation: in B's client, ask the assistant to call `save_goal` with an
   extra argument `player_id` set to A's id → tool error "Unknown tool
   argument"; A's context unchanged.

Pass: no cross-account data in any direction.

## 4. Concurrency — Player A, both clients

1. In ChatGPT: "Save a player fact: I am F2P." (`save_player_fact`).
2. In Claude, in parallel, save another fact.
3. Both succeed or exactly one reports a revision conflict and succeeds on
   retry; the context afterwards contains both facts.

## 5. Revocation — Player A

1. Open `https://<host>/account` (browser session) → both
   connections listed with client labels and permissions.
2. Revoke the **ChatGPT** connection.
3. ChatGPT: any tool call fails with an authentication error; Claude still
   works.
4. Reconnect ChatGPT (same connector entry, "reconnect"/re-authorize) → works
   again without removing and re-adding the connector.

Pass: revocation is per connection; reconnect succeeds (client registration
survived).

## 6. Restart persistence

1. `sudo systemctl restart msf-assistant.service`; wait for `/health`.
2. Claude and ChatGPT (A) and B's client all keep working **without**
   re-authorizing; A's context still contains goal and facts.

## 7. Token expiry and refresh

1. Wait > 15 minutes without using A's Claude connector.
2. Use it again → works transparently (refresh token used; no re-login).

## 8. Account deletion — Player B

1. B opens `/account` → "Delete account" → confirm.
2. B's client: tool calls fail with authentication error.
3. Server: `sudo ls /var/lib/msf-assistant/state/players/` no longer contains
   B's UUID directory.
4. B signs in again via a client → new empty account; "Show my advisor
   context." is empty.

## 9. Mac off

1. Quit the local stdio server / shut the Mac down.
2. From a phone or second machine, use ChatGPT or Claude (A) → profile and
   context still answer.

This is the acceptance criterion for replacing a local stdio setup ("access with the Mac off").
Only after this step switch the local connection over.

## 10. Backup and restore drill (operator, service stopped)

```
sudo docker compose --env-file /etc/msf-assistant/hosted.env -f /opt/msf-assistant/deploy/compose.yaml \
  exec assistant python -m msf_assistant hosted backup /data/backups --retention 7
sudo systemctl stop msf-assistant.service
sudo docker compose --env-file /etc/msf-assistant/hosted.env -f /opt/msf-assistant/deploy/compose.yaml \
  run --rm --no-deps -e MSF_HOSTED_DATA=/data/restore-drill assistant restore /data/backups/<archive>.tar --maintenance
sudo docker compose --env-file /etc/msf-assistant/hosted.env -f /opt/msf-assistant/deploy/compose.yaml \
  run --rm --no-deps -e MSF_HOSTED_DATA=/data/restore-drill assistant resume --deletions-reconciled
sudo rm -rf /var/lib/msf-assistant/restore-drill   # drill only; do not swap state
sudo systemctl start msf-assistant.service
```

Pass: backup archive created; restore validates and resumes; after the start the
live service is unchanged and clients still work. (Restored tokens would fail by
design — the drill does not swap the state directory.)

## 11. Load and headroom

With both players connected: run `refresh_data` for A and B at the same time
and a third refresh immediately after → the third gets "Refresh is busy";
`free -m` on the host keeps > 150 MiB available; `docker stats` for the
container stays under 384 MiB; no OOM in `dmesg`.

## Result

| Step | Result | Date | Notes |
| --- | --- | --- | --- |
| 1–11 | | | |

All steps pass → the instance is ready for other players. Any failure → keep
it private and file the failing step with the observed message (no secrets).
