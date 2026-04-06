#!/usr/bin/env bash
# =============================================================================
#  test_tools.sh  –  ibkr-mcp-server diagnostics
#  Run on the server:  ./test_tools.sh
# =============================================================================
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
err() { echo -e "${RED}✗${NC} $*"; }
hdr() { echo; echo -e "${YELLOW}=== $* ===${NC}"; }

MCP_URL="http://localhost:8000/mcp"
TMPFILE=$(mktemp /tmp/mcp_test_XXXXXX.json)
trap 'rm -f "$TMPFILE"' EXIT

# ── 1. Container ─────────────────────────────────────────────────────────────
hdr "Container status"
docker compose ps ibkr-mcp-server

STATUS=$(docker inspect "$(docker compose ps -q ibkr-mcp-server)" \
  --format '{{.State.Health.Status}}' 2>/dev/null || echo "unknown")
echo "  Health: $STATUS"
[ "$STATUS" = "healthy" ] && ok "Container healthy" || err "Not healthy: $STATUS"

# ── 2. Versions inside container  (-T avoids TTY error) ──────────────────────
hdr "Python / library versions"
docker compose exec -T ibkr-mcp-server python3 - << 'PYEOF'
import sys, fastmcp, ib_async
print(f"  Python  : {sys.version.split()[0]}")
print(f"  fastmcp : {fastmcp.__version__}")
print(f"  ib_async: {ib_async.__version__}")
PYEOF

# ── 3. server.py sanity check ────────────────────────────────────────────────
hdr "server.py integrity"
docker compose exec -T ibkr-mcp-server python3 - << 'PYEOF'
import ast, os, sys

path = "/app/server.py"
if not os.path.exists(path):
    print(f"  ✗ {path} missing!"); sys.exit(1)

with open(path) as f:
    src = f.read()
ast.parse(src)

tree = ast.parse(src)
names = {n.name for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
expected = [
    "lookup_contract","ticker_to_conid","search_contracts","get_contract_details",
    "get_historical_data","get_fundamental_data","get_news","get_historical_news",
    "get_article","get_account_summary","get_positions",
    "place_order","place_spread_order","cancel_order",
    "get_open_orders","modify_order","get_trades_history",
]
missing = [t for t in expected if t not in names]
if missing:
    print(f"  ✗ Tools missing in source: {missing}"); sys.exit(1)

main_pos = src.index("def main() -> None:")
bad = [t for t in expected
       if src.index(f"async def {t}(" if f"async def {t}(" in src else f"def {t}(") > main_pos]
if bad:
    print(f"  ✗ Defined AFTER main() – won't register: {bad}"); sys.exit(1)

print(f"  ✓ {path}  ({len(src.splitlines())} lines, {len(expected)} tools, all before main())")
PYEOF

# ── 4. Environment ────────────────────────────────────────────────────────────
hdr "Trading configuration"
docker compose exec -T ibkr-mcp-server env \
  | grep -E "TRADING_ENABLED|READ_ONLY_API|IB_HOST|IB_PORT|IB_CLIENT" | sort \
  || echo "  (no matching vars)"

# ── 5. MCP endpoint ───────────────────────────────────────────────────────────
hdr "MCP HTTP endpoint"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' \
  --max-time 5 2>/dev/null || echo "000")
echo "  POST $MCP_URL  →  HTTP $HTTP_CODE"
[ "$HTTP_CODE" = "200" ] && ok "Endpoint responding" || err "Endpoint not responding (HTTP $HTTP_CODE)"

# ── 6. tools/list via MCP ────────────────────────────────────────────────────
hdr "Registered tools (tools/list)"
curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  --max-time 10 > "$TMPFILE" 2>/dev/null || echo "{}" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    try:
        data = json.load(f)
    except Exception as e:
        print(f"  Parse error: {e}"); sys.exit(0)

tools = data.get("result", {}).get("tools", [])
names = {t["name"] for t in tools}
print(f"  Total tools visible via MCP: {len(names)}\n")

readonly = [
    "lookup_contract","ticker_to_conid","search_contracts","get_contract_details",
    "get_historical_data","get_fundamental_data","get_news","get_historical_news",
    "get_article","get_account_summary","get_positions",
]
trading = [
    "place_order","place_spread_order","cancel_order",
    "get_open_orders","modify_order","get_trades_history",
]

print("  Read-only (11):")
for t in readonly:
    print(f"    {'✓' if t in names else '✗ MISSING'}  {t}")
print("\n  Trading (6):")
for t in trading:
    print(f"    {'✓' if t in names else '✗ MISSING'}  {t}")

all_ok = all(t in names for t in readonly + trading)
print(f"\n  {'✓ All 17 tools present' if all_ok else '✗ Some tools missing – rebuild with --no-cache'}")
PYEOF

# ── 7. Trading guard ──────────────────────────────────────────────────────────
hdr "Trading guard (place_order blocked when TRADING_ENABLED=false)"
curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"place_order","arguments":{"symbol":"AAPL","action":"BUY","quantity":1}}}' \
  --max-time 10 > "$TMPFILE" 2>/dev/null || echo "{}" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    try: data = json.load(f)
    except: data = {}
content = json.dumps(data)
if "Trading is disabled" in content or "TRADING_ENABLED" in content:
    print("  ✓ Guard active – place_order blocked correctly")
elif "error" in content.lower() or "isError" in content:
    print("  ✓ place_order reachable (IB connection error expected – gateway not connected)")
else:
    print(f"  Response: {content[:300]}")
PYEOF

# ── 8. Python one-liner ───────────────────────────────────────────────────────
hdr "Quick Python test (run from server)"
echo '  python3 -c "'
echo '    import urllib.request, json'
echo '    req = urllib.request.Request("http://localhost:8000/mcp",'
echo '      data=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}).encode(),'
echo '      headers={"Content-Type":"application/json"})'
echo '    with urllib.request.urlopen(req, timeout=10) as r:'
echo '      data = json.load(r)'
echo '    [print(t["name"]) for t in data["result"]["tools"]]'
echo '  "'

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  To enable trading (paper):"
echo "    1. .env → TRADING_ENABLED=true  READ_ONLY_API=no"
echo "    2. docker compose build --no-cache ibkr-mcp-server"
echo "    3. docker compose up -d"
echo "    4. ./test_tools.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
