"""
IB MCP Server – Extended Edition
Based on ghcr.io/hellek1/ib-mcp (read-only tools) + Trading Extensions.

Safety model:
  READ_ONLY_API=yes    → all write tools blocked at Gateway / Caddy layer
  TRADING_ENABLED=false → all write tools raise an error in server.py
  Both flags are independent; set both for maximum protection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import ib_async as ib
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
IB_HOST      = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT      = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))

_TRADING_ENABLED_RAW = os.getenv("TRADING_ENABLED", "false").strip().lower()
TRADING_ENABLED = _TRADING_ENABLED_RAW in ("true", "yes", "1")

# ---------------------------------------------------------------------------
# IB connection helper (async context manager)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _ib_connect() -> AsyncGenerator[ib.IB, None]:
    """Async context manager: connect, yield, disconnect."""
    client = ib.IB()
    await client.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    try:
        yield client
    finally:
        client.disconnect()

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="ib-mcp",
    description=(
        "MCP server exposing Interactive Brokers data via ib_async. "
        "Trading tools are active when TRADING_ENABLED=true."
    ),
)

# ===========================================================================
# READ-ONLY TOOLS
# ===========================================================================

@mcp.tool()
async def lookup_contract(
    symbol: str,
    exchange: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Look up contract details by ticker symbol and optional exchange/currency."""
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, exchange or "SMART", currency or "USD")
        details = await ibc.reqContractDetailsAsync(contract)
        if not details:
            return {"error": f"No contract found for {symbol}"}
        d = details[0]
        return {
            "conid":       d.contract.conId,
            "symbol":      d.contract.symbol,
            "secType":     d.contract.secType,
            "exchange":    d.contract.exchange,
            "currency":    d.contract.currency,
            "longName":    d.longName,
            "industry":    d.industry,
            "category":    d.category,
            "subcategory": d.subcategory,
        }


@mcp.tool()
async def ticker_to_conid(symbol: str, exchange: str = "", currency: str = "") -> dict[str, Any]:
    """Convert ticker symbol to IBKR contract ID (conid)."""
    result = await lookup_contract(symbol, exchange, currency)
    if "error" in result:
        return result
    return {"conid": result["conid"], "symbol": result["symbol"]}


@mcp.tool()
async def search_contracts(query: str, sec_type: str = "STK") -> list[dict[str, Any]]:
    """Search for contracts by partial symbol or company name."""
    async with _ib_connect() as ibc:
        results = await ibc.reqMatchingSymbolsAsync(query)
        return [
            {
                "conid":    r.contract.conId,
                "symbol":   r.contract.symbol,
                "secType":  r.contract.secType,
                "exchange": r.contract.exchange,
                "currency": r.contract.currency,
            }
            for r in results
            if not sec_type or r.contract.secType == sec_type
        ]


@mcp.tool()
async def get_contract_details(conid: int) -> dict[str, Any]:
    """Get detailed contract information including dividends and corporate actions."""
    async with _ib_connect() as ibc:
        contract = ib.Contract(conId=conid)
        await ibc.qualifyContractsAsync(contract)
        details = await ibc.reqContractDetailsAsync(contract)
        if not details:
            return {"error": f"No details for conid={conid}"}
        d = details[0]
        return {
            "conid":          d.contract.conId,
            "symbol":         d.contract.symbol,
            "secType":        d.contract.secType,
            "exchange":       d.contract.exchange,
            "currency":       d.contract.currency,
            "longName":       d.longName,
            "industry":       d.industry,
            "category":       d.category,
            "subcategory":    d.subcategory,
            "minTick":        d.minTick,
            "priceMagnifier": d.priceMagnifier,
            "underConId":     d.underConId,
        }


@mcp.tool()
async def get_historical_data(
    symbol: str,
    duration: str = "1 M",
    bar_size: str = "1 day",
    data_type: str = "TRADES",
    exchange: str = "",
    currency: str = "",
) -> list[dict[str, Any]]:
    """Retrieve historical market data with configurable duration, bar size, and data type."""
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, exchange or "SMART", currency or "USD")
        await ibc.qualifyContractsAsync(contract)
        bars = await ibc.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=data_type,
            useRTH=True,
            formatDate=1,
        )
        return [
            {"date": b.date, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ]


@mcp.tool()
async def get_fundamental_data(symbol: str, report_type: str = "ReportsFinSummary") -> dict[str, Any]:
    """Retrieve fundamental data for a contract.

    report_type options: ReportsFinSummary, ReportsOwnership,
                         ReportSnapshot, ReportsFinStatements, RESC, CalendarReport
    """
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, "SMART", "USD")
        await ibc.qualifyContractsAsync(contract)
        data = await ibc.reqFundamentalDataAsync(contract, report_type)
        return {"symbol": symbol, "report_type": report_type, "data": data}


@mcp.tool()
async def get_news(symbol: str, max_articles: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent news headlines for a contract."""
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, "SMART", "USD")
        await ibc.qualifyContractsAsync(contract)
        providers = await ibc.reqNewsProvidersAsync()
        provider_codes = "+".join(p.code for p in providers) if providers else "BRFG+DJNL"
        news = await ibc.reqHistoricalNewsAsync(
            contract.conId, provider_codes,
            startDateTime="", endDateTime="",
            totalResults=max_articles,
        )
        return [
            {"time": n.time, "providerCode": n.providerCode,
             "articleId": n.articleId, "headline": n.headline}
            for n in news
        ]


@mcp.tool()
async def get_historical_news(
    symbol: str,
    start_date: str = "",
    end_date: str = "",
    max_articles: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve historical news articles within a date range."""
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, "SMART", "USD")
        await ibc.qualifyContractsAsync(contract)
        providers = await ibc.reqNewsProvidersAsync()
        provider_codes = "+".join(p.code for p in providers) if providers else "BRFG+DJNL"
        news = await ibc.reqHistoricalNewsAsync(
            contract.conId, provider_codes,
            startDateTime=start_date,
            endDateTime=end_date,
            totalResults=max_articles,
        )
        return [
            {"time": n.time, "providerCode": n.providerCode,
             "articleId": n.articleId, "headline": n.headline}
            for n in news
        ]


@mcp.tool()
async def get_article(provider_code: str, article_id: str) -> dict[str, Any]:
    """Retrieve the full text of a news article by provider code and article ID."""
    async with _ib_connect() as ibc:
        article = await ibc.reqNewsArticleAsync(provider_code, article_id)
        return {"articleType": article.articleType, "articleText": article.articleText}


@mcp.tool()
async def get_account_summary() -> dict[str, Any]:
    """Retrieve account summary: balances, equity, margin, cash."""
    async with _ib_connect() as ibc:
        summary = await ibc.accountSummaryAsync()
        return {item.tag: {"value": item.value, "currency": item.currency} for item in summary}


@mcp.tool()
async def get_positions() -> list[dict[str, Any]]:
    """Retrieve all current portfolio positions."""
    async with _ib_connect() as ibc:
        await ibc.reqPositionsAsync()
        return [
            {
                "account":  p.account,
                "symbol":   p.contract.symbol,
                "secType":  p.contract.secType,
                "exchange": p.contract.exchange,
                "currency": p.contract.currency,
                "conid":    p.contract.conId,
                "position": p.position,
                "avgCost":  p.avgCost,
            }
            for p in ibc.positions()
        ]


# ===========================================================================
# TRADING TOOLS  (gated by TRADING_ENABLED)
# ===========================================================================

def _require_trading() -> None:
    if not TRADING_ENABLED:
        raise RuntimeError(
            "Trading is disabled. Set TRADING_ENABLED=true in your environment "
            "to enable order placement. Also ensure READ_ONLY_API=no."
        )


@mcp.tool()
async def place_order(
    symbol: str,
    action: str,
    quantity: float,
    order_type: str = "MKT",
    limit_price: float | None = None,
    stop_price: float | None = None,
    exchange: str = "SMART",
    currency: str = "USD",
    tif: str = "DAY",
    account: str = "",
) -> dict[str, Any]:
    """Place a stock order.

    Args:
        symbol:      Ticker symbol (e.g. AAPL)
        action:      BUY or SELL
        quantity:    Number of shares
        order_type:  MKT | LMT | STP | STP LMT  (default: MKT)
        limit_price: Required for LMT and STP LMT orders
        stop_price:  Required for STP and STP LMT orders
        exchange:    Exchange (default: SMART)
        currency:    Currency (default: USD)
        tif:         Time-in-force: DAY | GTC | IOC  (default: DAY)
        account:     Account ID (optional, uses default if empty)
    """
    _require_trading()

    action     = action.upper()
    order_type = order_type.upper()

    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got: {action!r}")
    if order_type in ("LMT", "STP LMT") and limit_price is None:
        raise ValueError(f"limit_price is required for {order_type} orders")
    if order_type in ("STP", "STP LMT") and stop_price is None:
        raise ValueError(f"stop_price is required for {order_type} orders")

    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, exchange, currency)
        await ibc.qualifyContractsAsync(contract)

        order = ib.Order(
            action=action,
            totalQuantity=quantity,
            orderType=order_type,
            tif=tif,
        )
        if limit_price is not None:
            order.lmtPrice = limit_price
        if stop_price is not None:
            order.auxPrice = stop_price
        if account:
            order.account = account

        trade = ibc.placeOrder(contract, order)
        await asyncio.sleep(1)

        return {
            "orderId":    trade.order.orderId,
            "status":     trade.orderStatus.status,
            "symbol":     symbol,
            "action":     action,
            "quantity":   quantity,
            "orderType":  order_type,
            "limitPrice": limit_price,
            "stopPrice":  stop_price,
        }


@mcp.tool()
async def cancel_order(order_id: int) -> dict[str, Any]:
    """Cancel an open order by its IBKR order ID."""
    _require_trading()

    async with _ib_connect() as ibc:
        open_trades = ibc.openTrades()
        target = next((t for t in open_trades if t.order.orderId == order_id), None)
        if target is None:
            return {"error": f"No open order found with orderId={order_id}"}

        ibc.cancelOrder(target.order)
        await asyncio.sleep(1)

        still_open = any(t.order.orderId == order_id for t in ibc.openTrades())
        return {"orderId": order_id, "status": "PendingCancel" if still_open else "Cancelled"}


@mcp.tool()
async def get_open_orders() -> list[dict[str, Any]]:
    """Retrieve all currently open orders."""
    _require_trading()

    async with _ib_connect() as ibc:
        await ibc.reqOpenOrdersAsync()
        return [
            {
                "orderId":   t.order.orderId,
                "symbol":    t.contract.symbol,
                "secType":   t.contract.secType,
                "action":    t.order.action,
                "quantity":  t.order.totalQuantity,
                "orderType": t.order.orderType,
                "limitPrice": getattr(t.order, "lmtPrice", None),
                "stopPrice":  getattr(t.order, "auxPrice", None),
                "tif":       t.order.tif,
                "status":    t.orderStatus.status,
                "filled":    t.orderStatus.filled,
                "remaining": t.orderStatus.remaining,
            }
            for t in ibc.openTrades()
        ]


@mcp.tool()
async def modify_order(
    order_id: int,
    quantity: float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    tif: str | None = None,
) -> dict[str, Any]:
    """Modify quantity, price or time-in-force of an existing open order."""
    _require_trading()

    async with _ib_connect() as ibc:
        open_trades = ibc.openTrades()
        target = next((t for t in open_trades if t.order.orderId == order_id), None)
        if target is None:
            return {"error": f"No open order found with orderId={order_id}"}

        order = target.order
        if quantity    is not None: order.totalQuantity = quantity
        if limit_price is not None: order.lmtPrice      = limit_price
        if stop_price  is not None: order.auxPrice       = stop_price
        if tif         is not None: order.tif            = tif

        trade = ibc.placeOrder(target.contract, order)
        await asyncio.sleep(1)

        return {
            "orderId":    trade.order.orderId,
            "status":     trade.orderStatus.status,
            "quantity":   trade.order.totalQuantity,
            "limitPrice": getattr(trade.order, "lmtPrice", None),
            "stopPrice":  getattr(trade.order, "auxPrice", None),
            "tif":        trade.order.tif,
        }


@mcp.tool()
async def get_trades_history() -> list[dict[str, Any]]:
    """Retrieve executed fills from the current session."""
    _require_trading()

    async with _ib_connect() as ibc:
        return [
            {
                "execId":     f.execution.execId,
                "time":       str(f.execution.time),
                "symbol":     f.contract.symbol,
                "action":     f.execution.side,
                "quantity":   f.execution.shares,
                "price":      f.execution.price,
                "commission": f.commissionReport.commission if f.commissionReport else None,
                "currency":   f.commissionReport.currency  if f.commissionReport else None,
            }
            for f in ibc.fills()
        ]


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="IB MCP Server (extended)")
    parser.add_argument("--host",      default=IB_HOST,      help="IB Gateway host")
    parser.add_argument("--port",      default=IB_PORT,      type=int)
    parser.add_argument("--client-id", default=IB_CLIENT_ID, type=int)
    parser.add_argument("--transport", default=os.getenv("IB_MCP_TRANSPORT", "stdio"),
                        choices=["stdio", "http"])
    parser.add_argument("--http-host", default=os.getenv("IB_MCP_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--http-port", default=int(os.getenv("IB_MCP_HTTP_PORT", "8000")), type=int)
    args = parser.parse_args()

    global IB_HOST, IB_PORT, IB_CLIENT_ID
    IB_HOST      = args.host
    IB_PORT      = args.port
    IB_CLIENT_ID = args.client_id

    logger.info("IB MCP Server (extended) – Trading: %s",
                "ENABLED" if TRADING_ENABLED else "DISABLED (read-only)")
    logger.info("Connecting to IB Gateway at %s:%d (clientId=%d)", IB_HOST, IB_PORT, IB_CLIENT_ID)

    if args.transport == "http":
        mcp.run(transport="streamable-http", host=args.http_host, port=args.http_port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
