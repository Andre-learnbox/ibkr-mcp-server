"""
IB MCP Server – Extended Edition
Standalone: all read-only tools + 6 trading tools (incl. spreads).

Tested against: fastmcp==2.14.6, ib_async==2.1.0, Python 3.12

Safety:
  READ_ONLY_API=yes     → IB Gateway blocks writes at protocol level
  TRADING_ENABLED=false → all write tools raise RuntimeError before any API call
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import ib_async as ib
from fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config – mutable dict; CLI args in main() update it in-place (no global)
# ---------------------------------------------------------------------------
_cfg: dict[str, Any] = {
    "host":      os.getenv("IB_HOST", "127.0.0.1"),
    "port":      int(os.getenv("IB_PORT", "7497")),
    "client_id": int(os.getenv("IB_CLIENT_ID", "1")),
}

TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "false").strip().lower() in (
    "true", "yes", "1"
)


@asynccontextmanager
async def _ib_connect() -> AsyncGenerator[ib.IB, None]:
    client = ib.IB()
    await client.connectAsync(_cfg["host"], _cfg["port"], clientId=_cfg["client_id"])
    try:
        yield client
    finally:
        client.disconnect()


def _require_trading() -> None:
    if not TRADING_ENABLED:
        raise RuntimeError(
            "Trading is disabled. "
            "Set TRADING_ENABLED=true and READ_ONLY_API=no to enable orders."
        )


mcp = FastMCP(name="ib-mcp")


# ===========================================================================
# READ-ONLY TOOLS (11)
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
async def ticker_to_conid(
    symbol: str,
    exchange: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Convert a ticker symbol to its IBKR contract ID (conid)."""
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
    """Get detailed contract information (industry, tick size, underlying, etc.)."""
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
    """Retrieve historical OHLCV bars.

    Args:
        symbol:    Ticker (e.g. AAPL)
        duration:  e.g. "1 M", "5 D", "1 Y"
        bar_size:  e.g. "1 day", "1 hour", "5 mins"
        data_type: TRADES | MIDPOINT | BID | ASK
    """
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
async def get_fundamental_data(
    symbol: str,
    report_type: str = "ReportsFinSummary",
) -> dict[str, Any]:
    """Retrieve fundamental data for a stock.

    report_type: ReportsFinSummary | ReportsOwnership | ReportSnapshot |
                 ReportsFinStatements | RESC | CalendarReport
    """
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, "SMART", "USD")
        await ibc.qualifyContractsAsync(contract)
        data = await ibc.reqFundamentalDataAsync(contract, report_type)
        return {"symbol": symbol, "report_type": report_type, "data": data}


@mcp.tool()
async def get_news(symbol: str, max_articles: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent news headlines for a stock."""
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
    """Retrieve historical news within a date range (format: YYYY-MM-DD HH:MM:SS)."""
    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, "SMART", "USD")
        await ibc.qualifyContractsAsync(contract)
        providers = await ibc.reqNewsProvidersAsync()
        provider_codes = "+".join(p.code for p in providers) if providers else "BRFG+DJNL"
        news = await ibc.reqHistoricalNewsAsync(
            contract.conId, provider_codes,
            startDateTime=start_date, endDateTime=end_date,
            totalResults=max_articles,
        )
        return [
            {"time": n.time, "providerCode": n.providerCode,
             "articleId": n.articleId, "headline": n.headline}
            for n in news
        ]


@mcp.tool()
async def get_article(provider_code: str, article_id: str) -> dict[str, Any]:
    """Retrieve the full text of a news article."""
    async with _ib_connect() as ibc:
        article = await ibc.reqNewsArticleAsync(provider_code, article_id)
        return {"articleType": article.articleType, "articleText": article.articleText}


@mcp.tool()
async def get_account_summary() -> dict[str, Any]:
    """Retrieve account balances, equity, margin, and cash summary."""
    async with _ib_connect() as ibc:
        summary = await ibc.accountSummaryAsync()
        return {
            item.tag: {"value": item.value, "currency": item.currency}
            for item in summary
        }


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
# TRADING TOOLS (6)  –  all require TRADING_ENABLED=true + READ_ONLY_API=no
# ===========================================================================

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
    """Place a single-leg stock order (requires TRADING_ENABLED=true).

    Args:
        symbol:      Ticker (e.g. AAPL)
        action:      BUY or SELL
        quantity:    Number of shares
        order_type:  MKT | LMT | STP | STP LMT  (default: MKT)
        limit_price: Required for LMT / STP LMT
        stop_price:  Required for STP / STP LMT
        exchange:    default SMART
        currency:    default USD
        tif:         DAY | GTC | IOC  (default: DAY)
        account:     Account ID (optional)
    """
    _require_trading()
    action     = action.upper()
    order_type = order_type.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got: {action!r}")
    if order_type in ("LMT", "STP LMT") and limit_price is None:
        raise ValueError(f"limit_price required for {order_type}")
    if order_type in ("STP", "STP LMT") and stop_price is None:
        raise ValueError(f"stop_price required for {order_type}")

    async with _ib_connect() as ibc:
        contract = ib.Stock(symbol, exchange, currency)
        await ibc.qualifyContractsAsync(contract)
        order = ib.Order(action=action, totalQuantity=quantity,
                         orderType=order_type, tif=tif)
        if limit_price is not None: order.lmtPrice = limit_price
        if stop_price  is not None: order.auxPrice = stop_price
        if account:                 order.account  = account
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
async def place_spread_order(
    legs: list[dict[str, Any]],
    action: str,
    quantity: float,
    limit_price: float | None = None,
    tif: str = "DAY",
    account: str = "",
) -> dict[str, Any]:
    """Place a multi-leg options spread (Bull Put, Bear Call, Iron Condor, etc.).
    Requires TRADING_ENABLED=true.

    Claude will always show the full order details and ask for confirmation
    before calling this tool.

    Args:
        legs: List of leg dicts, each containing:
              - symbol   : underlying ticker  (e.g. "MSFT")
              - expiry   : YYYYMMDD           (e.g. "20260417")
              - strike   : float              (e.g. 360.0)
              - right    : "C" (call) or "P" (put)
              - action   : "BUY" or "SELL"   (per-leg direction)
              - ratio    : int, usually 1
              - exchange : optional, default "SMART"
        action:      Overall combo action – BUY (net debit) or SELL (net credit)
        quantity:    Number of spread contracts
        limit_price: Net limit price (debit or credit); None = market order
        tif:         DAY | GTC  (default: DAY)
        account:     Account ID (optional)

    Example – Bull Put Spread on MSFT (sell 360P, buy 340P, Apr 2026):
        legs=[
          {"symbol":"MSFT","expiry":"20260417","strike":360,"right":"P",
           "ratio":1,"action":"SELL"},
          {"symbol":"MSFT","expiry":"20260417","strike":340,"right":"P",
           "ratio":1,"action":"BUY"},
        ]
        action="SELL", quantity=1, limit_price=2.50
    """
    _require_trading()
    if not legs or len(legs) < 2:
        raise ValueError("A spread requires at least 2 legs.")
    action = action.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got: {action!r}")

    async with _ib_connect() as ibc:
        combo_legs: list[ib.ComboLeg] = []
        leg_details: list[dict[str, Any]] = []

        for leg in legs:
            sym      = leg["symbol"]
            expiry   = str(leg["expiry"])
            strike   = float(leg["strike"])
            right    = leg["right"].upper()
            ratio    = int(leg.get("ratio", 1))
            leg_act  = leg["action"].upper()
            exchange = leg.get("exchange", "SMART")

            if right not in ("C", "P"):
                raise ValueError(f"right must be C or P, got: {right!r}")
            if leg_act not in ("BUY", "SELL"):
                raise ValueError(f"leg action must be BUY or SELL, got: {leg_act!r}")

            opt = ib.Option(sym, expiry, strike, right, exchange)
            details = await ibc.reqContractDetailsAsync(opt)
            if not details:
                raise RuntimeError(f"No contract: {sym} {expiry} {strike}{right}")
            conid = details[0].contract.conId

            combo_legs.append(ib.ComboLeg(
                conId=conid, ratio=ratio, action=leg_act, exchange=exchange,
            ))
            leg_details.append({
                "symbol": sym, "expiry": expiry, "strike": strike,
                "right": right, "action": leg_act, "ratio": ratio, "conid": conid,
            })

        bag = ib.Contract(
            symbol=legs[0]["symbol"],
            secType="BAG",
            currency="USD",
            exchange=legs[0].get("exchange", "SMART"),
            comboLegs=combo_legs,
        )
        order_type = "LMT" if limit_price is not None else "MKT"
        order = ib.Order(action=action, totalQuantity=quantity,
                         orderType=order_type, tif=tif)
        if limit_price is not None: order.lmtPrice = limit_price
        if account:                 order.account  = account

        trade = ibc.placeOrder(bag, order)
        await asyncio.sleep(1)
        return {
            "orderId":    trade.order.orderId,
            "status":     trade.orderStatus.status,
            "action":     action,
            "quantity":   quantity,
            "orderType":  order_type,
            "limitPrice": limit_price,
            "tif":        tif,
            "legs":       leg_details,
        }


@mcp.tool()
async def cancel_order(order_id: int) -> dict[str, Any]:
    """Cancel an open order by IBKR order ID (requires TRADING_ENABLED=true)."""
    _require_trading()
    async with _ib_connect() as ibc:
        trades = ibc.openTrades()
        target = next((t for t in trades if t.order.orderId == order_id), None)
        if target is None:
            return {"error": f"No open order with orderId={order_id}"}
        ibc.cancelOrder(target.order)
        await asyncio.sleep(1)
        still_open = any(t.order.orderId == order_id for t in ibc.openTrades())
        return {"orderId": order_id,
                "status": "PendingCancel" if still_open else "Cancelled"}


@mcp.tool()
async def get_open_orders() -> list[dict[str, Any]]:
    """List all open orders (requires TRADING_ENABLED=true)."""
    _require_trading()
    async with _ib_connect() as ibc:
        await ibc.reqOpenOrdersAsync()
        return [
            {
                "orderId":    t.order.orderId,
                "symbol":     t.contract.symbol,
                "secType":    t.contract.secType,
                "action":     t.order.action,
                "quantity":   t.order.totalQuantity,
                "orderType":  t.order.orderType,
                "limitPrice": getattr(t.order, "lmtPrice", None),
                "stopPrice":  getattr(t.order, "auxPrice", None),
                "tif":        t.order.tif,
                "status":     t.orderStatus.status,
                "filled":     t.orderStatus.filled,
                "remaining":  t.orderStatus.remaining,
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
    """Modify quantity, price, or TIF of an open order (requires TRADING_ENABLED=true)."""
    _require_trading()
    async with _ib_connect() as ibc:
        trades = ibc.openTrades()
        target = next((t for t in trades if t.order.orderId == order_id), None)
        if target is None:
            return {"error": f"No open order with orderId={order_id}"}
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
    """Retrieve executed fills from the current session (requires TRADING_ENABLED=true)."""
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


@mcp.tool()
async def place_option_order(
    symbol: str,
    right: str,
    strike: float,
    expiry: str,
    action: str,
    quantity: int,
    order_type: str = "LMT",
    limit_price: float | None = None,
    tif: str = "DAY",
    account: str = "",
    exchange: str = "SMART",
    currency: str = "USD",
) -> dict[str, Any]:
    """Place a single-leg options order (requires TRADING_ENABLED=true).

    Args:
        symbol:      Underlying ticker (e.g. "SPY")
        right:       "C" (call) or "P" (put)
        strike:      Strike price (e.g. 655.0)
        expiry:      Expiry date YYYYMMDD (e.g. "20260501")
        action:      "BUY" or "SELL"
        quantity:    Number of contracts
        order_type:  "LMT" | "MKT" | "STP"  (default: LMT)
        limit_price: Limit price in USD – required for LMT / STP
        tif:         "DAY" | "GTC"  (default: DAY)
        account:     Account ID (e.g. "DU8111665"), optional
        exchange:    default "SMART"
        currency:    default "USD"
    """
    _require_trading()

    right      = right.upper()
    action     = action.upper()
    order_type = order_type.upper()

    if right not in ("C", "P"):
        raise ValueError(f"right must be C or P, got: {right!r}")
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got: {action!r}")
    if order_type in ("LMT", "STP") and limit_price is None:
        raise ValueError(f"limit_price is required for {order_type} orders")

    async with _ib_connect() as ibc:
        contract = ib.Option(symbol, expiry, strike, right, exchange, currency=currency)
        details = await ibc.reqContractDetailsAsync(contract)
        if not details:
            raise RuntimeError(f"No option contract found: {symbol} {expiry} {strike}{right}")
        qualified = details[0].contract

        order = ib.Order(
            action=action,
            totalQuantity=quantity,
            orderType=order_type,
            tif=tif,
        )
        if limit_price is not None:
            order.lmtPrice = limit_price
        if account:
            order.account = account

        trade = ibc.placeOrder(qualified, order)
        await asyncio.sleep(1)

        return {
            "orderId":    trade.order.orderId,
            "status":     trade.orderStatus.status,
            "symbol":     symbol,
            "right":      right,
            "strike":     strike,
            "expiry":     expiry,
            "action":     action,
            "quantity":   quantity,
            "orderType":  order_type,
            "limitPrice": limit_price,
            "tif":        tif,
            "conid":      qualified.conId,
        }


# ===========================================================================
@mcp.tool()
async def get_option_chain(
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> dict[str, Any]:
    """Retrieve all available expirations and strikes for an underlying.

    Uses reqSecDefOptParams (IBKR-recommended, no throttling).
    Returns the chain skeleton – use get_option_price() to get
    bid/ask/greeks for specific contracts.

    Args:
        symbol:   Underlying ticker (e.g. "MSFT", "SPY")
        exchange: default "SMART"
        currency: default "USD"

    Returns:
        chains: list of { exchange, tradingClass, multiplier,
                          expirations (sorted), strikes (sorted) }
        underlying: { conid, last_price }
    """
    async with _ib_connect() as ibc:
        underlying = ib.Stock(symbol, exchange, currency)
        await ibc.qualifyContractsAsync(underlying)

        chains = await ibc.reqSecDefOptParamsAsync(
            underlying.symbol, "", underlying.secType, underlying.conId
        )

        # Also fetch current underlying price for context
        tickers = await ibc.reqTickersAsync(underlying)
        under_price = tickers[0].marketPrice() if tickers else None

        return {
            "underlying": {
                "symbol":  underlying.symbol,
                "conid":   underlying.conId,
                "price":   under_price,
            },
            "chains": [
                {
                    "exchange":     c.exchange,
                    "tradingClass": c.tradingClass,
                    "multiplier":   c.multiplier,
                    "expirations":  sorted(c.expirations),
                    "strikes":      sorted(c.strikes),
                }
                for c in chains
            ],
        }


@mcp.tool()
async def get_option_price(
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> dict[str, Any]:
    """Get current bid/ask/last and Greeks for a specific option contract.

    Uses reqTickersAsync which returns modelGreeks (delta, gamma,
    theta, vega, impliedVol) calculated by the IB model.

    Args:
        symbol:   Underlying ticker (e.g. "MSFT")
        expiry:   YYYYMMDD  (e.g. "20260417")
        strike:   Strike price (e.g. 360.0)
        right:    "C" (call) or "P" (put)
        exchange: default "SMART"
        currency: default "USD"

    Returns:
        bid, ask, last, volume, openInterest,
        modelGreeks: { delta, gamma, theta, vega, impliedVol, undPrice }
    """
    right = right.upper()
    if right not in ("C", "P"):
        raise ValueError(f"right must be C or P, got: {right!r}")

    async with _ib_connect() as ibc:
        contract = ib.Option(symbol, expiry, strike, right, exchange, currency=currency)
        details = await ibc.reqContractDetailsAsync(contract)
        if not details:
            return {"error": f"No contract: {symbol} {expiry} {strike}{right}"}
        qualified = details[0].contract

        tickers = await ibc.reqTickersAsync(qualified)
        if not tickers:
            return {"error": "No market data returned"}
        t = tickers[0]

        def _greeks(g: ib.OptionComputation | None) -> dict[str, Any] | None:
            if g is None:
                return None
            return {
                "delta":      g.delta,
                "gamma":      g.gamma,
                "theta":      g.theta,
                "vega":       g.vega,
                "impliedVol": g.impliedVol,
                "optPrice":   g.optPrice,
                "undPrice":   g.undPrice,
            }

        return {
            "symbol":       symbol,
            "expiry":       expiry,
            "strike":       strike,
            "right":        right,
            "conid":        qualified.conId,
            "bid":          t.bid,
            "ask":          t.ask,
            "last":         t.last,
            "close":        t.close,
            "volume":       t.volume,
            "openInterest": getattr(t, "openInterest", None),
            "modelGreeks":  _greeks(t.modelGreeks),
            "bidGreeks":    _greeks(t.bidGreeks),
            "askGreeks":    _greeks(t.askGreeks),
        }


# Entry point  –  must be LAST so all @mcp.tool() decorators run first
# ===========================================================================

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="IB MCP Server (extended)")
    parser.add_argument("--host",      default=_cfg["host"],      help="IB Gateway host")
    parser.add_argument("--port",      default=_cfg["port"],      type=int)
    parser.add_argument("--client-id", default=_cfg["client_id"], type=int)
    parser.add_argument("--transport", default=os.getenv("IB_MCP_TRANSPORT", "stdio"),
                        choices=["stdio", "http", "streamable-http", "sse"])
    parser.add_argument("--http-host", default=os.getenv("IB_MCP_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--http-port", default=int(os.getenv("IB_MCP_HTTP_PORT", "8000")), type=int)
    args = parser.parse_args()

    _cfg["host"]      = args.host
    _cfg["port"]      = args.port
    _cfg["client_id"] = args.client_id

    logger.info("=" * 55)
    logger.info("IB MCP Server (extended)  fastmcp=2.14.6  ib_async=2.1.0")
    logger.info("Gateway  : %s:%d (clientId=%d)", _cfg["host"], _cfg["port"], _cfg["client_id"])
    logger.info("Trading  : %s", "ENABLED" if TRADING_ENABLED else "DISABLED")
    logger.info("Tools    : 11 read-only + 6 trading = 17 total")
    logger.info("=" * 55)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.http_host, port=args.http_port)


if __name__ == "__main__":
    main()
