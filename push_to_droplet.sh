#!/bin/bash
# One-click "copy my local data to the droplet" routine.
#
# Automates the manual dance this used to take: open an SSH tunnel to the
# droplet's Postgres, wait for it to actually be listening, rsync over
# any uploaded flyer/venue-photo files, run migrate_to_postgres.py
# through the tunnel, then close the connection again -- whether either
# step succeeds or fails.
#
# The rsync step exists because Event.image_url/Venue.image_url rows
# copied by migrate_to_postgres.py can point at a locally-uploaded file
# (app/static/uploads/flyers/<uuid>.jpg) -- that directory is gitignored
# (real uploaded images, not something to commit), so `git pull` on the
# droplet never puts the actual file there. Without this, a locally
# uploaded image's URL syncs over fine but 404s on the live site because
# the file itself never did. (See app/utils.py's flyer_url() docstring
# for the related fix making that URL relative instead of baking in
# whatever host happened to be serving the page at upload time --
# necessary but not sufficient on its own; the file still has to
# actually get there.)
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
# Also requires rsync on your Mac (ships with macOS by default).
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
DROPLET_APP_DIR="${DROPLET_APP_DIR:-/var/www/localarts}"
FLYER_DIR="app/static/uploads/flyers"

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

# Nothing to sync yet on a brand-new local install -- rsync would
# otherwise error on a missing source directory rather than just finding
# zero files.
mkdir -p "$FLYER_DIR"

echo "Syncing uploaded flyer/venue-photo images..."
ssh -S "$CONTROL_SOCKET" "${DROPLET_SSH_USER}@${DROPLET_IP}" \
  "mkdir -p '${DROPLET_APP_DIR}/${FLYER_DIR}'"
rsync -az -e "ssh -S $CONTROL_SOCKET" \
  "${FLYER_DIR}/" "${DROPLET_SSH_USER}@${DROPLET_IP}:${DROPLET_APP_DIR}/${FLYER_DIR}/"

python3 migrate_to_postgres.py \
  "postgresql://localarts:${DROPLET_PG_PASSWORD}@localhost:${LOCAL_TUNNEL_PORT}/localarts"
