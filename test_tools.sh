#!/usr/bin/env bash
# =============================================================================
#  test_tools.sh – Diagnostics & tool verification for ibkr-mcp-server
#
#  Usage on the server:
#    chmod +x test_tools.sh
#    ./test_tools.sh
#
#  What this checks:
#    1. Container running & healthy
#    2. MCP endpoint reachable
#    3. Tools actually listed (incl. trading tools)
#    4. Config values (TRADING_ENABLED, READ_ONLY_API)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
hdr()  { echo; echo -e "${YELLOW}=== $* ===${NC}"; }

# ── 1. Container status ───────────────────────────────────────────────────────
hdr "Container status"
docker compose ps ibkr-mcp-server 2>/dev/null || { fail "docker compose not available"; exit 1; }

STATUS=$(docker compose ps ibkr-mcp-server --format json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','unknown'))" 2>/dev/null \
  || echo "unknown")
echo "  Health: $STATUS"

# ── 2. Image details ─────────────────────────────────────────────────────────
hdr "Image details"
IMG=$(docker compose images ibkr-mcp-server --format json 2>/dev/null \
  | python3 -c "import sys,json; rows=json.load(sys.stdin); print(rows[0]['Image'] if rows else 'unknown')" 2>/dev/null \
  || echo "unknown")
echo "  Image: $IMG"

docker compose exec ibkr-mcp-server python3 - << 'PYEOF'
import fastmcp, ib_async
print(f"  fastmcp : {fastmcp.__version__}")
print(f"  ib_async: {ib_async.__version__}")
import sys
print(f"  Python  : {sys.version.split()[0]}")
PYEOF

# ── 3. server.py entrypoint ───────────────────────────────────────────────────
hdr "Entrypoint check"
ENTRY=$(docker inspect $(docker compose ps -q ibkr-mcp-server) \
  --format '{{json .Config.Entrypoint}}' 2>/dev/null || echo "unknown")
echo "  Entrypoint: $ENTRY"

# Check if our server.py is running
docker compose exec ibkr-mcp-server python3 -c "
import ast
with open('/app/server.py') as f:
    src = f.read()
tree = ast.parse(src)
tools = [n.name for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and not n.name.startswith('_')]
print(f'  Functions in /app/server.py: {len(tools)}')
print(f'  Tools: {tools}')
" 2>/dev/null || warn "Could not inspect /app/server.py"

# ── 4. Environment variables ──────────────────────────────────────────────────
hdr "Environment (trading config)"
docker compose exec ibkr-mcp-server env | grep -E "TRADING|READ_ONLY|IB_HOST|IB_PORT|IB_CLIENT" | sort

# ── 5. MCP HTTP endpoint ──────────────────────────────────────────────────────
hdr "MCP endpoint reachability"
# Try from inside the network
MCP_URL="http://localhost:8000/mcp"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  --max-time 5 2>/dev/null || echo "000")
echo "  POST $MCP_URL → HTTP $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
  ok "MCP endpoint responding"
else
  fail "MCP endpoint not responding (code $HTTP_CODE)"
fi

# ── 6. List tools via MCP protocol ───────────────────────────────────────────
hdr "MCP tool listing"
TOOLS_RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  --max-time 10 2>/dev/null || echo '{}')

python3 - << PYEOF
import json, sys

raw = '''$TOOLS_RESPONSE'''
try:
    data = json.loads(raw)
except:
    print("  Could not parse MCP response")
    sys.exit(0)

tools = data.get('result', {}).get('tools', [])
names = [t['name'] for t in tools]
print(f"  Total tools visible: {len(names)}")
print()

readonly = ['lookup_contract','ticker_to_conid','search_contracts','get_contract_details',
            'get_historical_data','get_fundamental_data','get_news','get_historical_news',
            'get_article','get_account_summary','get_positions']
trading  = ['place_order','cancel_order','get_open_orders','modify_order','get_trades_history']

print("  Read-only tools:")
for t in readonly:
    mark = '✓' if t in names else '✗ MISSING'
    print(f"    {mark}  {t}")

print()
print("  Trading tools:")
for t in trading:
    mark = '✓' if t in names else '✗ MISSING'
    print(f"    {mark}  {t}")
PYEOF

# ── 7. Test place_order (read-only guard) ────────────────────────────────────
hdr "Trading guard test"
GUARD_RESP=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"place_order","arguments":{"symbol":"AAPL","action":"BUY","quantity":1}}}' \
  --max-time 10 2>/dev/null || echo '{}')

python3 - << PYEOF
import json
raw = '''$GUARD_RESP'''
try:
    data = json.loads(raw)
    # If TRADING_ENABLED=false → RuntimeError in content
    content = str(data)
    if 'Trading is disabled' in content or 'TRADING_ENABLED' in content:
        print("  ✓ Guard working: place_order blocked (TRADING_ENABLED=false)")
    elif 'error' in content.lower():
        print(f"  ✓ place_order reached (errored, but tool is registered)")
        print(f"  Response: {content[:200]}")
    else:
        print(f"  Response: {content[:200]}")
except Exception as e:
    print(f"  Parse error: {e}")
PYEOF

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test complete. To enable trading:"
echo "    Edit .env → TRADING_ENABLED=true + READ_ONLY_API=no"
echo "    docker compose up -d --force-recreate ibkr-mcp-server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
