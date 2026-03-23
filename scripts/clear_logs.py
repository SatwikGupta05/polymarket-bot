#!/usr/bin/env python3
"""
Clear log files for a fresh session.

By default it targets the `logs` directory in the project root and removes
all `.log` files inside it.

Usage:
    python scripts/clear_logs.py
    python scripts/clear_logs.py --yes
    python scripts/clear_logs.py --logs-dir ./logs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_logs_dir() -> Path:
    return _project_root() / "logs"


def clear_logs(logs_dir: Path) -> None:
    if not logs_dir.exists():
        print(f"No logs directory found at: {logs_dir}")
        return

    if not logs_dir.is_dir():
        print(f"Path is not a directory: {logs_dir}")
        return

    log_files = sorted(logs_dir.glob("*.log"))
    if not log_files:
        print(f"No .log files found in: {logs_dir}")
        return

    deleted_files: list[tuple[str, int]] = []

    for log_file in log_files:
        size = log_file.stat().st_size
        log_file.unlink()
        deleted_files.append((log_file.name, size))

    print("\nLogs cleared successfully.")
    print(f"Directory: {logs_dir}")
    print("\nFiles removed:")
    for name, size in deleted_files:
        print(f"  - {name}: {size} bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear log files from the logs directory")
    parser.add_argument(
        "--logs-dir",
        default=str(_default_logs_dir()),
        help="Path to logs directory (default: ./logs)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir).resolve()

    if not args.yes:
        print("This will permanently delete all .log files from:")
        print(f"  {logs_dir}")
        confirm = input("Type 'CLEAR' to continue: ").strip()
        if confirm != "CLEAR":
            print("Aborted. No changes made.")
            return 1

    clear_logs(logs_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
