#!/bin/bash
# One-click "ship everything" routine: commit + push local code changes,
# pull them onto the droplet, sync the droplet's Postgres schema, sync
# your local data over, then restart the service.
#
# This intentionally runs in this order -- code first, schema second, data
# third -- not the order it's sometimes thought of in ("sync the schema,
# then push code"). sync_schema.py compares the live database against
# whatever app/models.py says *on the droplet*, so it has to run after the
# droplet already has today's code, or it'll just compare against
# yesterday's model and find nothing to do.
#
# Reuses push_to_droplet.sh's existing SSH tunnel/config -- see that
# script's own comments for one-time setup (deploy/push_to_droplet.env).
# This adds two more optional settings to that same file:
#
#   DROPLET_APP_DIR=/var/www/localarts        # default if unset
#   DROPLET_SERVICE_NAME=local-music.service  # default if unset
#
# Steps, in order:
#   1. If there are uncommitted local changes, stage + commit them
#      (prompts for a commit message) and `git push`.
#   2. Over the same SSH connection: `git pull`, `pip install -r
#      requirements.txt`, `python3 sync_schema.py --apply` (adds any
#      missing columns, widens any that need it -- see sync_schema.py's
#      own docstring; never drops or narrows anything), then restarts
#      the systemd service.
#   3. Runs migrate_to_postgres.py through the same tunnel to copy your
#      local venue/artist/event data over (still asks you to type "yes"
#      before wiping the droplet's existing rows -- see
#      migrate_to_postgres.py's own docstring for why).
#
# Requires password-based SSH to work the same way it does when you SSH
# in by hand -- you'll be prompted once, right when the connection opens.
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
DROPLET_SERVICE_NAME="${DROPLET_SERVICE_NAME:-local-music.service}"

# --- Step 1: commit + push local code changes -------------------------------

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "You're on branch '$BRANCH', not 'main' -- this script assumes you deploy"
  echo "from main. Switch branches first, or Ctrl-C now if that's not what you meant."
  read -r -p "Press Enter to continue anyway, or Ctrl-C to abort..."
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes:"
  git status --short
  echo
  read -r -p "Commit message: " COMMIT_MSG
  if [ -z "$COMMIT_MSG" ]; then
    echo "No commit message entered -- aborting so nothing gets committed with a blank message."
    exit 1
  fi
  git add -A
  git commit -m "$COMMIT_MSG"
else
  echo "No uncommitted changes -- skipping commit."
fi

echo "Pushing to origin..."
git push

# --- Steps 2 + 3: one SSH connection, reused for both the remote deploy -----
# --- commands and the local->droplet data-sync tunnel -----------------------

CONTROL_SOCKET="/tmp/localarts_deploy_all_$$"

cleanup() {
  if [ -S "$CONTROL_SOCKET" ]; then
    echo "Closing SSH connection..."
    ssh -S "$CONTROL_SOCKET" -O exit "${DROPLET_SSH_USER}@${DROPLET_IP}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Opening SSH connection to ${DROPLET_IP} (you may be asked for your password)..."
if ! ssh -f -N -M -S "$CONTROL_SOCKET" \
    -L "${LOCAL_TUNNEL_PORT}:localhost:5432" \
    "${DROPLET_SSH_USER}@${DROPLET_IP}"; then
  echo "Could not open the SSH connection -- check DROPLET_IP/DROPLET_SSH_USER in $ENV_FILE."
  exit 1
fi

echo
echo "--- Step 2: pulling code + syncing schema on the droplet ---"
ssh -S "$CONTROL_SOCKET" "${DROPLET_SSH_USER}@${DROPLET_IP}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
cd "${DROPLET_APP_DIR}"
git pull
source .venv/bin/activate
pip install -q -r requirements.txt
set -a; source deploy/local-music.env; set +a
python3 sync_schema.py --apply
deactivate
systemctl restart "${DROPLET_SERVICE_NAME}"
echo "Droplet code + schema are up to date; service restarted."
REMOTE_SCRIPT

echo
echo "--- Step 3: syncing local data to the droplet ---"
echo "Waiting for the tunnel to come up..."
for _ in $(seq 1 15); do
  if nc -z localhost "$LOCAL_TUNNEL_PORT" 2>/dev/null; then
    break
  fi
  sleep 1
done
if ! nc -z localhost "$LOCAL_TUNNEL_PORT" 2>/dev/null; then
  echo "Tunnel never came up on port $LOCAL_TUNNEL_PORT -- aborting the data sync."
  echo "(Your code + schema changes above already went through fine.)"
  exit 1
fi

python3 migrate_to_postgres.py \
  "postgresql://localarts:${DROPLET_PG_PASSWORD}@localhost:${LOCAL_TUNNEL_PORT}/localarts"

echo
echo "All done: code pushed + pulled, schema synced, data synced, service restarted."
