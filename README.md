# IBKR MCP Stack – Setup & Konfiguration

## Architektur

```
Internet (HTTPS)
       │
       ▼
   Caddy (TLS + Auth)
   ├── /mcp*   → Anthropic-IPs (Claude.ai) ODER Bearer-Token
   │              → ibkr-mcp-server:8000
   └── /mcpo*  → Bearer-Token → mcpo:8001 → ibkr-mcp-server:8000
                                  ↑
                            (OpenAPI für Open-WebUI)
```

## Schnellstart

```bash
# 1. Repository klonen / Dateien kopieren
cp .env.example .env

# 2. Token generieren
openssl rand -hex 32
# → Ausgabe in .env bei MCP_SECRET_TOKEN eintragen

# 3. Domain eintragen
# → DOMAIN=mcp.deine-domain.de in .env

# 4. IBKR-Zugangsdaten eintragen
# → TWS_USERID und TWS_PASSWORD in .env

# 5. Starten
docker compose up -d

# 6. Logs prüfen
docker compose logs -f
```

---

## Client-Konfiguration

### Claude.ai

Claude.ai verbindet sich automatisch über Anthropic-IPs – **kein Token nötig**.

**Einrichtung:**
1. claude.ai → Einstellungen → Connectors → + Custom Connector
2. URL: `https://mcp.deine-domain.de/mcp`
3. Transport: Streamable HTTP
4. Auth: **None** (Anthropic-IPs sind whitelisted)

---

### Perplexity (Pro/Max/Enterprise)

**Einrichtung:**
1. perplexity.ai → Settings → Connectors → + Custom Connector → Remote
2. URL: `https://mcp.deine-domain.de/mcp`
3. Transport: Streamable HTTP
4. Auth: **API Key** → deinen `MCP_SECRET_TOKEN` eintragen

---

### Open-WebUI

Open-WebUI nutzt den MCPO-Endpunkt (OpenAPI-Format).

**Einrichtung:**
1. Admin Settings → External Tools → + Add Server
2. Type: **OpenAPI** (nicht MCP!)
3. URL: `https://mcp.deine-domain.de/mcpo/ibkr`
4. Auth: Bearer → deinen `MCP_SECRET_TOKEN` eintragen

---

### OpenAI Responses API

Token wird pro API-Request als Header übergeben.

```python
from openai import OpenAI

client = OpenAI()
resp = client.responses.create(
    model="gpt-4.1",
    tools=[{
        "type": "mcp",
        "server_label": "ibkr",
        "server_url": "https://mcp.deine-domain.de/mcp",
        "require_approval": "never",
        "headers": {
            "Authorization": "Bearer DEIN_MCP_SECRET_TOKEN"
        }
    }],
    input="Zeige mein Portfolio"
)
```

---

## Sicherheitshinweise

- `READ_ONLY_API=yes` **niemals** deaktivieren solange MCP aktiv!
- `MCP_SECRET_TOKEN` niemals in Git committen
- VNC (`VNC_SERVER_PASSWORD`) nur für Debugging aktivieren
- Anthropic-IP-Whitelist bei Änderungen prüfen: https://docs.claude.com/en/api/ip-addresses

## Diagnose

```bash
# Alle Container-Status
docker compose ps

# Live-Logs
docker compose logs -f

# Einzelne Services
docker compose logs ib-gateway --tail=50
docker compose logs ibkr-mcp-server --tail=20
docker compose logs mcpo --tail=20
docker compose logs caddy --tail=20

# MCP-Verbindung testen (simuliert Open-WebUI/Claude.ai)
curl -v -X POST https://mcp.deine-domain.de/mcp \
  -H "Authorization: Bearer DEIN_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# MCPO OpenAPI-Schema prüfen
curl -H "Authorization: Bearer DEIN_TOKEN" \
  https://mcp.deine-domain.de/mcpo/ibkr/openapi.json

# MCPO neu starten (bei Session-Problemen)
docker compose restart mcpo
```

## Dateistruktur

```
ibkr-mcp-stack/
├── docker-compose.yml   # Alle Services
├── Caddyfile            # TLS + Routing + Auth
├── .env                 # Zugangsdaten (nicht committen!)
├── .env.example         # Template
└── mcpo/
    └── config.json      # MCPO Server-Konfiguration
```
