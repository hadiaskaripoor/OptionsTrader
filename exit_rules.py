"""
exit_rules.py
Defines when a simulated open position should be closed. Kept separate
from strategies.py (entry logic) so entry and exit rules can be tuned
independently.

All three strategies get a profit target, a stop loss, and a
time-based exit (close as expiration approaches regardless of P&L,
since theta/gamma risk accelerates near expiry).
"""

import config


EXIT_RULES = {
    "long_option": {
        "profit_target_multiple": 1.5,   # close at 150% of entry premium (50% gain)
        "stop_loss_multiple": 0.5,       # close at 50% of entry premium (50% loss)
        "close_at_dte": 5,                # force close with 5 days to expiry left
    },
    "covered_call": {
        "profit_target_multiple": 0.5,   # close (buy back) once option decays to 50% of credit received
        "stop_loss_multiple": None,      # no stop loss -- worst case is assignment, which is acceptable by design
        "close_at_dte": 2,
    },
    "credit_spread": {
        "profit_target_multiple": 0.5,   # close once spread value decays to 50% of credit received (buy back)
        "stop_loss_multiple": 2.0,       # close if spread value grows to 2x credit received (defined-risk stop)
        "close_at_dte": 7,
    },
}


def should_close(strategy, entry_price, current_price, days_to_expiry):
    """
    Returns (should_close: bool, reason: str) for a simulated open position.

    entry_price / current_price meaning depends on direction:
      - long_option: price of the single long contract
      - covered_call / credit_spread: price to buy back the position
        (i.e. current cost to close a position you're short)
    """
    rules = EXIT_RULES.get(strategy)
    if not rules:
        return False, ""

    if days_to_expiry is not None and days_to_expiry <= rules["close_at_dte"]:
        return True, f"time exit: {days_to_expiry}d to expiry (threshold {rules['close_at_dte']}d)"

    if entry_price is None or current_price is None or entry_price == 0:
        return False, ""

    if strategy == "long_option":
        # bought the option; gain if current > entry
        if rules["profit_target_multiple"] and current_price >= entry_price * rules["profit_target_multiple"]:
            return True, f"profit target: {current_price:.2f} >= {rules['profit_target_multiple']}x entry ({entry_price:.2f})"
        if rules["stop_loss_multiple"] and current_price <= entry_price * rules["stop_loss_multiple"]:
            return True, f"stop loss: {current_price:.2f} <= {rules['stop_loss_multiple']}x entry ({entry_price:.2f})"
    else:
        # sold premium (covered_call / credit_spread); gain if current < entry (cheaper to buy back)
        if rules["profit_target_multiple"] and current_price <= entry_price * rules["profit_target_multiple"]:
            return True, f"profit target: {current_price:.2f} <= {rules['profit_target_multiple']}x entry ({entry_price:.2f})"
        if rules["stop_loss_multiple"] and current_price >= entry_price * rules["stop_loss_multiple"]:
            return True, f"stop loss: {current_price:.2f} >= {rules['stop_loss_multiple']}x entry ({entry_price:.2f})"

    return False, ""
