"""
strategies.py
Rules-based signal generation for three strategies, tagged separately:

  1. long_option    -- buy calls/puts when IV is cheap + trend agrees
  2. covered_call   -- sell OTM calls against shares you already hold
  3. credit_spread  -- sell defined-risk vertical spreads when IV is rich

Every function returns a list of signal dicts with a 'reason' field
explaining WHY the signal fired, so nothing is a black box. These signals
are candidates for the paper trade logger -- they are not auto-executed
without going through main.py's confirmation/logging step.
"""

import config
import data_fetcher
import iv_analysis


def _closest_by_delta(contracts, target_delta, option_type):
    """Pick the contract of a given type whose delta is closest to target."""
    candidates = [c for c in contracts if c.get("type") == option_type and c.get("delta") is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(abs(c["delta"]) - target_delta))


def _best_same_expiry_spread(contracts, option_type, short_delta, long_delta):
    """
    A valid vertical spread requires both legs at the SAME expiration.
    Groups contracts by expiry, finds the best short/long leg pair within
    each expiry, returns the pair from whichever expiry has the most
    contracts available (proxy for liquidity).
    """
    by_expiry = {}
    for c in contracts:
        if c.get("type") != option_type or c.get("delta") is None:
            continue
        by_expiry.setdefault(c["expiry"], []).append(c)

    best_pair = (None, None)
    best_count = -1
    for expiry, group in by_expiry.items():
        short_leg = min(group, key=lambda c: abs(abs(c["delta"]) - short_delta))
        long_leg = min(group, key=lambda c: abs(abs(c["delta"]) - long_delta))
        if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
            continue
        if len(group) > best_count:
            best_count = len(group)
            best_pair = (short_leg, long_leg)

    return best_pair


def _best_same_expiry_spread(contracts, option_type, short_delta, long_delta):
    """
    A valid vertical spread requires both legs at the SAME expiration.
    Picking each leg independently by closest-delta across the whole
    chain (as an earlier version of this code did) can pick two
    different expiries, which corrupts the width/credit math. This
    groups contracts by expiry first, then finds the best short/long
    leg pair within each expiry, and returns the pair from whichever
    expiry has the most contracts available (a reasonable proxy for
    liquidity, since Alpaca's chain snapshot doesn't include volume).
    """
    by_expiry = {}
    for c in contracts:
        if c.get("type") != option_type or c.get("delta") is None:
            continue
        by_expiry.setdefault(c["expiry"], []).append(c)

    best_pair = (None, None)
    best_count = -1
    for expiry, group in by_expiry.items():
        short_leg = min(group, key=lambda c: abs(abs(c["delta"]) - short_delta))
        long_leg = min(group, key=lambda c: abs(abs(c["delta"]) - long_delta))
        if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
            continue
        if len(group) > best_count:
            best_count = len(group)
            best_pair = (short_leg, long_leg)

    return best_pair


def scan_long_options(symbol):
    """
    Strategy 1: Long calls/puts as a stock alternative.
    Buy when IV rank is low (cheap) AND trend supports the direction.
    """
    cfg = config.LONG_OPTION
    if not cfg["enabled"]:
        return []

    signals = []
    trend = data_fetcher.simple_trend(symbol, cfg["trend_lookback_days"])
    contracts = data_fetcher.get_option_chain(symbol, cfg["min_days_to_expiry"], cfg["max_days_to_expiry"])
    if not contracts:
        return signals

    # Use median IV across the fetched chain as the "current IV" proxy
    ivs = [c["iv"] for c in contracts if c.get("iv")]
    if not ivs:
        return signals
    current_iv = sorted(ivs)[len(ivs) // 2]

    iv_info = iv_analysis.classify(current_iv, symbol)
    if iv_info["iv_rank"] > cfg["max_iv_rank"]:
        return signals  # too expensive to buy premium right now

    direction = None
    if trend == "up":
        direction = "call"
    elif trend == "down":
        direction = "put"
    else:
        return signals  # no clear directional edge, skip

    contract = _closest_by_delta(contracts, cfg["target_delta"], direction)
    if contract is None or contract.get("mid") is None:
        return signals

    stock_price = data_fetcher.get_daily_bars(symbol, 5)["close"].iloc[-1]
    premium_pct = contract["mid"] / stock_price
    if premium_pct > cfg["max_premium_pct_of_stock"]:
        return signals  # too rich in absolute terms even if IV rank is low

    signals.append({
        "strategy": "long_option",
        "symbol": symbol,
        "action": "BUY_TO_OPEN",
        "contract": contract["occ_symbol"],
        "type": direction,
        "strike": contract["strike"],
        "expiry": contract["expiry"],
        "est_premium": contract["mid"],
        "iv_rank": iv_info["iv_rank"],
        "reason": (
            f"Trend={trend}, IV rank={iv_info['iv_rank']} (cheap threshold "
            f"{cfg['max_iv_rank']}), delta target {cfg['target_delta']}, "
            f"premium {premium_pct:.2%} of stock price."
        ),
    })
    return signals


def scan_covered_calls(symbol, shares_held=0):
    """
    Strategy 2: Sell covered calls against shares already held.
    Only fires if shares_held >= 100 and IV rank is rich enough to be
    worth collecting premium.
    """
    cfg = config.COVERED_CALL
    if not cfg["enabled"]:
        return []
    if cfg["requires_shares_held"] and shares_held < 100:
        return []

    signals = []
    contracts = data_fetcher.get_option_chain(symbol, cfg["min_days_to_expiry"], cfg["max_days_to_expiry"])
    if not contracts:
        return signals

    ivs = [c["iv"] for c in contracts if c.get("iv")]
    if not ivs:
        return signals
    current_iv = sorted(ivs)[len(ivs) // 2]

    iv_info = iv_analysis.classify(current_iv, symbol)
    if iv_info["iv_rank"] < cfg["min_iv_rank"]:
        return signals  # not enough premium to justify capping upside

    contract = _closest_by_delta(contracts, cfg["target_delta"], "call")
    if contract is None or contract.get("mid") is None:
        return signals

    max_contracts = shares_held // 100
    signals.append({
        "strategy": "covered_call",
        "symbol": symbol,
        "action": "SELL_TO_OPEN",
        "contract": contract["occ_symbol"],
        "type": "call",
        "strike": contract["strike"],
        "expiry": contract["expiry"],
        "est_premium": contract["mid"],
        "max_contracts": max_contracts,
        "iv_rank": iv_info["iv_rank"],
        "reason": (
            f"IV rank={iv_info['iv_rank']} (rich threshold {cfg['min_iv_rank']}), "
            f"delta target {cfg['target_delta']}. Covers up to {max_contracts} "
            f"contract(s) against {shares_held} shares held."
        ),
    })
    return signals


def scan_credit_spreads(symbol):
    """
    Strategy 3: Defined-risk vertical credit spreads.
    Sell the short leg near target delta, buy the long leg further OTM
    to cap risk, only when IV rank is rich and the credit collected is
    a reasonable fraction of the width between strikes.
    """
    cfg = config.CREDIT_SPREAD
    if not cfg["enabled"]:
        return []

    signals = []
    contracts = data_fetcher.get_option_chain(symbol, cfg["min_days_to_expiry"], cfg["max_days_to_expiry"])
    if not contracts:
        return signals

    ivs = [c["iv"] for c in contracts if c.get("iv")]
    if not ivs:
        return signals
    current_iv = sorted(ivs)[len(ivs) // 2]

    iv_info = iv_analysis.classify(current_iv, symbol)
    if iv_info["iv_rank"] < cfg["min_iv_rank"]:
        return signals

    trend = data_fetcher.simple_trend(symbol, 20)
    # Bullish/flat -> sell put credit spread; bearish/flat -> sell call credit spread
    option_type = "put" if trend in ("up", "flat") else "call"

    short_leg, long_leg = _best_same_expiry_spread(
        contracts, option_type, cfg["short_leg_delta"], cfg["long_leg_delta"]
    )
    if short_leg is None or long_leg is None:
        return signals
    if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
        return signals  # need two distinct strikes

    width = abs(short_leg["strike"] - long_leg["strike"])
    if width == 0:
        return signals

    credit = (short_leg["mid"] or 0) - (long_leg["mid"] or 0)
    if credit <= 0:
        return signals

    ratio = credit / width
    if ratio < cfg["min_credit_to_width_ratio"]:
        return signals

    signals.append({
        "strategy": "credit_spread",
        "symbol": symbol,
        "action": "SELL_TO_OPEN_SPREAD",
        "short_leg": short_leg["occ_symbol"],
        "long_leg": long_leg["occ_symbol"],
        "type": option_type,
        "width": width,
        "est_credit": round(credit, 2),
        "credit_to_width_ratio": round(ratio, 3),
        "iv_rank": iv_info["iv_rank"],
        "reason": (
            f"IV rank={iv_info['iv_rank']} (rich threshold {cfg['min_iv_rank']}), "
            f"{option_type} credit spread width {width}, credit/width ratio "
            f"{ratio:.2%} (min {cfg['min_credit_to_width_ratio']:.0%}), trend={trend}."
        ),
    })
    return signals


def scan_all(symbol, shares_held=0):
    """Run all three strategies for one symbol, tagged separately."""
    return {
        "long_option": scan_long_options(symbol),
        "covered_call": scan_covered_calls(symbol, shares_held=shares_held),
        "credit_spread": scan_credit_spreads(symbol),
    }
