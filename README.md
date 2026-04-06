# IBKR MCP Stack

Selbst-gehosteter MCP-Server für Interactive Brokers – absicherbar für Claude.ai, Perplexity, Open-WebUI und die OpenAI API.

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
# 1. .env anlegen
cp .env.example .env

# 2. Token generieren und in .env eintragen
openssl rand -hex 32

# 3. Alle Pflichtfelder in .env ausfüllen (Domain, IBKR-Zugangsdaten)

# 4. Starten
docker compose up -d

# 5. Logs prüfen
docker compose logs -f caddy
```

## Paper ↔ Live umschalten

Nur diese zwei Zeilen in `.env` ändern, dann neu starten:

```env
# Paper-Konto
TRADING_MODE=paper
IB_GATEWAY_PORT=4004

# Live-Konto
TRADING_MODE=live
IB_GATEWAY_PORT=4003
```

```bash
docker compose up -d --force-recreate ib-gateway ibkr-mcp-server
```

## Client-Konfiguration

### Claude.ai
Verbindet sich über Anthropic-IPs – kein Token nötig.
- URL: `https://deine-domain.de/mcp`
- Transport: Streamable HTTP
- Auth: **None**

### Perplexity (Pro/Max/Enterprise)
- Settings → Connectors → + Custom Connector → Remote
- URL: `https://deine-domain.de/mcp`
- Transport: Streamable HTTP
- Auth: **API Key** → `MCP_SECRET_TOKEN` eintragen

### Open-WebUI
- Admin Settings → External Tools → + Add Server
- Type: **OpenAPI** (nicht MCP!)
- URL: `https://deine-domain.de/mcpo/ibkr`
- Auth: **Bearer** → `MCP_SECRET_TOKEN` eintragen

### OpenAI Responses API
```python
from openai import OpenAI
client = OpenAI()
resp = client.responses.create(
    model="gpt-4.1",
    tools=[{
        "type": "mcp",
        "server_label": "ibkr",
        "server_url": "https://deine-domain.de/mcp",
        "require_approval": "never",
        "headers": {"Authorization": "Bearer DEIN_MCP_SECRET_TOKEN"}
    }],
    input="Zeige mein Portfolio"
)
```

## Sicherheitshinweise

- `READ_ONLY_API=yes` **niemals** deaktivieren solange MCP aktiv ist!
- `MCP_SECRET_TOKEN` niemals in Git committen (`.gitignore` schützt `.env`)
- Anthropic-IP-Whitelist bei Änderungen prüfen: https://docs.claude.com/en/api/ip-addresses

## Diagnose

```bash
# Status aller Container
docker compose ps

# Caddy Logs (TLS-Fehler, Auth-Fehler)
docker compose logs caddy --tail=50

# MCP-Server Logs
docker compose logs ibkr-mcp-server --tail=20

# MCPO Logs
docker compose logs mcpo --tail=20

# Verbindungstest (simuliert Perplexity/OpenAI)
curl -v -X POST https://deine-domain.de/mcp \
  -H "Authorization: Bearer DEIN_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# MCPO OpenAPI-Schema prüfen
curl -H "Authorization: Bearer DEIN_TOKEN" \
  https://deine-domain.de/mcpo/ibkr/openapi.json

# MCPO neu starten (bei Session-Problemen)
docker compose restart mcpo
```

## Dateistruktur

```
ibkr-mcp-server/
├── docker-compose.yml   # Alle 4 Services
├── Caddyfile            # TLS + Auth Template (__DOMAIN__ Platzhalter)
├── .env.example         # Vorlage (cp zu .env, niemals committen!)
├── .gitignore
├── README.md
└── mcpo/
    └── config.json      # MCPO → ibkr-mcp-server (intern)
```
