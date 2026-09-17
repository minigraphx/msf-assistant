# Hosted service operations (0.4.0)

This guide describes how to run the multi-player service on your own host.
`<host>` stands for your public host name (for example `advisor.example.com`);
nothing in the bundle is tied to a specific domain. Public release requires a
maintained host OS, an MSF application registered with the exact redirect
`https://<host>/oauth/callback`, and real ChatGPT/Claude acceptance tests
(`docs/hosted-acceptance.md`). The local macOS stdio mode and its Keychain
remain independent.

## Runtime and host prerequisites

Run **exactly one container with one Uvicorn worker**. The CLI always sets
`workers=1`, including when `WEB_CONCURRENCY` is present. Multiple replicas or
workers are unsupported because admission, rate and sync counters are local to
the process. Player and maintenance locks additionally serialize separate
operator processes. Lock order is maintenance → player → SQLite; do not hold a
SQL transaction while waiting for a player lock.

The host requires Docker and the Docker Compose **plugin v2.20 or later**
(legacy `docker-compose` 1.x is not sufficient), nginx with a certificate for
`<host>`, a separately mounted `/var`, and a maintained OS with security
updates. Do not change other nginx sites or perform an OS upgrade as an
installation side effect. Build the image on a separate machine for
`linux/amd64` rather than on a small server.

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
| `/etc/nginx/sites-available/<host>` (+ `sites-enabled` symlink) | Dedicated nginx vhost rendered from `nginx.conf.template` |
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

`sudo PUBLIC_URL=https://<host> bash deploy/install-host.sh <image-tag> [--nginx]`
performs the host layout above idempotently as root: service identity 10001,
private directories, `hosted.env` (only if absent; on later runs only
`MSF_IMAGE`/`MSF_PUBLIC_URL` are updated and missing keys appended), a
placeholder client secret, key generation inside the image, the systemd unit, a
health wait and, with `--nginx`, the vhost rendered from `nginx.conf.template`
into `sites-available/<host>` plus its `sites-enabled` symlink behind `nginx -t`
(an existing vhost is kept unless `NGINX_REPLACE=1`; a failed test restores the
previous file). `SERVICE_NAME` sets the page title, `CERT_NAME` the Let's
Encrypt directory when the certificate is not named after the host (for
example a zone wildcard). The script never prints secrets and never overwrites
an existing key, secret or env file. Replace the placeholder `MSF_CLIENT_ID`,
the secret file and the operator lines after MSF app registration, then
`systemctl restart msf-assistant.service`. On later upgrades the URL is read
from the existing env, so `sudo bash install-host.sh <new-tag>` is enough.

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

Use `nginx.conf.template` for the dedicated vhost (the install script renders
it). The name must not contain a Scopely or Marvel mark (see the API terms
section below). A certificate for `<host>` must exist under
`/etc/letsencrypt/live/<CERT_NAME>/` before the vhost is enabled; issue it with
certbot (HTTP-01 through nginx, or a DNS-01 wildcard for the zone) and make
sure its renewal reloads nginx. Run `nginx -t` before reloading. No TLS bypass is allowed. Access logs
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
same lock. Archives contain only the database and UUID player context files,
including encrypted upstream credentials; game data snapshots are never archived
(MSF API terms: 30-day TTL and immediate deletion on request; they are
re-fetched with `refresh_data`). Installation keys and app secrets are excluded. Authentication metadata and snapshots are still private:
protect archives as personal data. The archive is limited to 512 MiB, 20,000
files, 128 MiB per database and 24 MiB per player file; archive growth beyond
these bounds fails closed and requires a reviewed capacity change. Retention is
1–30 archives and at most `--max-age-days` (default and maximum 30) days; both
remove only matching private backup files after success.

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
For each deleted UUID in the restored database run
`hosted delete-player UUID` against the restored root (with `MSF_HOSTED_DATA`
pointing at it); it takes the player lock, deactivates the player, revokes all
grants and removes `players/UUID`, exactly like the account page. Backups skip
crash leftovers named `.msf-*` inside a player directory; remove them by hand
during reconciliation if present. Do this while the service is
stopped, using the matching key file, never an SQL edit against a live
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

## Obligations under the MSF API Terms of Use

Register your instance as the Application: name = `MSF_SERVICE_NAME` (default
"Strike Advisor"), URL = `MSF_PUBLIC_URL`, callback `https://<host>/oauth/callback`,
privacy policy `https://<host>/privacy.html`, terms `https://<host>/terms.html`.
Neither the name nor the host may contain a Scopely or Marvel mark (MSF, Marvel,
Strike Force, Scopely); the configuration refuses such values. Every page names
Scopely as the source of the game data without implying endorsement. The
implementation enforces:

- **30-day TTL on Data**: a player's snapshot older than 30 days is deleted on
  access and tools ask for `refresh_data`; snapshots are never written to
  backups; archives are deleted after `--max-age-days` (default and maximum 30).
- **Deletion on request**: `/account` deletion or `hosted delete-player UUID`
  removes the snapshot immediately; backups hold no game data. Requests that
  reach the operator through Scopely must be executed promptly.
- **Privacy notice** states that Data is provided "as is", what is collected
  and the retention above.

Operator duties that no code can perform:

- **Breach notice within 24 hours**: if you believe the API key or any player
  Data was accessed or disclosed by an unauthorized party, notify Scopely
  (legal address in the API terms) within 24 hours with all circumstances, and
  rotate the client secret (new 0600 file, restart the service).
- **Termination by Scopely**: stop the service (`systemctl disable --now
  msf-assistant.service`), remove the nginx site, delete
  `/var/lib/msf-assistant/state/players/*/snapshot.json` and all archives under
  `/var/lib/msf-assistant/backups`, then delete the state directory and the
  off-host copies. Keep no copy of Data.
- Keep the registration data at the Scopely developer site current and the API
  key confidential; never share it with players or third-party services.
- Do not add paid tiers, donation gates, advertising, or any monetization of
  the Data, and do not target players under 13.

## Client connection

The nginx vhost rate-limits the unauthenticated surface per client address
(registration/token 6 per minute, browser login pages 30 per minute, `/mcp`
120 per minute with bursts) and caps OAuth request bodies at 16 KiB; the
application additionally limits authenticated players. Failures inside the
service log only the route and the exception class name at WARNING (visible in
`docker logs`), never messages, parameters or credentials.

The public help page (English) displays the configured origin plus `/mcp` and setup
steps. Clients use automatic registration and OAuth/PKCE; players never supply
MSF application secrets. Follow current official [ChatGPT setup](https://developers.openai.com/plugins/deploy/connect-chatgpt)
and [Claude setup](https://claude.com/docs/connectors/custom/remote-mcp).
Workspace policies and plans can restrict custom connectors. DCR is supported;
CIMD fetching is not implemented, so when Claude's connector dialog offers
"published identity" (its default), "register automatically" or "own client",
players must choose **register automatically** — the help page says so. Protocol tests are not proof that the current
real clients accept this deployment; complete the real-client acceptance script
in `docs/hosted-acceptance.md` before declaring release complete.
