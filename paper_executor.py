"""
paper_executor.py
Opens and closes SIMULATED paper positions in the local trade log, using
real market prices pulled from Alpaca's data API. This does NOT submit
orders to Alpaca's order book -- your Alpaca paper account balance will
not reflect these trades. See README for why, and how to move to real
order submission later.

Risk limits from config.RISK are enforced here before any position is
opened, using the local trade log as the source of truth for what's
currently "open".
"""

from datetime import date, datetime, timezone

import config
import data_fetcher
import exit_rules
import trade_logger


def _entry_price_and_desc(signal):
    """Extract a single representative entry price + human-readable
    contract description from a signal, handling single-leg and
    spread signals differently."""
    strat = signal["strategy"]
    if strat == "long_option":
        return signal["est_premium"], signal["contract"]
    elif strat == "covered_call":
        return signal["est_premium"], signal["contract"]
    elif strat == "credit_spread":
        return signal["est_credit"], f"{signal['short_leg']} / {signal['long_leg']}"
    return None, None


def _action_for_signal(signal):
    return signal["action"]


def count_open_positions(symbol=None):
    open_trades = trade_logger.get_open_trades()
    if symbol:
        open_trades = [t for t in open_trades if t["symbol"] == symbol]
    return len(open_trades)


def can_open_new_position(symbol, est_risk_dollars, account_equity):
    """Checks config.RISK limits before allowing a new position."""
    risk = config.RISK
    open_total = count_open_positions()
    open_for_symbol = count_open_positions(symbol)

    if open_total >= risk["max_open_positions"]:
        return False, f"max_open_positions limit reached ({open_total}/{risk['max_open_positions']})"
    if open_for_symbol >= risk["max_positions_per_underlying"]:
        return False, f"max_positions_per_underlying limit reached for {symbol} ({open_for_symbol}/{risk['max_positions_per_underlying']})"

    max_risk_dollars = account_equity * risk["max_risk_per_trade_pct_of_account"]
    if est_risk_dollars > max_risk_dollars:
        return False, f"est. risk ${est_risk_dollars:.2f} exceeds per-trade cap ${max_risk_dollars:.2f} ({risk['max_risk_per_trade_pct_of_account']:.0%} of account)"

    return True, ""


def _estimate_risk_dollars(signal):
    """Rough worst-case dollar risk for one contract/spread, used for position sizing checks."""
    strat = signal["strategy"]
    if strat == "long_option":
        return signal["est_premium"] * 100  # max loss = premium paid
    elif strat == "covered_call":
        return 0  # risk is on the underlying shares already held, not the option itself
    elif strat == "credit_spread":
        return (signal["width"] - signal["est_credit"]) * 100  # max loss on a defined-risk spread
    return 0


def open_signal_as_trade(signal, account_equity):
    """
    Attempt to open a simulated paper trade from a signal, subject to
    risk limits. Returns (trade_id or None, message).
    """
    entry_price, contract_desc = _entry_price_and_desc(signal)
    if entry_price is None:
        return None, "signal missing a usable entry price"

    est_risk = _estimate_risk_dollars(signal)
    ok, reason = can_open_new_position(signal["symbol"], est_risk, account_equity)
    if not ok:
        return None, f"skipped: {reason}"

    trade_id = trade_logger.open_trade(
        signal_id=signal.get("_signal_id"),
        strategy=signal["strategy"],
        symbol=signal["symbol"],
        contract_desc=contract_desc,
        action=signal["action"],
        entry_price=entry_price,
        quantity=1,
        notes=f"auto-opened by daemon; est_risk=${est_risk:.2f}",
    )
    return trade_id, f"opened trade {trade_id} ({signal['strategy']} {signal['symbol']}), est. risk ${est_risk:.2f}"


def _current_price_for_open_trade(trade):
    """
    Re-fetch the current price to close an open trade. For spreads this
    re-derives both legs' current mid prices from a fresh chain pull
    (occ symbols are parsed back out of contract_desc).
    Returns (current_price, days_to_expiry) or (None, None) if unavailable.
    """
    strategy = trade["strategy"]
    symbol = trade["symbol"]

    try:
        # Wide DTE window since we don't know remaining days a priori
        contracts = data_fetcher.get_option_chain(symbol, min_dte=0, max_dte=60)
    except Exception:
        return None, None

    by_symbol = {c["occ_symbol"]: c for c in contracts}

    if strategy in ("long_option", "covered_call"):
        occ = trade["contract_desc"]
        c = by_symbol.get(occ)
        if c is None or c.get("mid") is None:
            return None, None
        dte = (c["expiry"] - date.today()).days if c.get("expiry") else None
        return c["mid"], dte

    elif strategy == "credit_spread":
        try:
            short_occ, long_occ = [s.strip() for s in trade["contract_desc"].split("/")]
        except ValueError:
            return None, None
        short_c = by_symbol.get(short_occ)
        long_c = by_symbol.get(long_occ)
        if short_c is None or long_c is None or short_c.get("mid") is None or long_c.get("mid") is None:
            return None, None
        current_cost_to_close = short_c["mid"] - long_c["mid"]  # cost to buy back the spread
        dte = (short_c["expiry"] - date.today()).days if short_c.get("expiry") else None
        return current_cost_to_close, dte

    return None, None


def reconcile_open_trades():
    """
    Check every open trade against current market prices and exit rules.
    Closes any that qualify. Returns a list of (trade_id, pnl, reason) for
    trades closed this pass.
    """
    closed = []
    for trade in trade_logger.get_open_trades():
        current_price, dte = _current_price_for_open_trade(trade)
        if current_price is None:
            continue  # couldn't price it this pass, leave open, try again next reconcile

        should, reason = exit_rules.should_close(
            trade["strategy"], trade["entry_price"], current_price, dte
        )
        if should:
            pnl = trade_logger.close_trade(trade["id"], current_price, notes=reason)
            closed.append((trade["id"], pnl, reason))

    return closed
