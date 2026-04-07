"""
IB MCP Server – Persistent Connection Edition
fastmcp==2.14.6  |  ib_async==2.1.0  |  Python 3.12

Architecture:
  One persistent IB connection shared by all tool calls.
  _get_ib() reconnects automatically on disconnect.
  reqMarketDataType(3) → delayed data, no subscription needed,
  no competing-session conflict (Error 10197).

Safety:
  READ_ONLY_API=yes  → IB Gateway blocks writes at protocol level
  TRADING_ENABLED=false → write tools raise RuntimeError before any call
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import ib_async as ib
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_cfg: dict[str, Any] = {
    "host":      os.getenv("IB_HOST", "127.0.0.1"),
    "port":      int(os.getenv("IB_PORT", "7497")),
    "client_id": int(os.getenv("IB_CLIENT_ID", "1")),
}

TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "false").strip().lower() in (
    "true", "yes", "1"
)

# ---------------------------------------------------------------------------
# Persistent connection state
# ---------------------------------------------------------------------------
_ib: ib.IB | None = None
_ib_lock: asyncio.Lock = asyncio.Lock()


async def _get_ib() -> ib.IB:
    """Return the shared IB connection, reconnecting if needed."""
    global _ib
    async with _ib_lock:
        if _ib is not None and _ib.isConnected():
            return _ib

        # Disconnect cleanly if stale
        if _ib is not None:
            try:
                _ib.disconnect()
            except Exception:
                pass

        _ib = ib.IB()
        try:
            await _ib.connectAsync(
                _cfg["host"], _cfg["port"], clientId=_cfg["client_id"]
            )
            # Delayed data (type 3): works without subscription, no
            # competing-session conflict with Mobile/Web sessions.
            # Switch to type 1 (live) in .env if a live subscription exists.
            market_data_type = int(os.getenv("IB_MARKET_DATA_TYPE", "3"))
            _ib.reqMarketDataType(market_data_type)
            logger.info(
                "IB connected: %s:%d  marketDataType=%d  trading=%s",
                _cfg["host"], _cfg["port"], market_data_type,
                "ENABLED" if TRADING_ENABLED else "DISABLED",
            )
        except Exception as exc:
            logger.error("IB connect failed: %s", exc)
            _ib = None
            raise RuntimeError(f"Cannot connect to IB Gateway: {exc}") from exc

        return _ib


def _on_disconnect() -> None:
    """Called by ib_async when the connection drops unexpectedly."""
    global _ib
    logger.warning("IB connection lost – will reconnect on next tool call")
    _ib = None


# ---------------------------------------------------------------------------
# FastMCP lifespan – connect on startup, disconnect on shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastMCP):
    logger.info("Server starting – connecting to IB Gateway …")
    try:
        ibc = await _get_ib()
        ibc.disconnectedEvent += _on_disconnect
    except Exception as exc:
        logger.warning("Initial IB connect failed (%s) – will retry on first call", exc)
    yield
    # Shutdown: clean disconnect
    global _ib
    if _ib is not None:
        try:
            _ib.disconnect()
            logger.info("IB disconnected cleanly on shutdown")
        except Exception:
            pass


def _require_trading() -> None:
    if not TRADING_ENABLED:
        raise RuntimeError(
            "Trading is disabled. Set TRADING_ENABLED=true and "
            "READ_ONLY_API=no to enable order placement."
        )


# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(name="ib-mcp", lifespan=_lifespan)


# ===========================================================================
# READ-ONLY TOOLS (13)
# ===========================================================================

@mcp.tool()
async def lookup_contract(
    symbol: str,
    exchange: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Look up contract details by ticker symbol and optional exchange/currency."""
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
    contract = ib.Stock(symbol, "SMART", "USD")
    await ibc.qualifyContractsAsync(contract)
    data = await ibc.reqFundamentalDataAsync(contract, report_type)
    return {"symbol": symbol, "report_type": report_type, "data": data}


@mcp.tool()
async def get_news(symbol: str, max_articles: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent news headlines for a stock."""
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
    article = await ibc.reqNewsArticleAsync(provider_code, article_id)
    return {"articleType": article.articleType, "articleText": article.articleText}


@mcp.tool()
async def get_account_summary() -> dict[str, Any]:
    """Retrieve account balances, equity, margin, and cash summary."""
    ibc = await _get_ib()
    summary = await ibc.accountSummaryAsync()
    return {
        item.tag: {"value": item.value, "currency": item.currency}
        for item in summary
    }


@mcp.tool()
async def get_positions() -> list[dict[str, Any]]:
    """Retrieve all current portfolio positions."""
    ibc = await _get_ib()
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


@mcp.tool()
async def get_option_chain(
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> dict[str, Any]:
    """Retrieve all available expirations and strikes for an underlying.

    Uses reqSecDefOptParams (IBKR-recommended, no throttling).
    Returns chain skeleton + current underlying price.

    Args:
        symbol:   Underlying ticker (e.g. "MSFT", "SPY", "ETN")
        exchange: default "SMART"
        currency: default "USD"
    """
    ibc = await _get_ib()
    underlying = ib.Stock(symbol, exchange, currency)
    await ibc.qualifyContractsAsync(underlying)

    chains = await ibc.reqSecDefOptParamsAsync(
        underlying.symbol, "", underlying.secType, underlying.conId
    )

    # Underlying price via persistent connection (already streaming)
    under_ticker = ibc.reqMktData(underlying, genericTickList="", snapshot=False)
    await asyncio.sleep(2)
    ibc.cancelMktData(underlying)
    under_price = under_ticker.marketPrice()
    if under_price != under_price:  # NaN check
        under_price = None

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

    Uses the persistent connection + reqMktData (works with delayed data,
    no competing-session conflict).

    Args:
        symbol:   Underlying ticker (e.g. "ETN")
        expiry:   YYYYMMDD  (e.g. "20260424")
        strike:   Strike price (e.g. 355.0)
        right:    "C" (call) or "P" (put)
        exchange: default "SMART"
        currency: default "USD"

    Returns:
        bid, ask, last, close, volume, openInterest,
        modelGreeks: { delta, gamma, theta, vega, impliedVol, undPrice }
    """
    right = right.upper()
    if right not in ("C", "P"):
        raise ValueError(f"right must be C or P, got: {right!r}")

    ibc = await _get_ib()
    contract = ib.Option(symbol, expiry, strike, right, exchange, currency=currency)
    details = await ibc.reqContractDetailsAsync(contract)
    if not details:
        return {"error": f"No contract: {symbol} {expiry} {strike}{right}"}
    qualified = details[0].contract

    # Subscribe, wait for data, cancel – persistent connection means
    # the stream sets up reliably even for options not in the watchlist.
    ticker = ibc.reqMktData(qualified, genericTickList="", snapshot=False)
    await asyncio.sleep(3)
    ibc.cancelMktData(qualified)

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
        "bid":          ticker.bid,
        "ask":          ticker.ask,
        "last":         ticker.last,
        "close":        ticker.close,
        "volume":       ticker.volume,
        "openInterest": getattr(ticker, "openInterest", None),
        "modelGreeks":  _greeks(ticker.modelGreeks),
        "bidGreeks":    _greeks(ticker.bidGreeks),
        "askGreeks":    _greeks(ticker.askGreeks),
    }


# ===========================================================================
# TRADING TOOLS (7)  –  require TRADING_ENABLED=true + READ_ONLY_API=no
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

    ibc = await _get_ib()
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
        limit_price: Required for LMT / STP
        tif:         "DAY" | "GTC"  (default: DAY)
        account:     Account ID (optional)
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
        raise ValueError(f"limit_price required for {order_type}")

    ibc = await _get_ib()
    contract = ib.Option(symbol, expiry, strike, right, exchange, currency=currency)
    details = await ibc.reqContractDetailsAsync(contract)
    if not details:
        raise RuntimeError(f"No option contract: {symbol} {expiry} {strike}{right}")
    qualified = details[0].contract
    order = ib.Order(action=action, totalQuantity=quantity,
                     orderType=order_type, tif=tif)
    if limit_price is not None: order.lmtPrice = limit_price
    if account:                 order.account  = account
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
        "conid":      qualified.conId,
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

    Claude always presents full order details and asks for confirmation first.

    Args:
        legs: list of dicts with keys:
              symbol, expiry (YYYYMMDD), strike, right (C/P),
              action (BUY/SELL), ratio (int), exchange (optional)
        action:      BUY (net debit) or SELL (net credit) for the combo
        quantity:    Number of spread contracts
        limit_price: Net limit price; None = market
        tif:         DAY | GTC  (default: DAY)
        account:     Account ID (optional)
    """
    _require_trading()
    if not legs or len(legs) < 2:
        raise ValueError("A spread requires at least 2 legs.")
    action = action.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got: {action!r}")

    ibc = await _get_ib()
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
            raise ValueError(f"leg right must be C or P, got: {right!r}")
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
        symbol=legs[0]["symbol"], secType="BAG", currency="USD",
        exchange=legs[0].get("exchange", "SMART"), comboLegs=combo_legs,
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
    ibc = await _get_ib()
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
# Entry point  –  ALL @mcp.tool() decorators above main() → all registered
# ===========================================================================

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="IB MCP Server (persistent connection)")
    parser.add_argument("--host",      default=_cfg["host"],      help="IB Gateway host")
    parser.add_argument("--port",      default=_cfg["port"],      type=int)
    parser.add_argument("--client-id", default=_cfg["client_id"], type=int)
    parser.add_argument("--transport", default=os.getenv("IB_MCP_TRANSPORT", "stdio"),
                        choices=["stdio", "http", "streamable-http", "sse"])
    parser.add_argument("--http-host", default=os.getenv("IB_MCP_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--http-port", default=int(os.getenv("IB_MCP_HTTP_PORT", "8000")),
                        type=int)
    args = parser.parse_args()

    _cfg["host"]      = args.host
    _cfg["port"]      = args.port
    _cfg["client_id"] = args.client_id

    logger.info("=" * 60)
    logger.info("IB MCP Server  fastmcp=2.14.6  ib_async=2.1.0")
    logger.info("Architecture: persistent connection (reconnects on drop)")
    logger.info("Gateway  : %s:%d  clientId=%d", _cfg["host"], _cfg["port"], _cfg["client_id"])
    logger.info("Trading  : %s", "ENABLED" if TRADING_ENABLED else "DISABLED")
    logger.info("MarketData: type %s (3=delayed/no-subscription)",
                os.getenv("IB_MARKET_DATA_TYPE", "3"))
    logger.info("Tools    : 13 read-only + 7 trading = 20 total")
    logger.info("=" * 60)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.http_host, port=args.http_port)


if __name__ == "__main__":
    main()
