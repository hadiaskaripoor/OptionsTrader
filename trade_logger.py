"""
trade_logger.py
SQLite-backed logging for every signal generated and every paper trade
taken. This is the "memory" of the system across sessions -- since I
(Claude) don't retain state between conversations, this log is what lets
a future session pick up where the last one left off: you show me the
exported data and we refine config.py / strategies.py together.
"""

import sqlite3
import json
from datetime import datetime, timezone

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_json TEXT NOT NULL,
    taken INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    ts_opened TEXT NOT NULL,
    ts_closed TEXT,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_desc TEXT NOT NULL,
    action TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER DEFAULT 1,
    status TEXT DEFAULT 'open',
    pnl REAL,
    notes TEXT,
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);
"""


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def log_signal(signal: dict):
    """Store a generated signal, whether or not it gets acted on."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO signals (ts, strategy, symbol, signal_json) VALUES (?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            signal.get("strategy"),
            signal.get("symbol"),
            json.dumps(signal),
        ),
    )
    conn.commit()
    signal_id = cur.lastrowid
    conn.close()
    return signal_id


def open_trade(signal_id, strategy, symbol, contract_desc, action, entry_price, quantity=1, notes=""):
    """Record that a paper trade was opened based on a signal."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trades
           (signal_id, ts_opened, strategy, symbol, contract_desc, action,
            entry_price, quantity, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (signal_id, datetime.now(timezone.utc).isoformat(), strategy, symbol,
         contract_desc, action, entry_price, quantity, notes),
    )
    conn.commit()
    cur.execute("UPDATE signals SET taken = 1 WHERE id = ?", (signal_id,))
    conn.commit()
    trade_id = cur.lastrowid
    conn.close()
    return trade_id


def close_trade(trade_id, exit_price, notes=""):
    """Record that a trade was closed and compute realized P&L."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT entry_price, quantity, action FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No trade found with id {trade_id}")

    entry_price, quantity, action = row
    # For BUY_TO_OPEN positions, P&L = (exit - entry) * qty * 100
    # For SELL_TO_OPEN (credit) positions, P&L = (entry - exit) * qty * 100
    multiplier = 100
    if action in ("SELL_TO_OPEN", "SELL_TO_OPEN_SPREAD"):
        pnl = (entry_price - exit_price) * quantity * multiplier
    else:
        pnl = (exit_price - entry_price) * quantity * multiplier

    cur.execute(
        """UPDATE trades SET ts_closed = ?, exit_price = ?, status = 'closed',
           pnl = ?, notes = notes || ? WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), exit_price, pnl, f" | close: {notes}", trade_id),
    )
    conn.commit()
    conn.close()
    return pnl


def get_open_trades():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE status = 'open'")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def get_all_trades():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades ORDER BY ts_opened DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_signals(limit=100):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def summary_stats():
    """Basic performance stats used by the dashboard."""
    trades = get_all_trades()
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    losses = [t for t in closed if (t["pnl"] or 0) <= 0]

    total_pnl = sum(t["pnl"] or 0 for t in closed)
    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0

    by_strategy = {}
    for t in closed:
        s = t["strategy"]
        by_strategy.setdefault(s, {"count": 0, "pnl": 0.0, "wins": 0})
        by_strategy[s]["count"] += 1
        by_strategy[s]["pnl"] += t["pnl"] or 0
        if (t["pnl"] or 0) > 0:
            by_strategy[s]["wins"] += 1

    return {
        "total_closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "by_strategy": by_strategy,
    }
