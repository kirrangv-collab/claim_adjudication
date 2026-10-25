#!/usr/bin/env bash
# Restore a backup made by deploy/backup.sh into the running database.
# This REPLACES all current data.
#
# Usage:  bash deploy/restore.sh deploy/backups/claimlab-YYYYMMDD-HHMMSS.sql.gz [--yes]
set -euo pipefail

if [ ! -f "${1:-}" ]; then
  echo "Usage: bash deploy/restore.sh <backup file .sql.gz> [--yes]" >&2
  exit 1
fi
BACKUP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
cd "$(dirname "$0")"

if [ "${2:-}" != "--yes" ]; then
  read -r -p "This replaces ALL data in the running database with $BACKUP. Type 'restore' to continue: " ANSWER
  ANSWER="${ANSWER%$'\r'}"
  [ "$ANSWER" = "restore" ] || { echo "Aborted."; exit 1; }
fi
gunzip -c "$BACKUP" | docker compose exec -T db psql -q -U claimlab -d claimlab -v ON_ERROR_STOP=1 >/dev/null
echo "Restored $BACKUP"
