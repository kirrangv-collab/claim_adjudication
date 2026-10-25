FROM python:3.12-slim

# Set INSTALL_SBIOBERT=true to bake SBioBERT (sentence-transformers + PyTorch)
# into the image for CLAIMLAB_EMBEDDER=sbiobert. Off by default: large image.
ARG INSTALL_SBIOBERT=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    CLAIMLAB_RELOAD=false \
    HF_HOME=/tmp/hf

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --no-compile ".[knowledge]" \
    && if [ "$INSTALL_SBIOBERT" = "true" ]; then pip install --no-cache-dir --no-compile ".[sbiobert]"; fi
COPY backend ./backend
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY data/processed ./data/processed
COPY data/ontology ./data/ontology
COPY data/calibration ./data/calibration
RUN chmod +x ./backend/docker-entrypoint.sh
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"
ENTRYPOINT ["./backend/docker-entrypoint.sh"]
