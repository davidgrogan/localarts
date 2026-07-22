#!/bin/bash
# One-click "copy my local data to the droplet" routine.
#
# Automates the manual dance this used to take: open an SSH tunnel to the
# droplet's Postgres, wait for it to actually be listening, run
# migrate_to_postgres.py through it, then close the tunnel again --
# whether the migration succeeds or fails.
#
# Setup (one time):
#   cp deploy/push_to_droplet.env.example deploy/push_to_droplet.env
#   nano deploy/push_to_droplet.env   # fill in your droplet IP, SSH user,
#                                      # and the localarts Postgres password
#
# Then just run this script (or double-click "Push to Droplet.command"
# in Finder) any time you want to send your local scan/review/tagging
# work up to the live site. It still asks you to type "yes" before
# wiping the droplet's data -- that confirmation is intentional, see
# migrate_to_postgres.py's own docstring for why.
#
# Requires password-based SSH to work the same way it does when you SSH
# in by hand -- you'll be prompted once, right when the tunnel opens.
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE="deploy/push_to_droplet.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE."
  echo "Copy deploy/push_to_droplet.env.example to $ENV_FILE and fill in your"
  echo "droplet's IP, SSH user, and Postgres password, then run this again."
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${DROPLET_SSH_USER:?Set DROPLET_SSH_USER in $ENV_FILE}"
: "${DROPLET_IP:?Set DROPLET_IP in $ENV_FILE}"
: "${DROPLET_PG_PASSWORD:?Set DROPLET_PG_PASSWORD in $ENV_FILE}"
LOCAL_TUNNEL_PORT="${LOCAL_TUNNEL_PORT:-5433}"

CONTROL_SOCKET="/tmp/localarts_push_tunnel_$$"

cleanup() {
  if [ -S "$CONTROL_SOCKET" ]; then
    echo "Closing SSH tunnel..."
    ssh -S "$CONTROL_SOCKET" -O exit "${DROPLET_SSH_USER}@${DROPLET_IP}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Opening SSH tunnel to ${DROPLET_IP} (you may be asked for your password)..."
if ! ssh -f -N -M -S "$CONTROL_SOCKET" \
    -L "${LOCAL_TUNNEL_PORT}:localhost:5432" \
    "${DROPLET_SSH_USER}@${DROPLET_IP}"; then
  echo "Could not open the SSH tunnel -- check DROPLET_IP/DROPLET_SSH_USER in $ENV_FILE."
  exit 1
fi

echo "Waiting for the tunnel to come up..."
for _ in $(seq 1 15); do
  if nc -z localhost "$LOCAL_TUNNEL_PORT" 2>/dev/null; then
    break
  fi
  sleep 1
done
if ! nc -z localhost "$LOCAL_TUNNEL_PORT" 2>/dev/null; then
  echo "Tunnel never came up on port $LOCAL_TUNNEL_PORT -- aborting."
  exit 1
fi
echo "Tunnel is up."

python3 migrate_to_postgres.py \
  "postgresql://localarts:${DROPLET_PG_PASSWORD}@localhost:${LOCAL_TUNNEL_PORT}/localarts"
