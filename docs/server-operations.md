# Hosted MSF Assistant 0.4.0

This bundle is ready for local verification, not evidence of a public deployment.
Public release requires a maintained host OS, approval/configuration of the MSF
application for multiple players and the exact redirect
`https://msf.andywhv.de/oauth/callback`, and real ChatGPT/Claude acceptance tests.
The existing macOS local service and Keychain remain independent.

## Runtime and host prerequisites

Run **exactly one container with one Uvicorn worker**. The CLI always sets
`workers=1`, including when `WEB_CONCURRENCY` is present. Multiple replicas or
workers are unsupported because admission, rate and sync counters are local to
the process. Player and maintenance locks additionally serialize separate
operator processes. Lock order is maintenance → player → SQLite; do not hold a
SQL transaction while waiting for a player lock.

The host requires Docker and the Docker Compose **plugin v2.20 or later**.
The preflight host has Docker 24.0.5 and legacy `docker-compose` 1.25.0, which is
not sufficient; install the supported plugin before deployment. The existing
Ubuntu 20.04 installation needs a maintained OS path before public release.
Do not change other nginx sites or perform an OS upgrade as an installation side
effect. Build on a separate machine for `linux/amd64`, not on the small server.

| Host path | Purpose / permissions |
| --- | --- |
| `/opt/msf-assistant/deploy/compose.yaml` | Reviewed release configuration, root-owned |
| `/etc/msf-assistant/hosted.env` | Configuration only, root-owned 0600 |
| `/etc/msf-assistant/secrets/` | Service UID 10001, directory 0700 |
| `/etc/msf-assistant/secrets/encryption.key` | Fernet key, UID 10001, 0600 |
| `/etc/msf-assistant/secrets/msf-client-secret` | MSF app secret, UID 10001, 0600 |
| `/var/lib/msf-assistant/` | Dedicated persistent bind source, UID 10001, 0700 |
| `/var/lib/msf-assistant/state/` | SQLite, encrypted tokens, player snapshots/context; 0700/0600 |
| `/var/lib/msf-assistant/backups/` | Private local archives; 0700/0600 |
| `/etc/nginx/sites-available/msf.andywhv.de` (+ `sites-enabled` symlink) | New dedicated nginx vhost |
| `/etc/systemd/system/msf-assistant.service` | Reviewed systemd wrapper |

`/var` must be a separately mounted filesystem. The systemd unit supervises attached Compose and restarts it after either clean
or failed container exit; an intentional systemd stop does not restart it.
Compose uses `restart: "no"` so a Docker daemon restart cannot bypass the mount
guard. Every systemd restart runs the guard before Compose. The unit declares
`RequiresMountsFor`, a mount condition and an explicit `mountpoint` check; never
start Compose manually without the same check. Compose refuses missing bind
sources. The container checks that `/data` is a mount and that its state root is
beneath it. There is no fallback to the root filesystem. No secrets belong in an
image, repository, command argument, environment value or backup archive.

Create host directories and secret files as UID 10001 before startup. Generate
the encryption key using `python -m msf_assistant hosted generate-key PATH` in a
trusted installed environment as the service UID, or the built image with a
writable mount of the private secrets directory. Generation uses exclusive
creation, 0600 and no secret output; it will not overwrite an existing key.
Provision the MSF client secret as a separate 0600 file through the operator's
secure secret channel. Do not paste it into shell history.

Copy `hosted.env.example` to the configuration path and set the public origin,
registered app ID, immutable image digest (or a unique commit release tag) and
the operator identity (`MSF_OPERATOR_NAME`, `MSF_OPERATOR_ADDRESS`,
`MSF_OPERATOR_EMAIL`). The service refuses to start without the operator
identity; it is rendered as responsible party on `/privacy.html` and as provider
on `/terms.html`, so a public instance is never anonymous. Both pages are drafts
written to match the implemented data flows; review them before public use.
The secret-file paths are references. Plaintext `MSF_CLIENT_SECRET` is not read
by hosted commands. Hosted commands never load local `.env` or Keychain data.
For host CLI use, `MSF_HOSTED_MOUNT=/var`; Compose overrides the mount to `/data`
and the state to `/data/state`.

## Build, start, observe

```
docker build --platform linux/amd64 -t msf-assistant:0.4.0-COMMIT .
docker save -o msf-assistant-0.4.0-COMMIT.tar msf-assistant:0.4.0-COMMIT
```

Transfer this artifact privately to the operator and `docker load` it on the
server. Record its SHA-256 and image ID. The Docker context is an allowlist of
source and the hashed runtime lock; personal files and test fixtures cannot be
included. The official Python 3.12.14 image is pinned by digest and every runtime
package is pinned with hashes. MCP remains 2.2.0 for its tested SDK adapters.
No package resolution or build happens at server startup.

`deploy/install-host.sh <image-tag> [--nginx]` performs the host layout above
idempotently as root: service identity 10001, private directories, `hosted.env`
(only if absent; otherwise only `MSF_IMAGE` is updated), a placeholder client
secret, key generation inside the image, the systemd unit, a health wait and,
with `--nginx`, the vhost in `sites-available` plus its `sites-enabled` symlink behind `nginx -t`. It never prints secrets and never
overwrites an existing key, secret or env file. Replace the placeholder
`MSF_CLIENT_ID` and secret file after MSF app registration, then
`systemctl restart msf-assistant.service`.

Install the reviewed systemd unit, run `systemctl daemon-reload`, and enable/start
`msf-assistant.service` after validating prerequisites. Compose publishes only
`127.0.0.1:8000`; Uvicorn binds the container interface explicitly. `/health`
returns only service health, with no player state. Root filesystem is read-only,
UID/GID is 10001, capabilities are dropped, new privileges are disabled, scratch
is a 16 MiB tmpfs, logs rotate at 3 × 5 MiB, and the container has a 384 MiB
memory/no-swap cap, one CPU and 64 PID limit. This cap contains failures; it does
not prove sufficient host capacity.

The hosted CLI defaults to two active public requests. Set
`MSF_HOSTED_ACTIVE_REQUESTS` to an integer from 1 through 4 only after measured
capacity validation; the provided Compose fixes it to 2. Sync capacity is two;
the admission limit also applies to sync requests. JSON byte limits do not bound
Python object expansion for arbitrary structures. A synthetic adversarial
snapshot can exhaust container memory; availability under such inputs is a known
limitation. Monitor restarts/OOM status and free host RAM; do not enable public
traffic solely because the health check passes. A production load test must also
leave headroom for nginx, existing applications and the OS.

Use `nginx.conf.example` as the dedicated vhost. The public name is
`msf.andywhv.de`; the `andywhv.de` Route53 zone already resolves it through its
wildcard `A` record to this host, so no DNS change is needed. The host's
`andywhv.de` Let's Encrypt certificate is a wildcard (`*.andywhv.de`) and covers
this name; the vhost shares it like the other `andywhv.de` sites. That
certificate is renewed **manually** (DNS-01, `authenticator = manual`), so put
its expiry on the operator calendar or switch it to the `certbot-dns-route53`
plugin now that Route53 access exists. Run `nginx -t` before reloading. No TLS bypass is allowed. Access logs
use `$uri`, never query strings or full requests. This vhost's nginx error log is
disabled because error context can retain OAuth callback queries. Use sanitized
access status, health and container status for diagnosis. Uvicorn access logging
is disabled. Apply bounded host nginx log rotation (for example daily, seven
rotations, compressed); verify the host's existing logrotate rule covers this
file. Preserve other vhosts and certificate renewal configuration.

## Coherent backup and retention

From `/opt/msf-assistant/deploy`, with the configured image and service running:

```
docker compose --env-file /etc/msf-assistant/hosted.env exec assistant \
  python -m msf_assistant hosted backup /data/backups --retention 7
```

Backup acquires an exclusive service maintenance lock (bounded wait), uses
SQLite's online backup API, and archives the matching player files while all
mutations are blocked. Retry a busy result later; never copy the live database
and player files separately. OAuth and browser mutations participate in the
same lock. Archives contain only the database and UUID player snapshot/context
files, including encrypted upstream credentials. Installation keys and app
secrets are excluded. Authentication metadata and snapshots are still private:
protect archives as personal data. The archive is limited to 512 MiB, 20,000
files, 128 MiB per database and 24 MiB per player file; archive growth beyond
these bounds fails closed and requires a reviewed capacity change. Retention is
1–30 archives and removes only matching private backup files after success.

**Same-host copies do not protect against server/disk loss.** After each backup,
copy the archive over an authenticated encrypted channel to a separate managed
backup host. Verify its checksum and private permissions there; use an encrypted
backup repository with its own retention policy. Keep the encryption key and
MSF app secret in a separately controlled encrypted secret backup, never in the
same tar archive. Losing the encryption key makes credential recovery impossible.
Regularly test restoration on an isolated machine with network disabled.

## Restore, deletion reconciliation and rollback

Close public access and stop the **systemd unit**, not just the container,
before a disaster restore: the unit uses `Restart=always`, so a bare
`docker compose stop` would be undone 30 s later, possibly mid-restore or before
the rename below. Preserve the old state directory unchanged for rollback;
**never extract over it**. Retrieve the verified archive and matching key. Set
`MSF_HOSTED_DATA` to a new directory under `/data` for the one-off container:

```
systemctl stop msf-assistant.service
systemctl is-active msf-assistant.service   # must print: inactive
docker compose --env-file /etc/msf-assistant/hosted.env run --rm --no-deps \
  -e MSF_HOSTED_DATA=/data/restored-NEW assistant \
  restore /data/backups/SELECTED.tar --maintenance
```

Restore validates paths, types, duplicate members, size, SQLite integrity,
foreign keys and supported schema; it rejects symlinks/hardlinks and any existing
destination. It verifies the matching encryption key, clears every restored MCP
client registration, grant, access/refresh token, authorization request/code and
browser session, and writes `RESTORE_MAINTENANCE`. `serve` refuses to start while
that marker exists. Every client must reconnect and perform fresh verified MSF
login; old bearer tokens and cookies fail.

Before reopening registration, reconcile **all account deletions after the
backup date** using the operator's deletion records or a recoverable newer state.
An old archive can otherwise resurrect an account that the player deleted.
Keep those records private and retain only what is needed to enforce deletion.
For each deleted UUID in the restored database, first enter
`store.player_lock(UUID)` (it requires an *active* player and fails afterwards),
then call `HostedStore.deactivate_player(UUID)` and remove its `players/UUID`
directory while still holding that lock; all restored OAuth/browser state is
already invalidated. Backups skip crash leftovers named `.msf-*` inside a player
directory; remove them by hand during reconciliation if present. Do this in an offline
operator session using the matching key file, never an SQL edit against a live
service. If the deletion history is unavailable, do not reopen this restored
state: start with a clean root and fresh registrations instead.

After verified reconciliation, run the explicit attestation against that root:

```
docker compose --env-file /etc/msf-assistant/hosted.env run --rm --no-deps \
  -e MSF_HOSTED_DATA=/data/restored-NEW assistant resume --deletions-reconciled
```

With the service still stopped, rename the old `state` to a preserved rollback
name and the new root to `state` on the same filesystem. Check ownership/modes,
start via systemd, confirm health and isolated player access, and then reopen
nginx. A rollback uses the previous tested image and a schema-compatible state
copy; never downgrade against a schema you have not tested. To roll back restored
data, stop first and restore another archive through the same invalidation and
deletion-reconciliation procedure. Do not reuse copied authorization state.

## Key rotation and existing local snapshot

Version 0.4.0 has no automatic in-place rekey command. Never replace the key file
under an existing database: key binding deliberately refuses it. For planned
rotation, stop the service and keep a verified separate backup/key pair. Create
a new private key and clean state, require fresh verified player registrations,
and migrate each player's snapshot/context only after matching the independently
verified issuer and subject to the former record. Do not transfer old OAuth,
browser or upstream credentials. Test the new state before deleting the old key.
If a key or upstream app credential is compromised, revoke affected upstream
credentials at MSF and replace the application secret through the operator's
secure channel as well; a local file change alone cannot revoke exposed tokens.

The existing user's local snapshot is **not** automatically imported. First
complete hosted MSF login and independently verify the same official issuer and
subject. With the service stopped, validate the snapshot, copy only the snapshot
(and separately validated advisor context if requested) to that verified UUID's
private directory as UID 10001/0600, and test access from a fresh client. Never
copy `.env`, Keychain exports, local tokens or another player's files. Keep the
local runtime available until explicit cutover acceptance.

## Client connection

The public help page displays the configured origin plus `/mcp` and German setup
steps. Clients use automatic registration and OAuth/PKCE; players never supply
MSF application secrets. Follow current official [ChatGPT setup](https://developers.openai.com/plugins/deploy/connect-chatgpt)
and [Claude setup](https://claude.com/docs/connectors/custom/remote-mcp).
Workspace policies and plans can restrict custom connectors. DCR is supported;
CIMD fetching is not implemented, so when Claude's connector dialog offers
"published identity" (its default), "register automatically" or "own client",
players must choose **register automatically** — the help page says so. Protocol tests are not proof that the current
real clients accept this deployment; complete both real-client acceptance tests
before declaring release complete.
