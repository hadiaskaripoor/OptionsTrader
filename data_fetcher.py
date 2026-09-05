"""
data_fetcher.py
Thin wrapper around Alpaca's market data + trading APIs.

Requires: pip install alpaca-py
Docs: https://docs.alpaca.markets/docs/about-market-data-api

This module intentionally fails loudly (raises) rather than silently
returning fake data, so you always know if a live call didn't work.
"""

from datetime import datetime, timedelta
import statistics

import config


def _get_trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)


def _get_stock_data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)


def _get_option_data_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    return OptionHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)


def get_account():
    """Returns account info (paper trading) -- balance, buying power, etc."""
    client = _get_trading_client()
    return client.get_account()


def get_positions():
    """Returns current open positions (paper trading)."""
    client = _get_trading_client()
    return client.get_all_positions()


def get_daily_bars(symbol, lookback_days=252):
    """
    Fetch daily OHLC bars for a symbol, used for:
      - realized volatility calc
      - simple trend/momentum filter
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    client = _get_stock_data_client()
    end = datetime.now()
    start = end - timedelta(days=int(lookback_days * 1.6))  # buffer for weekends/holidays

    # Free Alpaca accounts only have access to the IEX feed, not the paid
    # SIP (full consolidated market) feed. IEX is a single exchange's data,
    # not the full tape, but it's a reasonable proxy for daily closes used
    # in realized-vol/trend calcs here. Upgrade to SIP later if you want
    # tighter accuracy.
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df is None or df.empty:
        raise ValueError(f"No daily bars returned for {symbol}")
    return df.tail(lookback_days)


def realized_volatility(symbol, lookback_days=252):
    """
    Annualized realized volatility from daily close-to-close log returns.
    Used as a sanity check against option-implied volatility.
    """
    import math

    df = get_daily_bars(symbol, lookback_days=lookback_days)
    closes = df["close"].tolist()
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(log_rets) < 2:
        raise ValueError(f"Not enough data to compute realized vol for {symbol}")
    daily_std = statistics.pstdev(log_rets)
    return daily_std * math.sqrt(252)


def simple_trend(symbol, lookback_days=20):
    """
    Returns 'up', 'down', or 'flat' based on whether the latest close
    is above/below the simple moving average over lookback_days.
    This is a crude filter, not a signal on its own.
    """
    df = get_daily_bars(symbol, lookback_days=lookback_days + 5)
    closes = df["close"].tolist()
    sma = sum(closes[-lookback_days:]) / lookback_days
    last = closes[-1]
    if last > sma * 1.01:
        return "up"
    elif last < sma * 0.99:
        return "down"
    return "flat"


def get_option_chain(symbol, min_dte, max_dte):
    """
    Fetch the option chain for a symbol filtered to a DTE window.
    Returns a list of contract dicts with strike, expiry, bid, ask,
    implied_volatility, delta (Alpaca options data includes greeks
    on the snapshot endpoint).
    """
    from alpaca.data.requests import OptionChainRequest

    client = _get_option_data_client()
    today = datetime.now().date()
    exp_gte = today + timedelta(days=min_dte)
    exp_lte = today + timedelta(days=max_dte)

    # Free-tier accounts get the 'indicative' options feed rather than the
    # paid OPRA feed. Indicative quotes are a reasonable approximation but
    # not the full consolidated tape -- same tradeoff as IEX vs SIP above.
    req = OptionChainRequest(
        underlying_symbol=symbol,
        expiration_date_gte=exp_gte,
        expiration_date_lte=exp_lte,
        feed="indicative",
    )
    snapshot = client.get_option_chain(req)

    contracts = []
    for occ_symbol, data in snapshot.items():
        quote = getattr(data, "latest_quote", None)
        greeks = getattr(data, "greeks", None)
        iv = getattr(data, "implied_volatility", None)
        if quote is None:
            continue

        parsed = _parse_occ_symbol(occ_symbol)
        if parsed is None:
            continue

        contracts.append({
            "occ_symbol": occ_symbol,
            "strike": parsed["strike"],
            "expiry": parsed["expiry"],
            "type": parsed["type"],  # 'call' or 'put'
            "bid": quote.bid_price,
            "ask": quote.ask_price,
            "mid": (quote.bid_price + quote.ask_price) / 2 if quote.bid_price and quote.ask_price else None,
            "iv": iv,
            "delta": greeks.delta if greeks else None,
        })
    return contracts


def _parse_occ_symbol(occ_symbol):
    """
    Parse an OCC option symbol into its components. Format is fixed:
      <root symbol><YYMMDD><C or P><8-digit strike, implied 3 decimals>
    e.g. AAPL261009P00205000 -> AAPL, 2026-10-09, put, strike 205.00

    More reliable than guessing attribute names on Alpaca's snapshot
    objects, which vary across alpaca-py versions and were returning
    None for strike/expiry/type in testing.
    """
    import re
    from datetime import datetime as _dt

    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', occ_symbol)
    if not m:
        return None

    root, date_str, cp, strike_str = m.groups()
    expiry = _dt.strptime(date_str, "%y%m%d").date()
    strike = int(strike_str) / 1000.0
    opt_type = "call" if cp == "C" else "put"

    return {"root": root, "expiry": expiry, "type": opt_type, "strike": strike}