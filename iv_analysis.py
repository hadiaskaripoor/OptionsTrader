"""
iv_analysis.py
Computes IV Rank and IV Percentile -- the metrics that actually define
whether an option is "cheap" or "expensive", as opposed to raw premium.

IV Rank    = (current_iv - iv_52wk_low) / (iv_52wk_high - iv_52wk_low)
IV Percentile = % of days in the lookback window where IV was below current IV

Since Alpaca's option data API gives current snapshots rather than a long
history of daily IV, this module approximates the 52-week IV range using
realized volatility history as a proxy, with a clearly logged caveat.
For production use, a dedicated historical-IV data vendor (e.g. ORATS,
CBOE DataShop) would give a cleaner IV Rank calculation.
"""

import config
from data_fetcher import get_daily_bars
import math
import statistics


def historical_realized_vol_series(symbol, lookback_days=252, window=20):
    """
    Builds a rolling realized-volatility series as a proxy for a historical
    IV series. Returns a list of annualized vol values, one per trading day
    after the initial window.
    """
    df = get_daily_bars(symbol, lookback_days=lookback_days + window)
    closes = df["close"].tolist()
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    series = []
    for i in range(window, len(log_rets)):
        chunk = log_rets[i - window:i]
        vol = statistics.pstdev(chunk) * math.sqrt(252)
        series.append(vol)
    return series


def iv_rank(current_iv, symbol, lookback_days=config.IV_RANK_LOOKBACK_DAYS):
    """
    Approximate IV Rank using the realized-vol proxy series as the range.
    Returns a value in [0, 1] (clamped), plus the proxy low/high used.
    """
    series = historical_realized_vol_series(symbol, lookback_days=lookback_days)
    if not series:
        raise ValueError(f"Could not build vol history for {symbol}")

    lo, hi = min(series), max(series)
    if hi == lo:
        return 0.5, lo, hi  # no range to rank against

    rank = (current_iv - lo) / (hi - lo)
    rank = max(0.0, min(1.0, rank))
    return rank, lo, hi


def iv_percentile(current_iv, symbol, lookback_days=config.IV_RANK_LOOKBACK_DAYS):
    """
    Approximate IV Percentile: fraction of days in the proxy series
    where vol was below current_iv.
    """
    series = historical_realized_vol_series(symbol, lookback_days=lookback_days)
    if not series:
        raise ValueError(f"Could not build vol history for {symbol}")

    below = sum(1 for v in series if v < current_iv)
    return below / len(series)


def classify(current_iv, symbol):
    """
    Returns a dict summarizing IV rank/percentile and a 'cheap' / 'rich' /
    'neutral' classification per the thresholds in config.py.
    """
    rank, lo, hi = iv_rank(current_iv, symbol)
    pct = iv_percentile(current_iv, symbol)

    if rank <= config.IV_RANK_CHEAP_THRESHOLD:
        label = "cheap"
    elif rank >= config.IV_RANK_EXPENSIVE_THRESHOLD:
        label = "rich"
    else:
        label = "neutral"

    return {
        "symbol": symbol,
        "current_iv": current_iv,
        "iv_rank": round(rank, 3),
        "iv_percentile": round(pct, 3),
        "proxy_range_low": round(lo, 3),
        "proxy_range_high": round(hi, 3),
        "label": label,
    }
