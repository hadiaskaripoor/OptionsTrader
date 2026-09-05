# Options Screener + Paper Trading Logger

A rules-based (not black-box ML) system for scanning large-cap tech options,
tagging signals across three strategies, logging everything to a local
database, and viewing results in a browser dashboard.

## Read this first

- **This runs against Alpaca's paper trading API by default.** Do not point
  it at a live account until you've reviewed at least several weeks of
  logged paper results.
- **"Cheap" and "rich" are defined by IV Rank, not by option price.** A
  $0.30 option can be a terrible buy; a $10 option can be a good one. See
  `iv_analysis.py`.
- **The IV Rank calculation uses a realized-volatility proxy**, not a true
  historical IV series, because Alpaca's options data API gives current
  snapshots rather than a year of daily IV history. This is clearly logged
  as an approximation. For more accurate IV Rank, a historical options data
  vendor (ORATS, CBOE DataShop, etc.) would be a worthwhile upgrade later.
- **No orders are placed silently.** `main.py --confirm` prompts you
  per-signal before touching the paper account. `PLACE_ORDERS_WITHOUT_PROMPT`
  in `main.py` is off by default and should stay off until you trust the
  system.
- **I (Claude) do not have persistent memory across conversations.** This
  local database (`data/trades.db`) is the actual memory. Bring the exported
  JSON or a summary of results back to a future conversation with me and
  we'll refine `config.py` / `strategies.py` together based on what
  happened.

## Setup

```bash
cd options_trader
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt

export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
# ALPACA_BASE_URL defaults to the paper endpoint already
```

Get paper trading API keys from https://app.alpaca.markets (free paper
account). Confirm your account has **options trading enabled** at whatever
approval level (Level 1/2/3) matches these strategies — covered calls and
long options are typically Level 1/2; credit spreads need Level 2+.

## Daily usage

### Manual mode (you're at the computer)
```bash
# Scan the universe, log every signal, print reasoning for each
python main.py --scan

# Same, but prompt to place a paper order for each signal
python main.py --scan --confirm

# Print aggregate performance stats from everything logged so far
python main.py --summary

# Refresh the dashboard's data file
python export_dashboard_data.py
```

### Daemon mode (unattended, run before you leave for work)
```bash
python main.py --daemon
```

Run this once in the morning. It will:
1. Wait until `config.DAEMON['start_time']` (default 09:35) if started early
2. Scan the universe every `scan_interval_minutes` (default 30 min)
3. Auto-open qualifying signals as **simulated** paper trades (logged
   locally to `data/trades.db` -- see the important caveat below)
4. Reconcile open trades against exit rules each pass (`exit_rules.py`:
   profit targets, stop losses, time-based exits near expiry)
5. Automatically stop at `config.DAEMON['stop_time']` (default 15:55),
   or immediately if you press Ctrl+C
6. Run the daily adaptation step (`adapt.py`) -- nudges each strategy's
   key threshold stricter or looser based on that strategy's win rate,
   only once enough trades have closed to be meaningful. Every
   adjustment is logged with its reasoning to `logs/adaptation_log.jsonl`
   and stored in `data/adaptive_overrides.json` (delete that file any
   time to reset to `config.py`'s base values)
7. Export fresh dashboard data automatically

**Important: keep the laptop on and awake.** Disable sleep/hibernate in
Windows power settings for the day, or the loop will pause. Closing the
lid will also pause it unless you've disabled "sleep on lid close."

**Important caveat on daemon mode's trades:** they are simulated using
real market prices, but are NOT routed through Alpaca's actual paper
order book -- your Alpaca dashboard balance won't move. This was a
deliberate choice: real multi-leg option order submission needs to be
tested live against Alpaca's API during market hours, which isn't
something to ship untested and unattended. Once the simulated loop has
proven itself over a couple of weeks, real order routing is a natural
next step.

When you're back in the evening, just check the terminal output or run:
```bash
python main.py --summary
```
and open `dashboard.html` with the freshly exported data.

Then open `dashboard.html` directly in a browser (double-click it, or
`open dashboard.html` on Mac) and load `data/dashboard_data.json` via the
file picker at the top.

## Project structure

```
config.py                  # universe, thresholds, risk limits -- edit this first
data_fetcher.py             # Alpaca API wrapper (stock bars, option chains, positions)
iv_analysis.py               # IV Rank / IV Percentile calculation
strategies.py                # signal generation for all 3 strategies
trade_logger.py              # SQLite logging (signals + trades)
main.py                      # daily driver script
export_dashboard_data.py     # dumps DB to JSON for the dashboard
dashboard.html                # standalone browser dashboard, no server needed
data/trades.db                # created on first run
data/dashboard_data.json      # created by export_dashboard_data.py
```

## The three strategies, tagged separately

1. **`long_option`** — buy calls/puts as a stock substitute. Only fires when
   IV Rank is low (cheap) and a simple trend filter agrees with direction.
   Theta works against you here; this is the riskiest of the three for
   consistent daily income.
2. **`covered_call`** — sell OTM calls against shares you already hold
   (requires you to actually hold 100+ shares; checked via your Alpaca
   positions). Only fires when IV Rank is high enough that the premium is
   worth capping upside for.
3. **`credit_spread`** — sell a defined-risk vertical spread when IV Rank is
   high. Risk is capped by the long leg, unlike naked short options.

## Suggested next steps once you have paper data

- After 2–4 weeks of logged signals/trades, bring `dashboard_data.json` (or
  a summary) back to a conversation with Claude and go through: which
  strategy is actually working, which thresholds in `config.py` need
  tightening, whether the trend filter is helping or just adding noise.
- Consider replacing the realized-vol IV Rank proxy with real historical IV
  data if you want more precision on the "cheap/rich" classification.
- Backtest before scaling size — this system as shipped only forward-tests
  via paper trading, it doesn't replay history.
