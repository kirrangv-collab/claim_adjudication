#!/usr/bin/env bash
# Back up the ClaimLab PostgreSQL database to deploy/backups/ and delete
# backups older than KEEP_DAYS (default 14).
#
# Usage:  bash deploy/backup.sh
# Cron:   15 3 * * * bash /home/ubuntu/claim_adjudication/deploy/backup.sh >> /home/ubuntu/claimlab-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p backups
FILE="backups/claimlab-$(date -u +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U claimlab -d claimlab --clean --if-exists --no-owner | gzip > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"
find backups -name 'claimlab-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup written: deploy/$FILE ($(du -h "$FILE" | cut -f1))"
