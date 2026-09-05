"""
debug_strategy.py
Shows the actual intermediate values at each filter step for one symbol,
instead of just pass/fail. Use this to see WHY a strategy did or didn't
fire -- e.g. what the trend was, what IV rank came out to, whether a
delta-matched contract was even found.

Usage:
    python debug_strategy.py AAPL
"""

import sys
import config
import data_fetcher
import iv_analysis
from strategies import _closest_by_delta, _best_same_expiry_spread

symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

print(f"=== Debugging {symbol} ===\n")

# --- Trend ---
try:
    trend = data_fetcher.simple_trend(symbol, config.LONG_OPTION["trend_lookback_days"])
    print(f"Trend ({config.LONG_OPTION['trend_lookback_days']}d SMA): {trend}")
except Exception as e:
    print(f"Trend calc FAILED: {e}")
    trend = None

# --- Option chain (long_option window) ---
cfg = config.LONG_OPTION
try:
    contracts = data_fetcher.get_option_chain(symbol, cfg["min_days_to_expiry"], cfg["max_days_to_expiry"])
    print(f"\nContracts in {cfg['min_days_to_expiry']}-{cfg['max_days_to_expiry']} DTE window: {len(contracts)}")
except Exception as e:
    print(f"Option chain fetch FAILED: {e}")
    contracts = []

if not contracts:
    print("No contracts -- stopping here.")
    sys.exit(0)

ivs = [c["iv"] for c in contracts if c.get("iv")]
print(f"Contracts with IV: {len(ivs)}")
if not ivs:
    print("No IV data -- stopping here.")
    sys.exit(0)

current_iv = sorted(ivs)[len(ivs) // 2]
print(f"Median IV across chain: {current_iv:.4f}")

# --- IV Rank ---
try:
    iv_info = iv_analysis.classify(current_iv, symbol)
    print(f"\nIV Rank info: {iv_info}")
except Exception as e:
    print(f"IV Rank calc FAILED: {e}")
    iv_info = None

# --- long_option checks ---
print(f"\n--- long_option strategy checks ---")
print(f"max_iv_rank threshold: {cfg['max_iv_rank']}")
if iv_info:
    print(f"actual iv_rank: {iv_info['iv_rank']} -> {'PASS (cheap enough)' if iv_info['iv_rank'] <= cfg['max_iv_rank'] else 'FAIL (too expensive)'}")
print(f"trend: {trend} -> {'usable (up/down)' if trend in ('up','down') else 'FAIL (flat, no directional signal)'}")

if trend in ("call", "put") or trend in ("up", "down"):
    direction = "call" if trend == "up" else ("put" if trend == "down" else None)
    if direction:
        contract = _closest_by_delta(contracts, cfg["target_delta"], direction)
        if contract:
            stock_price = data_fetcher.get_daily_bars(symbol, 5)["close"].iloc[-1]
            premium_pct = contract["mid"] / stock_price if contract.get("mid") else None
            print(f"Closest {direction} by delta {cfg['target_delta']}: {contract}")
            print(f"Stock price: {stock_price:.2f}, premium_pct: {premium_pct:.2%} (max allowed {cfg['max_premium_pct_of_stock']:.0%})" if premium_pct else "mid price missing on matched contract")
        else:
            print(f"No {direction} contract found near target delta {cfg['target_delta']}")

# --- credit_spread checks ---
ccfg = config.CREDIT_SPREAD
print(f"\n--- credit_spread strategy checks ---")
print(f"min_iv_rank threshold: {ccfg['min_iv_rank']}")
if iv_info:
    print(f"actual iv_rank: {iv_info['iv_rank']} -> {'PASS (rich enough)' if iv_info['iv_rank'] >= ccfg['min_iv_rank'] else 'FAIL (not rich enough)'}")

if iv_info and iv_info['iv_rank'] >= ccfg['min_iv_rank']:
    cs_contracts = data_fetcher.get_option_chain(symbol, ccfg["min_days_to_expiry"], ccfg["max_days_to_expiry"])
    print(f"Contracts in {ccfg['min_days_to_expiry']}-{ccfg['max_days_to_expiry']} DTE window: {len(cs_contracts)}")

    option_type = "put" if trend in ("up", "flat") else "call"
    print(f"Trend={trend} -> selling {option_type} credit spread")

    short_leg, long_leg = _best_same_expiry_spread(
        cs_contracts, option_type, ccfg["short_leg_delta"], ccfg["long_leg_delta"]
    )
    print(f"Short leg (target delta {ccfg['short_leg_delta']}): {short_leg}")
    print(f"Long leg (target delta {ccfg['long_leg_delta']}): {long_leg}")

    if short_leg and long_leg:
        if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
            print("FAIL: short and long leg resolved to the SAME contract (delta targets too close together, or too few strikes with delta data)")
        else:
            width = abs(short_leg["strike"] - long_leg["strike"])
            credit = (short_leg["mid"] or 0) - (long_leg["mid"] or 0)
            ratio = credit / width if width else None
            print(f"Width: {width}, Credit: {credit:.2f}, Credit/Width ratio: {ratio:.3f}" if ratio else "Width is 0")
            print(f"min_credit_to_width_ratio threshold: {ccfg['min_credit_to_width_ratio']}")
            if ratio is not None:
                print("PASS" if ratio >= ccfg['min_credit_to_width_ratio'] else "FAIL (credit too small relative to width)")
    else:
        print("FAIL: could not find both legs (missing delta data on enough contracts)")