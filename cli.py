#!/usr/bin/env python3
"""
Polymarket AI Trading Bot -- Unified CLI

Provides a single entry point for all bot operations:
    python cli.py run          Start the trading bot
    python cli.py dashboard    Launch the Streamlit monitoring dashboard
    python cli.py status       Show portfolio balance, positions, and P&L
    python cli.py backtest     Run backtests (placeholder)
    python cli.py health       Verify API connections, database, and configuration
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> None:
    """Start the trading bot (disciplined mode by default)."""
    from src.utils.logging_setup import setup_logging

    log_level = getattr(args, "log_level", "INFO")
    setup_logging(log_level=log_level)

    live = getattr(args, "live", False)
    paper = getattr(args, "paper", False)
    beast = getattr(args, "beast", False)
    disciplined = getattr(args, "disciplined", False)
    safe_compounder = getattr(args, "safe_compounder", False)

    if live and paper:
        print("Error: --live and --paper are mutually exclusive.")
        sys.exit(1)

    live_mode = live and not paper

    if live_mode:
        print("[WARN]  WARNING: LIVE TRADING MODE ENABLED")
        print("   This will use real money and place actual trades.")

    # --safe-compounder mode: edge-based NO-side only
    if safe_compounder:
        _run_safe_compounder(live_mode=live_mode)
        return

    # --beast mode: original aggressive settings (NOT default)
    if beast:
        print("[WARN]  BEAST MODE: Aggressive settings enabled.")
        print("   WARNING: Aggressive settings with no guardrails. Use at your own risk.")
        from beast_mode_bot import BeastModeBot
        bot = BeastModeBot(live_mode=live_mode)
        try:
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            print("\nTrading bot stopped by user.")
        return

    # DEFAULT: disciplined mode (with or without --disciplined flag)
    print("[SHIELD]  DISCIPLINED MODE (default)")
    print("   Category scoring + portfolio enforcement active.")
    print("   Use --beast to run without guardrails (not recommended).")

    from beast_mode_bot import BeastModeBot
    from src.strategies.category_scorer import CategoryScorer
    from src.strategies.portfolio_enforcer import PortfolioEnforcer

    # Apply disciplined settings overrides
    from src.config import settings as cfg
    cfg.settings.trading.min_confidence_to_trade = 0.65
    cfg.settings.trading.max_position_size_pct = 3.0
    cfg.settings.trading.kelly_fraction = 0.25
    cfg.max_drawdown = 0.15
    cfg.max_sector_exposure = 0.30

    bot = BeastModeBot(live_mode=live_mode)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nTrading bot stopped by user.")


def _run_safe_compounder(live_mode: bool = False) -> None:
    """Run the Safe Compounder strategy."""
    from src.clients.polymarket_client import PolymarketClient as KalshiClient
    from src.strategies.safe_compounder import SafeCompounder

    print("[LOCK] SAFE COMPOUNDER MODE")
    print("   NO-side only | Edge-based | Near-certain outcomes")
    if not live_mode:
        print("   DRY RUN — no real orders will be placed")

    async def _run():
        client = KalshiClient()
        try:
            compounder = SafeCompounder(
                client=client,
                dry_run=not live_mode,
            )
            stats = await compounder.run()
            return stats
        finally:
            await client.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nSafe Compounder stopped by user.")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Launch the Streamlit monitoring dashboard."""
    import subprocess

    # Prefer the dedicated dashboard launch script if it exists.
    dashboard_script = Path(__file__).parent / "scripts" / "launch_dashboard.py"
    beast_dashboard = Path(__file__).parent / "scripts" / "beast_mode_dashboard.py"

    if dashboard_script.exists():
        subprocess.run([sys.executable, str(dashboard_script)], check=False)
    elif beast_dashboard.exists():
        # Fall back to running the dashboard module directly.
        from src.utils.logging_setup import setup_logging
        from beast_mode_bot import BeastModeBot

        setup_logging(log_level="INFO")
        bot = BeastModeBot(live_mode=False, dashboard_mode=True)
        try:
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            print("\nDashboard stopped by user.")
    else:
        print("Error: No dashboard script found.")
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    """Show current portfolio status: balance, positions, and P&L."""

    async def _status() -> None:
        from src.clients.polymarket_client import PolymarketClient as KalshiClient

        client = KalshiClient()
        try:
            # Fetch balance
            balance_resp = await client.get_balance()
            balance_cents = balance_resp.get("balance", 0)
            balance_usd = balance_cents / 100.0

            # Fetch normalized position payloads from the Polymarket client
            positions_resp = await client.get_positions()
            market_positions = positions_resp.get("market_positions", [])

            def _position_value_usd(pos: dict) -> float:
                for key in ("size", "currentValue", "value", "amount"):
                    raw = pos.get(key)
                    if raw not in (None, ""):
                        try:
                            return abs(float(raw))
                        except (TypeError, ValueError):
                            pass
                try:
                    return abs(float(pos.get("position", 0))) * 0.5
                except (TypeError, ValueError):
                    return 0.0

            active_positions = [p for p in market_positions if _position_value_usd(p) > 0]
            portfolio_value_usd = sum(_position_value_usd(p) for p in active_positions)

            # Display
            print("=" * 56)
            print("  PORTFOLIO STATUS")
            print("=" * 56)
            print(f"  Available Cash:     ${balance_usd:>10,.2f}")
            print(f"  Position Value:     ${portfolio_value_usd:>10,.2f}")
            print(f"  Total Portfolio:    ${balance_usd + portfolio_value_usd:>10,.2f}")
            print(f"  Active Positions:   {len(active_positions):>10}")

            total_exposure = 0.0
            total_realized_pnl = 0.0
            total_fees = 0.0

            if active_positions:
                print()
                print(f"  {'Event':<30} {'Exposure':>10} {'Cost':>10} {'P&L':>10} {'Fees':>8}")
                print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")

                for pos in active_positions:
                    ticker = (
                        pos.get("condition_id")
                        or pos.get("ticker")
                        or pos.get("market")
                        or pos.get("asset_id")
                        or "???"
                    )
                    exposure = _position_value_usd(pos)
                    cost = float(pos.get("initialValue", pos.get("cost", exposure)) or exposure)
                    pnl = float(pos.get("cashPnl", pos.get("realized_pnl", 0)) or 0)
                    fees = float(pos.get("fees", 0) or 0)
                    total_exposure += exposure
                    total_realized_pnl += pnl
                    total_fees += fees
                    print(
                        f"  {ticker:<30} ${exposure:>8.2f} ${cost:>8.2f} "
                        f"${pnl:>8.2f} ${fees:>6.2f}"
                    )

                print()
                print(f"  Total Exposure:     ${total_exposure:>10,.2f}")
                print(f"  Total Realized P&L: ${total_realized_pnl:>10,.2f}")
                print(f"  Total Fees Paid:    ${total_fees:>10,.2f}")

            print("=" * 56)
        finally:
            await client.close()

    try:
        asyncio.run(_status())
    except Exception as exc:
        print(f"Error fetching status: {exc}")
        sys.exit(1)


def cmd_scores(args: argparse.Namespace) -> None:
    """Show current category scores from the scoring system."""

    async def _scores():
        from src.strategies.category_scorer import CategoryScorer
        scorer = CategoryScorer()
        await scorer.initialize()
        scores = await scorer.get_all_scores()
        print(scorer.format_scores_table(scores))
        print()
        print("  Key: Score < 30 = BLOCKED | Alloc = max portfolio % allowed")
        print()

    try:
        asyncio.run(_scores())
    except Exception as exc:
        print(f"Error fetching scores: {exc}")
        sys.exit(1)


def cmd_history(args: argparse.Namespace) -> None:
    """Show trade history with category breakdown."""
    limit = getattr(args, "limit", 50)

    async def _history():
        import aiosqlite

        db_path = Path(__file__).parent / "trading_system.db"
        if not db_path.exists():
            print("No trading database found.")
            return

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # Overall stats
            cursor = await db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl
                FROM trade_logs
            """)
            overview = await cursor.fetchone()

            print("=" * 70)
            print("  TRADE HISTORY")
            print("=" * 70)
            if overview and overview["total"]:
                total = overview["total"]
                wins = overview["wins"] or 0
                pnl = overview["total_pnl"] or 0.0
                print(f"  Total Trades:  {total}")
                print(f"  Win Rate:      {wins/total*100:.1f}%")
                print(f"  Total P&L:     ${pnl:.2f}")
                print(f"  Avg per trade: ${(pnl/total):.2f}")
            print()

            # Category breakdown
            cursor = await db.execute("""
                SELECT
                    strategy as category,
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pnl) as total_pnl
                FROM trade_logs
                GROUP BY strategy
                ORDER BY total_pnl DESC
            """)
            cats = await cursor.fetchall()

            if cats:
                print(f"  {'Category':<22} {'Trades':>7} {'WR':>6} {'P&L':>10}")
                print(f"  {'-'*22} {'-'*7} {'-'*6} {'-'*10}")
                for row in cats:
                    cat = row["category"] or "unknown"
                    t = row["trades"]
                    w = row["wins"] or 0
                    p = row["total_pnl"] or 0.0
                    wr = f"{w/t*100:.0f}%" if t > 0 else "n/a"
                    print(f"  {cat:<22} {t:>7} {wr:>6} ${p:>9.2f}")
                print()

            # Recent trades
            cursor = await db.execute(f"""
                SELECT market_id, side, entry_price, exit_price, quantity, pnl,
                       entry_timestamp, strategy
                FROM trade_logs
                ORDER BY entry_timestamp DESC
                LIMIT {limit}
            """)
            trades = await cursor.fetchall()

            if trades:
                print(f"  Recent {limit} trades:")
                print(f"  {'Market':<28} {'Side':>4} {'Entry':>6} {'Exit':>6} {'Qty':>4} {'P&L':>8} {'Category'}")
                print(f"  {'-'*28} {'-'*4} {'-'*6} {'-'*6} {'-'*4} {'-'*8} {'-'*12}")
                for t in trades:
                    ts = (t["entry_timestamp"] or "")[:10]
                    cat = t["strategy"] or ""
                    print(
                        f"  {t['market_id'][:28]:<28} {t['side']:>4} "
                        f"{t['entry_price']:>6.2f} {t['exit_price']:>6.2f} "
                        f"{t['quantity']:>4} ${t['pnl']:>7.2f}  {cat}"
                    )

            # Blocked trades summary
            cursor2 = await db.execute("""
                SELECT COUNT(*) FROM blocked_trades
            """)
            r2 = await cursor2.fetchone()
            if r2 and r2[0]:
                print(f"\n  [STOP] {r2[0]} trades blocked by portfolio enforcer (use 'python cli.py health' for details)")

            print("=" * 70)

    try:
        asyncio.run(_history())
    except Exception as exc:
        print(f"Error fetching history: {exc}")
        sys.exit(1)


def cmd_backtest(args: argparse.Namespace) -> None:
    """Run backtesting engine on resolved Polymarket markets."""
    import subprocess, sys
    cmd = [sys.executable, "backtest.py"]
    if getattr(args, "offline",   False): cmd.append("--offline")
    if getattr(args, "report",    False): cmd.append("--report")
    if getattr(args, "limit",     None):  cmd += ["--limit",     str(args.limit)]
    if getattr(args, "category",  None):  cmd += ["--category",  args.category]
    if getattr(args, "min_volume",None):  cmd += ["--min-volume", str(args.min_volume)]
    subprocess.run(cmd)


def cmd_health(args: argparse.Namespace) -> None:
    """Run health checks on configuration, API, and database."""

    checks_passed = 0
    checks_failed = 0

    def ok(label: str, detail: str = "") -> None:
        nonlocal checks_passed
        checks_passed += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [PASS] {label}{suffix}")

    def fail(label: str, detail: str = "") -> None:
        nonlocal checks_failed
        checks_failed += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")

    print("=" * 56)
    print("  HEALTH CHECK")
    print("=" * 56)
    print()

    # 1. .env file
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        ok(".env file exists")
    else:
        fail(".env file missing", "copy env.template to .env and fill in keys")

    # 2. Required environment variables
    from dotenv import load_dotenv
    load_dotenv()

    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        val = os.getenv(var, "")
        if val and "your_" not in val.lower():
            ok(f"{var} is set")
        else:
            fail(f"{var} is missing or placeholder")

    # 3. Polymarket API connection
    async def _check_api() -> None:
        from src.clients.polymarket_client import PolymarketClient as KalshiClient
        client = KalshiClient()
        try:
            healthy = await client.health_check()
            if healthy:
                balance_resp = await client.get_balance()
                balance_usd = balance_resp.get("balance", 0) / 100.0
                ok("Polymarket API connection", f"balance=${balance_usd:,.2f}")
            else:
                fail("Polymarket API connection", "health check failed")
        except Exception as exc:
            fail("Polymarket API connection", str(exc))
        finally:
            await client.close()

    try:
        asyncio.run(_check_api())
    except Exception as exc:
        fail("Polymarket API connection", str(exc))

    # 4. Database
    db_path = Path(__file__).parent / "trading_system.db"
    try:
        import aiosqlite

        async def _check_db() -> None:
            from src.utils.database import DatabaseManager
            db_manager = DatabaseManager()
            await db_manager.initialize()
            ok("Database initialization", str(db_path))

        asyncio.run(_check_db())
    except Exception as exc:
        fail("Database initialization", str(exc))

    # 5. Python version
    if sys.version_info >= (3, 12):
        ok("Python version", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    else:
        fail("Python version", f"requires >=3.12, found {sys.version}")

    # Summary
    print()
    total = checks_passed + checks_failed
    print(f"  {checks_passed}/{total} checks passed")
    if checks_failed:
        print(f"  {checks_failed} issue(s) need attention")
    else:
        print("  All systems operational.")
    print("=" * 56)

    if checks_failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polymarket-bot",
        description="Polymarket AI Trading Bot -- Multi-model AI trading for prediction markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python cli.py run                      Start in disciplined mode (default, paper)\n"
            "  python cli.py run --live               Disciplined mode with real capital\n"
            "  python cli.py run --disciplined --live Explicit disciplined live trading\n"
            "  python cli.py run --safe-compounder    NO-side edge-based strategy\n"
            "  python cli.py run --beast              Beast mode (aggressive, not recommended)\n"
            "  python cli.py scores                   Show category scores\n"
            "  python cli.py history                  Show trade history + category breakdown\n"
            "  python cli.py status                   Check portfolio balance and positions\n"
            "  python cli.py health                   Verify all connections and config\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = subparsers.add_parser(
        "run",
        help="Start the trading bot (disciplined mode by default)",
        description=(
            "Launch the trading bot. Default is disciplined mode with category scoring "
            "and portfolio enforcement. Use --beast for aggressive mode (not recommended — "
            "aggressive settings with no guardrails — not recommended)."
        ),
    )
    live_group = p_run.add_mutually_exclusive_group()
    live_group.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading with real capital (default: paper trading)",
    )
    live_group.add_argument(
        "--paper",
        action="store_true",
        help="Run in paper-trading mode (no real orders)",
    )
    strategy_group = p_run.add_mutually_exclusive_group()
    strategy_group.add_argument(
        "--disciplined",
        action="store_true",
        default=True,
        help="Disciplined mode: category scoring + portfolio enforcement (DEFAULT)",
    )
    strategy_group.add_argument(
        "--beast",
        action="store_true",
        help="Beast mode: aggressive settings, no guardrails (not recommended)",
    )
    strategy_group.add_argument(
        "--safe-compounder",
        action="store_true",
        dest="safe_compounder",
        help="Safe Compounder: NO-side only, edge-based, near-certain outcomes",
    )
    p_run.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO)",
    )
    p_run.set_defaults(func=cmd_run)

    # --- scores ---
    p_scores = subparsers.add_parser(
        "scores",
        help="Show current category scores",
        description="Display all trading category scores, win rates, ROI, and allocation limits.",
    )
    p_scores.set_defaults(func=cmd_scores)

    # --- history ---
    p_history = subparsers.add_parser(
        "history",
        help="Show trade history with category breakdown",
        description="Display closed trade history grouped by category, win rate, and P&L.",
    )
    p_history.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of recent trades to show (default: 50)",
    )
    p_history.set_defaults(func=cmd_history)

    # --- dashboard ---
    p_dash = subparsers.add_parser(
        "dashboard",
        help="Launch the Streamlit monitoring dashboard",
        description="Open a real-time web dashboard showing portfolio performance, positions, risk metrics, and AI decision logs.",
    )
    p_dash.set_defaults(func=cmd_dashboard)

    # --- status ---
    p_status = subparsers.add_parser(
        "status",
        help="Show portfolio balance, positions, and P&L",
        description="Connect to the Polymarket APIs and display current account balance, open positions, and estimated portfolio value.",
    )
    p_status.set_defaults(func=cmd_status)

    # --- backtest ---
    p_bt = subparsers.add_parser(
        "backtest",
        help="Backtest AI signals against resolved Polymarket markets",
    )
    p_bt.add_argument("--limit",      type=int,   default=100,   help="Markets to fetch")
    p_bt.add_argument("--category",   type=str,   default=None,  help="Filter: crypto,politics,sports...")
    p_bt.add_argument("--min-volume", type=float, default=500,   dest="min_volume", help="Min USDC volume")
    p_bt.add_argument("--offline",    action="store_true",       help="Use built-in sample data (no API)")
    p_bt.add_argument("--report",     action="store_true",       help="Save HTML report")
    p_bt.set_defaults(func=cmd_backtest)

    # --- health ---
    p_health = subparsers.add_parser(
        "health",
        help="Verify API connections, database, and configuration",
        description="Run a series of diagnostic checks: .env presence, AI key configuration, Polymarket API connectivity, database initialization, and Python version.",
    )
    p_health.set_defaults(func=cmd_health)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
