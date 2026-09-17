#!/bin/bash
# Idempotent host installation for the hosted service. Run as root on the
# server from a directory that holds the loaded image's deploy files
# (compose.yaml, msf-assistant.service, nginx.conf.template; default: the
# directory of this script). Creates the service identity, private directories,
# configuration, key and systemd unit; optionally the nginx vhost. It never
# prints secret values and never overwrites an existing key, secret or env file.
#
#   sudo PUBLIC_URL=https://advisor.example.com bash install-host.sh <image-tag> [--nginx]
#   sudo bash install-host.sh msf-assistant:0.4.0-abc1234            # later upgrades
#
# Environment:
#   PUBLIC_URL     public origin; required on first install, otherwise taken
#                  from the existing /etc/msf-assistant/hosted.env
#   SERVICE_NAME   page title / MCP server name (default: Strike Advisor)
#   CERT_NAME      /etc/letsencrypt/live/<CERT_NAME>/ (default: the host name)
#   NGINX_REPLACE  set to 1 to re-render an existing vhost file
#   STAGE          directory with the deploy files (default: this script's dir)
set -euo pipefail

IMAGE="${1:?image tag, e.g. msf-assistant:0.4.0-abc1234}"
WITH_NGINX="${2:-}"
STAGE="${STAGE:-$(cd "$(dirname "$0")" && pwd)}"
ENV_FILE=/etc/msf-assistant/hosted.env
SERVICE_UID=10001
PLACEHOLDER="PENDING-MSF-APP-REGISTRATION"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
mountpoint -q /var || { echo "/var is not a separate mount; refusing" >&2; exit 1; }
docker compose version >/dev/null || { echo "docker compose plugin missing" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null || { echo "image $IMAGE not loaded; docker load it first" >&2; exit 1; }
for f in compose.yaml msf-assistant.service nginx.conf.template; do
  [ -f "$STAGE/$f" ] || { echo "missing $STAGE/$f" >&2; exit 1; }
done

if [ -z "${PUBLIC_URL:-}" ] && [ -f "$ENV_FILE" ]; then
  PUBLIC_URL="$(sed -n 's/^MSF_PUBLIC_URL=//p' "$ENV_FILE" | head -1)"
fi
[ -n "${PUBLIC_URL:-}" ] || { echo "PUBLIC_URL is required on first install" >&2; exit 1; }
case "$PUBLIC_URL" in https://*) ;; *) echo "PUBLIC_URL must be an https:// origin" >&2; exit 1;; esac
HOST="${PUBLIC_URL#https://}"; HOST="${HOST%%/*}"
case "$(echo "$HOST" | tr 'A-Z' 'a-z' | tr '-' ' ')" in
  *msf*|*marvel*|*strike\ force*|*strikeforce*|*scopely*)
    echo "host name must not contain a Scopely/Marvel mark (MSF API Terms of Use)" >&2; exit 1;;
esac
SERVICE_NAME="${SERVICE_NAME:-Strike Advisor}"
CERT_NAME="${CERT_NAME:-$HOST}"

echo "== service identity"
getent group "$SERVICE_UID" >/dev/null || groupadd -g "$SERVICE_UID" msf-assistant
getent passwd "$SERVICE_UID" >/dev/null || \
  useradd -r -u "$SERVICE_UID" -g "$SERVICE_UID" -M -d /nonexistent -s /usr/sbin/nologin msf-assistant

echo "== directories"
install -d -m 0700 -o "$SERVICE_UID" -g "$SERVICE_UID" \
  /var/lib/msf-assistant /var/lib/msf-assistant/state /var/lib/msf-assistant/backups
install -d -m 0755 -o root -g root /etc/msf-assistant /opt/msf-assistant /opt/msf-assistant/deploy
install -d -m 0700 -o "$SERVICE_UID" -g "$SERVICE_UID" /etc/msf-assistant/secrets
install -m 0644 -o root -g root "$STAGE/compose.yaml" /opt/msf-assistant/deploy/compose.yaml

echo "== configuration ($HOST)"
if [ ! -e "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<ENV
# Configuration only. Never place secret values here.
MSF_IMAGE=$IMAGE
MSF_PUBLIC_URL=$PUBLIC_URL
MSF_SERVICE_NAME=$SERVICE_NAME
# Placeholder until the MSF app is registered with callback $PUBLIC_URL/oauth/callback
MSF_CLIENT_ID=$PLACEHOLDER
# Shown on /privacy.html and /terms.html; replace before public use.
MSF_OPERATOR_NAME=REPLACE_WITH_OPERATOR_NAME
MSF_OPERATOR_ADDRESS=REPLACE_WITH_POSTAL_ADDRESS
MSF_OPERATOR_EMAIL=REPLACE_WITH_CONTACT@example.invalid
# Container overrides these paths; CLI on the host uses these values.
MSF_HOSTED_DATA=/var/lib/msf-assistant/state
MSF_HOSTED_MOUNT=/var
MSF_HOSTED_KEY_FILE=/etc/msf-assistant/secrets/encryption.key
MSF_CLIENT_SECRET_FILE=/etc/msf-assistant/secrets/msf-client-secret
MSF_HOSTED_ACTIVE_REQUESTS=2
ENV
  umask 022
else
  echo "   keeping existing $ENV_FILE"
  sed -i "s|^MSF_IMAGE=.*|MSF_IMAGE=$IMAGE|" "$ENV_FILE"
  sed -i "s|^MSF_PUBLIC_URL=.*|MSF_PUBLIC_URL=$PUBLIC_URL|" "$ENV_FILE"
  for pair in "MSF_SERVICE_NAME=$SERVICE_NAME" \
              MSF_OPERATOR_NAME=REPLACE_WITH_OPERATOR_NAME \
              MSF_OPERATOR_ADDRESS=REPLACE_WITH_POSTAL_ADDRESS \
              MSF_OPERATOR_EMAIL=REPLACE_WITH_CONTACT@example.invalid; do
    grep -q "^${pair%%=*}=" "$ENV_FILE" || {
      printf '%s\n' "$pair" >> "$ENV_FILE"
      echo "   added ${pair%%=*}; review it before public use"
    }
  done
fi
chown root:root "$ENV_FILE"; chmod 0600 "$ENV_FILE"

echo "== secrets"
if [ ! -e /etc/msf-assistant/secrets/msf-client-secret ]; then
  (umask 077; printf '%s' "$PLACEHOLDER" > /etc/msf-assistant/secrets/msf-client-secret)
  chown "$SERVICE_UID:$SERVICE_UID" /etc/msf-assistant/secrets/msf-client-secret
  echo "   placeholder client secret written; replace it after MSF app registration"
fi
chmod 0600 /etc/msf-assistant/secrets/msf-client-secret
if [ ! -e /etc/msf-assistant/secrets/encryption.key ]; then
  docker run --rm --user "$SERVICE_UID:$SERVICE_UID" --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --network none \
    -v /etc/msf-assistant/secrets:/run/secrets "$IMAGE" generate-key /run/secrets/encryption.key
fi

echo "== systemd"
install -m 0644 -o root -g root "$STAGE/msf-assistant.service" /etc/systemd/system/msf-assistant.service
systemctl daemon-reload
systemctl enable msf-assistant.service
systemctl restart msf-assistant.service   # first start, or switch to the new MSF_IMAGE
for _ in $(seq 1 30); do
  sleep 2
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
done
systemctl --no-pager --lines=0 status msf-assistant.service | head -3
echo "   health: $(curl -sS http://127.0.0.1:8000/health)"

if [ "$WITH_NGINX" = "--nginx" ]; then
  echo "== nginx ($HOST, certificate $CERT_NAME)"
  SITE="/etc/nginx/sites-available/$HOST"
  if [ -e "$SITE" ] && [ "${NGINX_REPLACE:-0}" != "1" ]; then
    echo "   keeping existing $SITE (set NGINX_REPLACE=1 to re-render)"
    ln -sfn "$SITE" "/etc/nginx/sites-enabled/$HOST"
  else
    [ -f "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" ] || {
      echo "certificate /etc/letsencrypt/live/$CERT_NAME/fullchain.pem missing; issue it first" >&2
      exit 1
    }
    [ -e "$SITE" ] && cp -p "$SITE" "$SITE.previous"
    sed -e "s|__HOST__|$HOST|g" -e "s|__CERT_NAME__|$CERT_NAME|g" "$STAGE/nginx.conf.template" > "$SITE.new"
    install -m 0644 -o root -g root "$SITE.new" "$SITE"; rm -f "$SITE.new"
    ln -sfn "$SITE" "/etc/nginx/sites-enabled/$HOST"
  fi
  if nginx -t; then
    systemctl reload nginx
    rm -f "$SITE.previous"
  else
    if [ -e "$SITE.previous" ]; then
      mv "$SITE.previous" "$SITE"; echo "nginx config test failed; previous vhost restored" >&2
    else
      rm -f "/etc/nginx/sites-enabled/$HOST" "$SITE"; echo "nginx config test failed; vhost removed" >&2
    fi
    exit 1
  fi
fi

echo "== done"
ls -ld /var/lib/msf-assistant/state /etc/msf-assistant/secrets/encryption.key
docker ps --filter name=msf-assistant --format '{{.Names}} {{.Status}}'
echo "   public: $PUBLIC_URL  (MSF app callback: $PUBLIC_URL/oauth/callback)"
