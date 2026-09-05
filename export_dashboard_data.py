"""
export_dashboard_data.py
Dumps the trade log to a JSON file that dashboard.html reads directly
(no server required -- just open dashboard.html in a browser after running
this script). Run it any time you want the dashboard to reflect the
latest data:

    python export_dashboard_data.py
"""

import json
import config
import trade_logger


def main():
    data = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "summary": trade_logger.summary_stats(),
        "open_trades": trade_logger.get_open_trades(),
        "all_trades": trade_logger.get_all_trades(),
        "recent_signals": trade_logger.get_recent_signals(200),
    }
    with open(config.DASHBOARD_EXPORT_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Exported dashboard data to {config.DASHBOARD_EXPORT_PATH}")


if __name__ == "__main__":
    main()
