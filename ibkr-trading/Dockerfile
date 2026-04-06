# Standalone image – full control over all dependencies.
# No longer extends hellek1/ib-mcp (black box with unknown FastMCP version).
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Pinned versions – validated locally before build
RUN pip install --no-cache-dir \
    "ib_async==2.1.0" \
    "fastmcp==2.14.6"

COPY server.py /app/server.py

HEALTHCHECK --interval=20s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -sf http://localhost:8000/mcp || exit 1

ENTRYPOINT ["python", "/app/server.py"]
