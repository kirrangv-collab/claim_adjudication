#!/bin/sh
# Apply database migrations before starting the API. Fails loudly (set -e)
# rather than starting a server against a schema it doesn't match.
set -e
echo "Applying database migrations..."
python -m alembic upgrade head
echo "Starting API server..."
exec python -m backend
