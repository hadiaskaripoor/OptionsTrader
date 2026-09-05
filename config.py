"""
config.py
Central configuration for the options screener/paper-trading system.
Edit this file to change universe, thresholds, and strategy parameters.
"""

import os

# ---------------------------------------------------------------------------
# ALPACA API CREDENTIALS
# Set these as environment variables, never hardcode keys in this file.
#   export ALPACA_API_KEY="your_key"
#   export ALPACA_SECRET_KEY="your_secret"
# Paper trading base URL is used by default. Do NOT point this at live
# trading until you have reviewed weeks of paper-trading logs.
# ---------------------------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "PKTSLVIGRYPGLQRIYJTUAHYVNN")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "4RWktzLLRRGzRed6S7GGmxxRdXctwGSjt3P6dvPHdjVu")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")

# ---------------------------------------------------------------------------
# UNIVERSE
# UNIVERSE_MODE = "static"  -> always scan exactly the tickers in WATCHLIST
# UNIVERSE_MODE = "dynamic" -> auto-screen all tradable US equities down to
#                               a liquid subset (see universe.py); WATCHLIST
#                               is used as a fallback until you've run
#                               `python main.py --build-universe` at least once
# ---------------------------------------------------------------------------
UNIVERSE_MODE = "dynamic"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "AMD", "CRM", "ORCL",
]

DYNAMIC_UNIVERSE = {
    "min_price": 15.0,                    # avoid penny stocks with unreliable option pricing
    "min_avg_dollar_volume": 50_000_000,  # $50M/day average $ volume -- proxy for liquid, optionable names
    "lookback_days_for_volume": 20,
    "max_universe_size": 40,              # cap on FINAL scanned universe -- keeps each scan pass fast
    "max_symbols_to_screen": 800,         # cap on how many candidates get liquidity-checked during a build
    "refresh_every_days": 3,              # how often a build is considered "fresh"; rebuild manually via --build-universe
}

# ---------------------------------------------------------------------------
# IV RANK / IV PERCENTILE THRESHOLDS
# IV Rank = (current IV - 52wk low IV) / (52wk high IV - 52wk low IV)
# We use this instead of raw premium to decide "cheap" vs "expensive".
# ---------------------------------------------------------------------------
IV_RANK_LOOKBACK_DAYS = 252          # ~1 trading year
IV_RANK_CHEAP_THRESHOLD = 0.30       # below this -> options considered "cheap" (buy premium)
IV_RANK_EXPENSIVE_THRESHOLD = 0.60   # above this -> options considered "rich" (sell premium)

# ---------------------------------------------------------------------------
# STRATEGY PARAMETERS (all three run in parallel, tagged separately)
# ---------------------------------------------------------------------------

# 1) Long calls / puts (directional, buy premium)
LONG_OPTION = {
    "enabled": True,
    "min_days_to_expiry": 21,
    "max_days_to_expiry": 45,
    "target_delta": 0.35,          # slightly OTM, cheaper than ATM
    "max_iv_rank": IV_RANK_CHEAP_THRESHOLD,   # only buy when IV is cheap
    "max_premium_pct_of_stock": 0.05,          # sanity cap: premium <= 5% of stock price
    "trend_lookback_days": 20,     # simple momentum filter
}

# 2) Covered calls (income on shares you already hold)
COVERED_CALL = {
    "enabled": True,
    "min_days_to_expiry": 14,
    "max_days_to_expiry": 45,
    "target_delta": 0.25,          # OTM call, ~25 delta is a common income target
    "min_iv_rank": 0.40,           # sell premium when IV is rich enough to be worth it
    "requires_shares_held": True,  # only signals for tickers you actually hold 100+ shares of
}

# 3) Credit spreads (defined-risk premium selling)
CREDIT_SPREAD = {
    "enabled": True,
    "min_days_to_expiry": 21,
    "max_days_to_expiry": 45,
    "short_leg_delta": 0.25,
    "long_leg_delta": 0.10,        # further OTM leg that defines/caps risk
    "min_iv_rank": IV_RANK_EXPENSIVE_THRESHOLD,
    "min_credit_to_width_ratio": 0.15,  # only take spreads paying >=15% of the width as credit
}

# ---------------------------------------------------------------------------
# RISK MANAGEMENT (applies across all strategies)
# ---------------------------------------------------------------------------
RISK = {
    "max_risk_per_trade_pct_of_account": 0.02,   # never risk more than 2% of account per trade
    "max_open_positions": 8,
    "max_positions_per_underlying": 2,
    "daily_loss_limit_pct_of_account": 0.05,     # stop new entries for the day if hit
}

# ---------------------------------------------------------------------------
# STORAGE
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trades.db")
DASHBOARD_EXPORT_PATH = os.path.join(os.path.dirname(__file__), "data", "dashboard_data.json")

# ---------------------------------------------------------------------------
# DAEMON MODE (unattended run during work hours)
# ---------------------------------------------------------------------------
DAEMON = {
    "start_time": "23:09",     # local time, wait a few min after open for quotes to stabilize
    "stop_time": "23:19",      # local time, stop before close
    "scan_interval_minutes": 1,
    "weekdays_only": False,
    "force_close_daily": True,  # close every open position before stopping each day -- no overnight risk

}

# ---------------------------------------------------------------------------
# ADAPTIVE OVERRIDES
# Loaded on top of the strategy dicts above, written by adapt.py based on
# closed-trade performance. This file is created automatically -- delete
# data/adaptive_overrides.json at any time to reset to the base values
# defined above.
# ---------------------------------------------------------------------------
def _apply_adaptive_overrides():
    overrides_path = os.path.join(os.path.dirname(__file__), "data", "adaptive_overrides.json")
    if not os.path.exists(overrides_path):
        return
    try:
        import json
        with open(overrides_path) as f:
            overrides = json.load(f)
    except Exception:
        return

    strategy_to_dict = {
        "long_option": LONG_OPTION,
        "covered_call": COVERED_CALL,
        "credit_spread": CREDIT_SPREAD,
    }
    for strategy, kv in overrides.items():
        target_dict = strategy_to_dict.get(strategy)
        if target_dict is None:
            continue
        for key, value in kv.items():
            if key in target_dict:
                target_dict[key] = value


_apply_adaptive_overrides()
