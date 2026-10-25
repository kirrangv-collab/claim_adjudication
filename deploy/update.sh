#!/usr/bin/env bash
# Update a running deployment to the latest code: back up, pull, rebuild (or
# pull published images if API_IMAGE/WEB_IMAGE are set), restart, clean up.
#
# Usage:  bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")"

bash backup.sh || echo "Warning: backup failed or the stack is not running; continuing." >&2
git -C .. pull --ff-only
if grep -qE '^(API_IMAGE|WEB_IMAGE)=.+' .env 2>/dev/null; then
  docker compose pull
  docker compose up -d
else
  docker compose up -d --build
fi
docker image prune -f >/dev/null
docker compose ps
