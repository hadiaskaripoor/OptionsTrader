"""
universe.py
Builds a trading universe dynamically from Alpaca's full list of
tradable US equities, filtered down by price and liquidity, instead of
a hand-maintained fixed ticker list.

Honest constraint: scanning all ~8,000 tradable US equities on every
pass isn't practical -- each symbol costs several API calls (option
chain + historical bars, x3 strategies), and free-tier rate limits will
throttle you well before that finishes. So this module:
  1. Only rebuilds the full universe when explicitly asked to
     (`python main.py --build-universe`), not automatically every run.
  2. Caches the result for `refresh_every_days` (see config.py).
  3. Caps how many symbols get liquidity-checked during a build
     (`max_symbols_to_screen`), to keep build time bounded.
  4. Falls back to config.WATCHLIST if no cached universe exists yet,
     so daily scans/daemon runs never silently hang waiting on a build.

If you want guaranteed full manual control instead, set
config.UNIVERSE_MODE = "static" and edit config.WATCHLIST directly.
"""

import json
import os
import time
from datetime import date

import config
import data_fetcher

CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "universe_cache.json")


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(symbols):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump({"symbols": symbols, "built_at": date.today().isoformat()}, f, indent=2)


def _cache_is_fresh(cache):
    if not cache:
        return False
    built = date.fromisoformat(cache["built_at"])
    return (date.today() - built).days < config.DYNAMIC_UNIVERSE["refresh_every_days"]


def _get_all_tradable_equities():
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)
    req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    assets = client.get_all_assets(req)

    # Pre-filter using attributes Alpaca already gives us for free (no
    # per-symbol API calls needed): major exchanges only, marginable and
    # shortable. This is a cheap proxy for "not a thin OTC/micro-cap
    # name" and shrinks the pool a lot before the expensive per-symbol
    # liquidity check below -- without this, a plain alphabetical slice
    # of the full ~12,900 tradable list is dominated by illiquid names
    # and barely covers past the first couple letters of the alphabet.
    major_exchanges = {"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"}

    def _exchange_str(a):
        exch = getattr(a, "exchange", None)
        if exch is None:
            return ""
        return str(exch).split(".")[-1].upper()

    tradable_alpha = [a for a in assets if a.tradable and a.symbol.isalpha()]
    marginable = [a for a in tradable_alpha if getattr(a, "marginable", False)]
    shortable = [a for a in marginable if getattr(a, "shortable", False)]
    on_major_exchange = [a for a in shortable if _exchange_str(a) in major_exchanges]

    print(f"[universe] filter funnel: {len(assets)} total -> {len(tradable_alpha)} tradable/alpha "
          f"-> {len(marginable)} marginable -> {len(shortable)} shortable -> "
          f"{len(on_major_exchange)} on major exchange")

    return sorted(a.symbol for a in on_major_exchange)



def _rank_by_liquidity(symbols):
    import random

    dcfg = config.DYNAMIC_UNIVERSE
    if len(symbols) > dcfg["max_symbols_to_screen"]:
        rng = random.Random(42)
        capped = rng.sample(symbols, dcfg["max_symbols_to_screen"])
        print(f"[universe] {len(symbols)} candidates after exchange/marginable pre-filter, "
              f"randomly sampling {len(capped)} to screen for liquidity (max_symbols_to_screen cap) "
              f"-- increase this in config.py if you're willing to wait longer.")
    else:
        capped = symbols
        print(f"[universe] {len(symbols)} candidates after exchange/marginable pre-filter, screening all of them.")



    scored = []
    for i, sym in enumerate(capped):
        if i % 100 == 0:
            print(f"[universe] screening {i}/{len(capped)}...")
        try:
            df = data_fetcher.get_daily_bars(sym, lookback_days=dcfg["lookback_days_for_volume"])
            if df.empty:
                continue
            avg_price = df["close"].mean()
            avg_dollar_vol = (df["close"] * df["volume"]).mean()
            if avg_price < dcfg["min_price"]:
                continue
            if avg_dollar_vol < dcfg["min_avg_dollar_volume"]:
                continue
            scored.append((sym, avg_dollar_vol))
        except Exception:
            continue
        time.sleep(0.05)  # be polite to the free-tier rate limit

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[: dcfg["max_universe_size"]]]


def build_dynamic_universe(force_refresh=False):
    """Explicitly (re)build the liquidity-screened universe and cache it.
    This is slow (minutes) by design -- run it manually, not as part of
    every daemon start."""
    cache = _load_cache()
    if not force_refresh and _cache_is_fresh(cache):
        print(f"[universe] cache from {cache['built_at']} is still fresh, nothing to do "
              f"(pass force_refresh=True to rebuild anyway)")
        return cache["symbols"]

    print("[universe] building fresh universe -- this scans many tickers and can take several minutes...")
    try:
        all_symbols = _get_all_tradable_equities()
    except Exception as e:
        print(f"[universe] could not fetch asset list ({e}), falling back to WATCHLIST")
        return config.WATCHLIST

    ranked = _rank_by_liquidity(all_symbols)
    if not ranked:
        print("[universe] liquidity screen returned nothing, falling back to WATCHLIST")
        return config.WATCHLIST

    _save_cache(ranked)
    print(f"[universe] done -- built universe of {len(ranked)} symbols: {ranked}")
    return ranked


def get_universe():
    """
    Fast path used by every scan/daemon run. Never triggers a slow
    rebuild itself -- uses the cache (even if stale) if one exists,
    otherwise falls back to config.WATCHLIST.
    """
    if config.UNIVERSE_MODE == "static":
        return config.WATCHLIST

    cache = _load_cache()
    if cache is None:
        print("[universe] no dynamic universe built yet -- using WATCHLIST as fallback. "
              "Run `python main.py --build-universe` (takes a few minutes) to enable a "
              "broader, liquidity-screened universe.")
        return config.WATCHLIST

    if not _cache_is_fresh(cache):
        print(f"[universe] cached universe from {cache['built_at']} is older than "
              f"{config.DYNAMIC_UNIVERSE['refresh_every_days']}d but still usable. "
              f"Run `python main.py --build-universe` to refresh when convenient.")

    return cache["symbols"]
