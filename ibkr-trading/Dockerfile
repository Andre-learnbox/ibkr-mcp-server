FROM ghcr.io/hellek1/ib-mcp:latest

# The hellek1 image installs ib_mcp as a Poetry package into site-packages.
# We copy our extended server.py to a neutral path and override the
# entrypoint so Python runs our file directly inside the existing
# environment (ib_async, fastmcp and all other deps are already present).

# LABEL forces Docker to invalidate cache and rebuild this layer
LABEL version="2.0.0" description="ibkr-mcp + trading extension"

COPY server.py /app/server.py

ENTRYPOINT ["python", "/app/server.py"]
