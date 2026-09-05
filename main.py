"""
main.py
Daily driver script. Run this once per session (e.g. each morning):

    python main.py --scan            # scan universe, log signals, print them
    python main.py --scan --confirm  # also place paper orders for signals you approve

This does NOT auto-trade silently. By design, every signal is printed and
logged; live paper orders only go out with --confirm, and even then you
get a per-signal y/n prompt. Flip that off yourself once you trust it --
see PLACE_ORDERS_WITHOUT_PROMPT below, off by default on purpose.
"""

import argparse
import json
import time
from datetime import datetime

import config
import data_fetcher
import strategies
import trade_logger
import paper_executor
import adapt
import export_dashboard_data
import universe

PLACE_ORDERS_WITHOUT_PROMPT = False  # keep False until you've reviewed weeks of paper logs


def get_shares_held(symbol):
    """Look up how many shares of `symbol` are currently held in the paper account."""
    try:
        positions = data_fetcher.get_positions()
        for p in positions:
            if p.symbol == symbol:
                return int(float(p.qty))
    except Exception as e:
        print(f"[warn] could not fetch positions for {symbol}: {e}")
    return 0


def run_scan(confirm=False):
    all_signals = []
    symbols = universe.get_universe()
    print(f"Scanning {len(symbols)} symbols: {symbols}\n")

    for symbol in symbols:
        print(f"\n=== Scanning {symbol} ===")
        try:
            shares_held = get_shares_held(symbol)
            results = strategies.scan_all(symbol, shares_held=shares_held)
        except Exception as e:
            print(f"[error] scan failed for {symbol}: {e}")
            continue

        for strat_name, signals in results.items():
            for sig in signals:
                signal_id = trade_logger.log_signal(sig)
                sig["_signal_id"] = signal_id
                all_signals.append(sig)
                print(f"[{strat_name}] {json.dumps(sig, indent=2, default=str)}")

                if confirm:
                    _maybe_place_order(sig)

    if not all_signals:
        print("\nNo signals fired today. That is a valid and common outcome -- "
              "the rules are intentionally selective (low IV rank for buying, "
              "high IV rank for selling). Forcing trades when nothing qualifies "
              "is how accounts bleed out on theta and bad entries.")

    return all_signals


def _maybe_place_order(sig):
    if not PLACE_ORDERS_WITHOUT_PROMPT:
        resp = input(f"Place PAPER order for {sig['symbol']} ({sig['strategy']})? [y/N] ")
        if resp.strip().lower() != "y":
            print("Skipped.")
            return

    # NOTE: actual order placement via alpaca-py's TradingClient.submit_order
    # with an OptionLegRequest is intentionally left as a manual wiring step.
    # Reasons: (1) option order payloads vary by strategy (single leg vs
    # multi-leg spread), (2) you should read Alpaca's options trading docs
    # for your account's approved options level before this goes live even
    # in paper form, (3) this keeps a human decision point in the loop.
    print(f"[TODO] Wire up order submission for signal {sig.get('_signal_id')} here.")
    print("See: https://docs.alpaca.markets/docs/options-trading")


def print_summary():
    stats = trade_logger.summary_stats()
    print(json.dumps(stats, indent=2))


def _parse_hhmm(s):
    h, m = map(int, s.split(":"))
    return h, m


def _within_daemon_window():
    now = datetime.now()
    cfg = config.DAEMON
    if cfg["weekdays_only"] and now.weekday() >= 5:
        return False
    start_h, start_m = _parse_hhmm(cfg["start_time"])
    stop_h, stop_m = _parse_hhmm(cfg["stop_time"])
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    stop = now.replace(hour=stop_h, minute=stop_m, second=0, microsecond=0)
    return start <= now <= stop


def run_daemon():
    """
    Unattended loop: scan, auto-open qualifying signals as SIMULATED
    paper trades (see paper_executor.py -- these are logged locally,
    NOT routed through Alpaca's real paper order book), reconcile
    existing open trades against exit rules, sleep, repeat. Stops
    automatically at config.DAEMON['stop_time'], or immediately on
    Ctrl+C. Either way, runs the daily wrap-up (adaptation + dashboard
    export) before exiting.

    Run this once in the morning before you leave. Leave the laptop on
    and awake (disable sleep in power settings) -- it needs to keep
    running unattended all day.
    """
    print(f"=== Daemon starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Window: {config.DAEMON['start_time']} - {config.DAEMON['stop_time']} "
          f"(weekdays only: {config.DAEMON['weekdays_only']})")
    print(f"Scan interval: {config.DAEMON['scan_interval_minutes']} min")
    print("Trades are SIMULATED (local log only, not real Alpaca paper orders). "
          "Press Ctrl+C to stop early.\n")

    try:
        account = data_fetcher.get_account()
        account_equity = float(account.equity)
    except Exception as e:
        print(f"[warn] could not fetch account equity, defaulting to $100,000: {e}")
        account_equity = 100000.0

    try:
        while True:
            if not _within_daemon_window():
                now = datetime.now()
                if config.DAEMON["weekdays_only"] and now.weekday() >= 5:
                    print(f"[{now.strftime('%H:%M:%S')}] Weekend -- daemon will not run. Exiting.")
                    break
                stop_h, stop_m = _parse_hhmm(config.DAEMON["stop_time"])
                if now.hour > stop_h or (now.hour == stop_h and now.minute >= stop_m):
                    print(f"[{now.strftime('%H:%M:%S')}] Past stop time. Wrapping up.")
                    break
                print(f"[{now.strftime('%H:%M:%S')}] Before start time, waiting...")
                time.sleep(60)
                continue

            now = datetime.now()
            print(f"\n--- Scan pass at {now.strftime('%H:%M:%S')} ---")

            symbols = universe.get_universe()
            for symbol in symbols:
                try:
                    shares_held = get_shares_held(symbol)
                    results = strategies.scan_all(symbol, shares_held=shares_held)
                except Exception as e:
                    print(f"[error] scan failed for {symbol}: {e}")
                    continue

                for strat_name, signals in results.items():
                    for sig in signals:
                        signal_id = trade_logger.log_signal(sig)
                        sig["_signal_id"] = signal_id
                        trade_id, msg = paper_executor.open_signal_as_trade(sig, account_equity)
                        print(f"[{symbol}][{strat_name}] {msg}")

            closed = paper_executor.reconcile_open_trades()
            for trade_id, pnl, reason in closed:
                print(f"[closed] trade {trade_id}: P&L ${pnl:.2f} ({reason})")

            if not _within_daemon_window():
                print("Past stop time after this pass. Wrapping up.")
                break

            print(f"Sleeping {config.DAEMON['scan_interval_minutes']} min...")
            time.sleep(config.DAEMON["scan_interval_minutes"] * 60)

    except KeyboardInterrupt:
        print("\n[stopped manually]")

    print("\n=== Daily wrap-up ===")
    closed = paper_executor.reconcile_open_trades()
    for trade_id, pnl, reason in closed:
        print(f"[closed] trade {trade_id}: P&L ${pnl:.2f} ({reason})")

    if config.DAEMON.get("force_close_daily", False):
        forced = paper_executor.force_close_all_open_trades()
        for trade_id, pnl, reason in forced:
            print(f"[closed] trade {trade_id}: P&L ${pnl:.2f} ({reason})")

    changes = adapt.run_adaptation()
    print("\nAdaptation results:")
    for c in changes:
        print(f"  [{c['strategy']}] {c['action']}: {c['reason']}")

    export_dashboard_data.main()
    print_summary()
    print(f"\n=== Daemon stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true", help="Scan universe and log signals")
    parser.add_argument("--confirm", action="store_true", help="Prompt to place paper orders per signal")
    parser.add_argument("--summary", action="store_true", help="Print performance summary from the trade log")
    parser.add_argument("--daemon", action="store_true",
                         help="Run unattended: auto-scan, auto-open simulated trades, reconcile, "
                              "adapt thresholds, all within config.DAEMON's time window")
    parser.add_argument("--build-universe", action="store_true",
                         help="Rebuild the dynamic liquidity-screened universe (slow, run manually "
                              "every few days, not part of the daily scan/daemon flow)")
    args = parser.parse_args()

    if args.build_universe:
        universe.build_dynamic_universe(force_refresh=True)
    elif args.daemon:
        run_daemon()
    elif args.scan:
        run_scan(confirm=args.confirm)
    if args.summary:
        print_summary()
    if not (args.scan or args.summary or args.daemon or args.build_universe):
        parser.print_help()
