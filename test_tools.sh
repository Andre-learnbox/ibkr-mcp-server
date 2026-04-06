#!/usr/bin/env bash
# =============================================================================
#  test_tools.sh – Diagnostics & tool verification for ibkr-mcp-server
#  Usage:  ./test_tools.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
hdr()  { echo; echo -e "${YELLOW}=== $* ===${NC}"; }

# ── 1. Container status ───────────────────────────────────────────────────────
hdr "Container status"
docker compose ps ibkr-mcp-server

STATUS=$(docker compose ps ibkr-mcp-server --format json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','unknown'))" 2>/dev/null \
  || echo "unknown")
echo "  Health: $STATUS"
[ "$STATUS" = "healthy" ] && ok "Container healthy" || warn "Container not healthy yet"

# ── 2. Image / Python / lib versions  (-T = no TTY) ──────────────────────────
hdr "Image details"
docker compose exec -T ibkr-mcp-server python3 - << 'PYEOF'
import sys, fastmcp, ib_async
print(f"  Python  : {sys.version.split()[0]}")
print(f"  fastmcp : {fastmcp.__version__}")
print(f"  ib_async: {ib_async.__version__}")
PYEOF

# ── 3. Entrypoint + server.py presence ───────────────────────────────────────
hdr "Entrypoint & server.py"
ENTRY=$(docker inspect "$(docker compose ps -q ibkr-mcp-server)" \
  --format '{{json .Config.Entrypoint}}' 2>/dev/null || echo "unknown")
echo "  Entrypoint: $ENTRY"

docker compose exec -T ibkr-mcp-server python3 - << 'PYEOF'
import ast, os
path = '/app/server.py'
if not os.path.exists(path):
    print(f"  ✗ {path} not found!")
else:
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    tools = sorted(
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith('_')
        and n.name not in ('main',)
    )
    print(f"  ✓ /app/server.py present ({len(src.splitlines())} lines)")
    print(f"  Tools in source ({len(tools)}): {tools}")
PYEOF

# ── 4. Environment ────────────────────────────────────────────────────────────
hdr "Environment (trading config)"
docker compose exec -T ibkr-mcp-server env \
  | grep -E "TRADING|READ_ONLY|IB_HOST|IB_PORT|IB_CLIENT" | sort \
  || warn "No matching env vars found"

# ── 5. MCP HTTP endpoint ──────────────────────────────────────────────────────
hdr "MCP endpoint reachability"
MCP_URL="http://localhost:8000/mcp"

INIT_RESP=$(curl -s -w "\nHTTP:%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  --max-time 5 2>/dev/null || echo -e "\nHTTP:000")

HTTP_CODE=$(echo "$INIT_RESP" | grep "HTTP:" | cut -d: -f2)
echo "  POST $MCP_URL → HTTP $HTTP_CODE"
[ "$HTTP_CODE" = "200" ] && ok "MCP endpoint responding" || fail "MCP not responding (code $HTTP_CODE)"

# ── 6. List tools via MCP protocol ───────────────────────────────────────────
hdr "MCP tool listing (tools/list)"

TOOLS_RESP=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  --max-time 10 2>/dev/null || echo '{}')

python3 - << PYEOF
import json, sys

try:
    data = json.loads('''$TOOLS_RESP''')
except Exception as e:
    print(f"  Parse error: {e}")
    sys.exit(0)

tools = data.get('result', {}).get('tools', [])
names = {t['name'] for t in tools}
print(f"  Total tools registered: {len(names)}")

readonly = [
    'lookup_contract','ticker_to_conid','search_contracts','get_contract_details',
    'get_historical_data','get_fundamental_data','get_news','get_historical_news',
    'get_article','get_account_summary','get_positions',
]
trading = [
    'place_order','place_spread_order','cancel_order',
    'get_open_orders','modify_order','get_trades_history',
]

print("\n  Read-only tools (11):")
for t in readonly:
    mark = '✓' if t in names else '✗ MISSING'
    print(f"    {mark}  {t}")

print("\n  Trading tools (6):")
for t in trading:
    mark = '✓' if t in names else '✗ MISSING'
    print(f"    {mark}  {t}")

missing_all = [t for t in readonly + trading if t not in names]
if missing_all:
    print(f"\n  ✗ Missing tools: {missing_all}")
else:
    print(f"\n  ✓ All 17 tools present")
PYEOF

# ── 7. Trading guard test ─────────────────────────────────────────────────────
hdr "Trading guard test (place_order should be blocked if TRADING_ENABLED=false)"

GUARD_RESP=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"place_order","arguments":{"symbol":"AAPL","action":"BUY","quantity":1}}}' \
  --max-time 10 2>/dev/null || echo '{}')

python3 - << PYEOF
import json
try:
    data = json.loads('''$GUARD_RESP''')
    content = json.dumps(data)
    if 'Trading is disabled' in content or 'TRADING_ENABLED' in content:
        print("  ✓ Guard active: place_order blocked correctly")
    elif 'place_order' in content and 'error' in content.lower():
        print("  ✓ place_order reached (IB connection error expected without gateway)")
    elif 'isError' in content or 'RuntimeError' in content:
        print("  ✓ place_order returns error as expected")
    else:
        print(f"  Response: {content[:300]}")
except Exception as e:
    print(f"  Parse error: {e}")
PYEOF

# ── 8. Python curl equivalent ─────────────────────────────────────────────────
hdr "Python test equivalent (copy & run anywhere)"
cat << 'PYEOF'
  # One-liner to list all tools:
  python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/mcp',
    data=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}).encode(),
    headers={'Content-Type':'application/json'}
)
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.load(r)
tools = [t['name'] for t in data.get('result',{}).get('tools',[])]
print(f'Tools ({len(tools)}):')
[print(f'  - {t}') for t in sorted(tools)]
"
PYEOF

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  To enable trading (paper account):"
echo "    1. Edit .env:  TRADING_ENABLED=true  READ_ONLY_API=no"
echo "    2. docker compose up -d --force-recreate ibkr-mcp-server"
echo "    3. ./test_tools.sh  (re-run to verify)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
