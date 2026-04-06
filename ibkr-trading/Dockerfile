FROM ghcr.io/hellek1/ib-mcp:latest

# The hellek1 image installs ib_mcp as a Poetry package into site-packages.
# Overwriting the source path has no effect on the installed package.
# Instead we copy our extended server.py to a known path and override
# the entrypoint so Python runs our file directly inside the existing
# environment (ib_async, fastmcp, and all other deps are already present).
COPY server.py /app/server.py

ENTRYPOINT ["python", "/app/server.py"]
