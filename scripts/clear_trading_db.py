#!/usr/bin/env python3
"""
Clear main trading database data for a fresh session.

This clears data from the primary DB used by both:
- Live trading mode
- `python cli.py run --paper`

By default it targets `trading_system.db` in the project root.

Usage:
    python scripts/clear_trading_db.py
    python scripts/clear_trading_db.py --yes
    python scripts/clear_trading_db.py --db ./trading_system.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


TABLES_TO_CLEAR = [
    "positions",
    "trade_logs",
    "markets",
    "market_analyses",
    "daily_cost_tracking",
    "llm_queries",
    "analysis_reports",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_db_path() -> Path:
    return _project_root() / "trading_system.db"


def clear_db(db_path: Path) -> None:
    if not db_path.exists():
        print(f"No DB found at: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()

        # Prevent foreign-key constraint ordering issues during cleanup.
        cur.execute("PRAGMA foreign_keys = OFF")

        deleted_summary: list[tuple[str, int]] = []

        for table in TABLES_TO_CLEAR:
            # Count rows first so we can report what was cleared.
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            exists = cur.fetchone() is not None
            if not exists:
                deleted_summary.append((table, -1))
                continue

            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = int(cur.fetchone()[0])
            cur.execute(f"DELETE FROM {table}")
            deleted_summary.append((table, row_count))

        # Reset autoincrement counters if present.
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        )
        if cur.fetchone() is not None:
            cur.execute("DELETE FROM sqlite_sequence")

        conn.commit()

        print("\nDatabase cleared successfully.")
        print(f"DB: {db_path}")
        print("\nRows removed:")
        for table, count in deleted_summary:
            if count == -1:
                print(f"  - {table}: table not found (skipped)")
            else:
                print(f"  - {table}: {count}")

    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear main trading database data")
    parser.add_argument(
        "--db",
        default=str(_default_db_path()),
        help="Path to database file (default: ./trading_system.db)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()

    if not args.yes:
        print("This will permanently clear trading history and positions from:")
        print(f"  {db_path}")
        confirm = input("Type 'CLEAR' to continue: ").strip()
        if confirm != "CLEAR":
            print("Aborted. No changes made.")
            return 1

    clear_db(db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
