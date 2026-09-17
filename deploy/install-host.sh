#!/bin/bash
# Idempotent host installation for the hosted MSF Assistant. Run as root on the
# server after the image tar and the deploy files are in $STAGE (default:
# /home/ubuntu/msf-deploy). Creates the service identity, private directories,
# configuration, key and systemd unit; optionally the nginx vhost. It never
# prints secret values and never overwrites an existing key, secret or env file.
#
#   sudo bash install-host.sh <image-tag> [--nginx]
#   sudo bash install-host.sh msf-assistant:0.4.0-b24a030 --nginx
set -euo pipefail

IMAGE="${1:?image tag, e.g. msf-assistant:0.4.0-b24a030}"
WITH_NGINX="${2:-}"
STAGE="${STAGE:-/home/ubuntu/msf-deploy}"
PUBLIC_URL="${PUBLIC_URL:-https://msf.andywhv.de}"
SERVICE_UID=10001
PLACEHOLDER="PENDING-MSF-APP-REGISTRATION"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
mountpoint -q /var || { echo "/var is not a separate mount; refusing" >&2; exit 1; }
docker compose version >/dev/null || { echo "docker compose plugin missing" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null || { echo "image $IMAGE not loaded; docker load it first" >&2; exit 1; }
for f in compose.yaml msf-assistant.service nginx.conf.example; do
  [ -f "$STAGE/$f" ] || { echo "missing $STAGE/$f" >&2; exit 1; }
done

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

echo "== configuration"
if [ ! -e /etc/msf-assistant/hosted.env ]; then
  umask 077
  cat > /etc/msf-assistant/hosted.env <<ENV
# Configuration only. Never place secret values here.
MSF_IMAGE=$IMAGE
MSF_PUBLIC_URL=$PUBLIC_URL
# Placeholder until the MSF app is registered with callback $PUBLIC_URL/oauth/callback
MSF_CLIENT_ID=$PLACEHOLDER
# Container overrides these paths; CLI on the host uses these values.
MSF_HOSTED_DATA=/var/lib/msf-assistant/state
MSF_HOSTED_MOUNT=/var
MSF_HOSTED_KEY_FILE=/etc/msf-assistant/secrets/encryption.key
MSF_CLIENT_SECRET_FILE=/etc/msf-assistant/secrets/msf-client-secret
MSF_HOSTED_ACTIVE_REQUESTS=2
ENV
  umask 022
else
  echo "   keeping existing /etc/msf-assistant/hosted.env"
  sed -i "s|^MSF_IMAGE=.*|MSF_IMAGE=$IMAGE|" /etc/msf-assistant/hosted.env
fi
chown root:root /etc/msf-assistant/hosted.env; chmod 0600 /etc/msf-assistant/hosted.env

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
  echo "== nginx"
  SITE=/etc/nginx/sites-available/msf.andywhv.de
  install -m 0644 -o root -g root "$STAGE/nginx.conf.example" "$SITE"
  ln -sfn "$SITE" /etc/nginx/sites-enabled/msf.andywhv.de
  if nginx -t; then
    systemctl reload nginx
  else
    rm -f /etc/nginx/sites-enabled/msf.andywhv.de
    echo "nginx config test failed; site disabled, nginx untouched" >&2
    exit 1
  fi
fi

echo "== done"
ls -ld /var/lib/msf-assistant/state /etc/msf-assistant/secrets/encryption.key
docker ps --filter name=msf-assistant --format '{{.Names}} {{.Status}}'
