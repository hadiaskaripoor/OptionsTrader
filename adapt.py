"""
adapt.py
Day-by-day "learning" step. This is NOT a trained ML model -- it's a
transparent, bounded rule: look at closed trade performance per
strategy, and nudge that strategy's key threshold slightly stricter or
looser depending on win rate. Every adjustment is logged with the
reasoning, and adjustments only apply once enough trades have closed to
mean something (avoids overfitting to a handful of trades).

Adjustments are stored in adaptive_overrides.json rather than editing
config.py directly, so your hand-set base config is never silently
rewritten -- you can always inspect, reset, or hand-edit the overrides
file. Delete it to reset to config.py's base values.
"""

import json
import os
from datetime import datetime, timezone

import config
import trade_logger

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "data", "adaptive_overrides.json")
MIN_CLOSED_TRADES_TO_ADAPT = 5   # don't adjust based on fewer than this many closed trades
STEP = 0.05                      # how much to move a threshold per adaptation pass

# For each strategy, which config key gets adjusted, and which direction
# counts as "stricter" (i.e. what to do when win rate is poor).
ADAPT_TARGETS = {
    "long_option": {"key": "max_iv_rank", "bounds": (0.10, 0.50), "stricter_is_lower": True},
    "covered_call": {"key": "min_iv_rank", "bounds": (0.30, 0.80), "stricter_is_lower": False},
    "credit_spread": {"key": "min_credit_to_width_ratio", "bounds": (0.10, 0.35), "stricter_is_lower": False},
}

CONFIG_DICT_FOR_STRATEGY = {
    "long_option": "LONG_OPTION",
    "covered_call": "COVERED_CALL",
    "credit_spread": "CREDIT_SPREAD",
}


def load_overrides():
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    with open(OVERRIDES_PATH) as f:
        return json.load(f)


def _save_overrides(overrides):
    os.makedirs(os.path.dirname(OVERRIDES_PATH), exist_ok=True)
    with open(OVERRIDES_PATH, "w") as f:
        json.dump(overrides, f, indent=2)


def run_adaptation():
    """
    Analyze closed trades per strategy and adjust thresholds within
    bounds. Returns a list of change-log dicts describing what happened
    (and why), whether or not any changes were made.
    """
    stats = trade_logger.summary_stats()
    overrides = load_overrides()
    changes = []

    for strategy, target in ADAPT_TARGETS.items():
        strat_stats = stats["by_strategy"].get(strategy)
        if not strat_stats or strat_stats["count"] < MIN_CLOSED_TRADES_TO_ADAPT:
            changes.append({
                "strategy": strategy,
                "action": "no_change",
                "reason": f"only {strat_stats['count'] if strat_stats else 0} closed trades, "
                          f"need {MIN_CLOSED_TRADES_TO_ADAPT} before adapting",
            })
            continue

        win_rate = strat_stats["wins"] / strat_stats["count"]
        key = target["key"]
        lo, hi = target["bounds"]
        base_dict = getattr(config, CONFIG_DICT_FOR_STRATEGY[strategy])
        current_value = overrides.get(strategy, {}).get(key, base_dict[key])

        if win_rate < 0.40:
            # performing poorly -> make entry criteria stricter (more selective)
            direction = -1 if target["stricter_is_lower"] else 1
            new_value = round(min(hi, max(lo, current_value + direction * STEP)), 3)
            reason = f"win rate {win_rate:.0%} over {strat_stats['count']} trades is poor -> tightening {key}"
        elif win_rate > 0.65:
            # performing well -> loosen slightly to allow more trade frequency
            direction = 1 if target["stricter_is_lower"] else -1
            new_value = round(min(hi, max(lo, current_value + direction * STEP)), 3)
            reason = f"win rate {win_rate:.0%} over {strat_stats['count']} trades is strong -> loosening {key} slightly"
        else:
            new_value = current_value
            reason = f"win rate {win_rate:.0%} over {strat_stats['count']} trades is in the neutral zone -> no change"

        if new_value != current_value:
            overrides.setdefault(strategy, {})[key] = new_value
            changes.append({
                "strategy": strategy, "action": "adjusted", "key": key,
                "old_value": current_value, "new_value": new_value, "reason": reason,
            })
        else:
            changes.append({"strategy": strategy, "action": "no_change", "reason": reason})

    _save_overrides(overrides)

    log_path = os.path.join(os.path.dirname(__file__), "logs", "adaptation_log.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "changes": changes}) + "\n")

    return changes
