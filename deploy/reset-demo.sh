#!/usr/bin/env bash
# Nightly reset for public demos: deletes every case review and evaluation run
# and re-seeds the demo-maker / demo-checker accounts. Admin accounts are kept.
#
# Usage:  bash deploy/reset-demo.sh
# Cron:   30 3 * * * bash /home/ubuntu/claim_adjudication/deploy/reset-demo.sh >> /home/ubuntu/claimlab-reset.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) resetting public demo data"
docker compose exec -T api python scripts/seed_demo.py --reset
