"""
debug_chain.py
Quick diagnostic: fetch the raw option chain for one symbol and print
what actually came back -- contract count, and whether IV/delta fields
are populated. Run this before trusting a "no signals" result from
main.py, since an empty/incomplete chain will also silently produce
zero signals.

Usage:
    python debug_chain.py AAPL
"""

import sys
import data_fetcher

symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

print(f"Fetching option chain for {symbol} (21-45 DTE window)...")
contracts = data_fetcher.get_option_chain(symbol, min_dte=21, max_dte=45)

print(f"\nTotal contracts returned: {len(contracts)}")

if not contracts:
    print("EMPTY CHAIN. This is the likely reason no signals fired -- "
          "the strategies have nothing to evaluate. Possible causes: "
          "wrong DTE window for current expirations, or the 'indicative' "
          "feed not returning data for this account tier.")
    sys.exit(0)

have_iv = [c for c in contracts if c.get("iv") is not None]
have_delta = [c for c in contracts if c.get("delta") is not None]
have_quote = [c for c in contracts if c.get("mid") is not None]

print(f"Contracts with IV populated:    {len(have_iv)} / {len(contracts)}")
print(f"Contracts with delta populated: {len(have_delta)} / {len(contracts)}")
print(f"Contracts with bid/ask (mid):   {len(have_quote)} / {len(contracts)}")

print("\nSample of first 5 contracts:")
for c in contracts[:5]:
    print(c)

if not have_delta:
    print("\nNO DELTA DATA. The strategies rely on delta to pick strikes "
          "(_closest_by_delta) -- with no delta values, every strategy "
          "will silently return zero signals regardless of IV/trend. "
          "This is a common limitation of free-tier / indicative options "
          "feeds, which often omit greeks. If this is the case, we'd need "
          "to either compute delta ourselves (Black-Scholes from strike/"
          "expiry/IV/underlying price) or upgrade the data feed.")
