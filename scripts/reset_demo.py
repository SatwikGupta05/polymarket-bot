"""
Demo Reset Script
=================
Run this before recording your demo video.
Clears the cooldown/analysis history so the bot processes fresh markets.

Usage:
    python scripts/reset_demo.py
"""
import sqlite3
import os

DB = "trading_system.db"

if not os.path.exists(DB):
    print("No database found — nothing to reset.")
else:
    conn = sqlite3.connect(DB)
    # Clear analysis history (causes cooldown skips)
    try:
        conn.execute("DELETE FROM market_analyses")
        print(f"[OK] Cleared market_analyses ({conn.execute('SELECT changes()').fetchone()[0]} rows)")
    except Exception as e:
        print(f"  market_analyses: {e}")

    # Clear LLM query log
    try:
        conn.execute("DELETE FROM llm_queries")
        print(f"[OK] Cleared llm_queries")
    except Exception as e:
        print(f"  llm_queries: {e}")

    # Keep trades and positions (useful to show history)
    conn.commit()
    conn.close()
    print("\n[OK] Demo reset complete. Run: python cli.py run --paper")
