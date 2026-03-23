#!/usr/bin/env python3
"""
Database Schema Fix Script

This script manually runs database migrations and fixes any schema issues
to ensure the dashboard works properly.
"""

import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.database import DatabaseManager


async def fix_database_schema():
    """Fix database schema issues and run migrations."""
    
    print("[FIX] Database Schema Fix Script")
    print("=" * 50)
    
    db_manager = DatabaseManager()
    
    try:
        print("[STATS] Initializing database and running migrations...")
        await db_manager.initialize()
        print("[OK] Database initialization complete!")
        
        # Test strategy column in positions
        print("\n[SEARCH] Checking positions table schema...")
        import aiosqlite
        async with aiosqlite.connect(db_manager.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(positions)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'strategy' in column_names:
                print("[OK] Strategy column exists in positions table")
            else:
                print("[FAIL] Strategy column missing from positions table")
                print("[FIX] Adding strategy column...")
                await db.execute("ALTER TABLE positions ADD COLUMN strategy TEXT")
                await db.commit()
                print("[OK] Strategy column added to positions table")
        
        # Test strategy column in trade_logs
        print("\n[SEARCH] Checking trade_logs table schema...")
        async with aiosqlite.connect(db_manager.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(trade_logs)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'strategy' in column_names:
                print("[OK] Strategy column exists in trade_logs table")
            else:
                print("[FAIL] Strategy column missing from trade_logs table")
                print("[FIX] Adding strategy column...")
                await db.execute("ALTER TABLE trade_logs ADD COLUMN strategy TEXT")
                await db.commit()
                print("[OK] Strategy column added to trade_logs table")
        
        # Test llm_queries table
        print("\n[SEARCH] Checking llm_queries table...")
        async with aiosqlite.connect(db_manager.db_path) as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='llm_queries'")
            table_exists = await cursor.fetchone()
            
            if table_exists:
                print("[OK] LLM queries table exists")
            else:
                print("[FAIL] LLM queries table missing")
                print("[FIX] Creating llm_queries table...")
                await db.execute("""
                    CREATE TABLE llm_queries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        query_type TEXT NOT NULL,
                        market_id TEXT,
                        prompt TEXT NOT NULL,
                        response TEXT NOT NULL,
                        tokens_used INTEGER,
                        cost_usd REAL,
                        confidence_extracted REAL,
                        decision_extracted TEXT
                    )
                """)
                await db.commit()
                print("[OK] LLM queries table created")
        
        # Test performance query
        print("\n[SEARCH] Testing performance query...")
        try:
            performance = await db_manager.get_performance_by_strategy()
            print(f"[OK] Performance query successful: {len(performance)} strategies found")
            
            if performance:
                for strategy, stats in performance.items():
                    print(f"   - {strategy}: {stats['completed_trades']} trades, ${stats['total_pnl']:.2f} P&L")
            else:
                print("   [INFO] No strategy performance data found (normal for new systems)")
                
        except Exception as e:
            print(f"[FAIL] Performance query failed: {e}")
        
        # Test LLM query
        print("\n[SEARCH] Testing LLM queries...")
        try:
            queries = await db_manager.get_llm_queries(hours_back=24, limit=5)
            print(f"[OK] LLM query successful: {len(queries)} queries found")
            
            if queries:
                for query in queries:
                    print(f"   - {query.strategy}: {query.query_type} at {query.timestamp.strftime('%H:%M:%S')}")
            else:
                print("   [INFO] No LLM queries found (normal until trading system runs)")
                
        except Exception as e:
            print(f"[FAIL] LLM query failed: {e}")
        
        print("\n[WIN] Database schema fix complete!")
        print("[STATS] Dashboard should now work properly")
        
    except Exception as e:
        print(f"[FAIL] Error fixing database schema: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await db_manager.close()
    
    return True


async def verify_database_health():
    """Verify database health and show current state."""
    
    print("\n[LIST] Database Health Check")
    print("-" * 30)
    
    db_manager = DatabaseManager()
    
    try:
        await db_manager.initialize()
        
        # Count records in each table
        import aiosqlite
        async with aiosqlite.connect(db_manager.db_path) as db:
            # Markets
            cursor = await db.execute("SELECT COUNT(*) FROM markets")
            markets_count = (await cursor.fetchone())[0]
            
            # Positions
            cursor = await db.execute("SELECT COUNT(*) FROM positions")
            positions_count = (await cursor.fetchone())[0]
            
            # Trade logs
            cursor = await db.execute("SELECT COUNT(*) FROM trade_logs")
            trades_count = (await cursor.fetchone())[0]
            
            # LLM queries (if table exists)
            try:
                cursor = await db.execute("SELECT COUNT(*) FROM llm_queries")
                llm_count = (await cursor.fetchone())[0]
            except:
                llm_count = "Table not created yet"
        
        print(f"[STATS] Markets: {markets_count}")
        print(f"💼 Positions: {positions_count}")
        print(f"[UP] Trades: {trades_count}")
        print(f"[BOT] LLM Queries: {llm_count}")
        
    except Exception as e:
        print(f"[FAIL] Database health check failed: {e}")
    
    finally:
        await db_manager.close()


if __name__ == "__main__":
    async def main():
        success = await fix_database_schema()
        await verify_database_health()
        
        if success:
            print("\n[START] Ready to launch dashboard!")
            print("Run: python launch_dashboard.py")
        else:
            print("\n[FAIL] Database fix failed - check errors above")
    
    asyncio.run(main()) 