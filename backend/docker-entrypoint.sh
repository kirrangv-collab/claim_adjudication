#!/bin/sh
# Apply database migrations before starting the API. Fails loudly (set -e)
# rather than starting a server against a schema it doesn't match.
set -e
echo "Applying database migrations..."
python -m alembic upgrade head
if [ "${CLAIMLAB_PUBLIC_DEMO:-false}" = "true" ]; then
    echo "Public demo mode: seeding demo accounts..."
    python scripts/seed_demo.py
fi
echo "Starting API server..."
exec python -m backend
