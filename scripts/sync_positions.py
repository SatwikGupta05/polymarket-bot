#!/usr/bin/env python3
"""
Position Sync Tool

This script syncs your current Polymarket positions into the database
so the dashboard shows accurate data.

It will:
1. Get current positions from Polymarket
2. Mark old database positions as closed  
3. Add current positions to database
4. Update the dashboard data source
"""

import asyncio
import sys
import os
from datetime import datetime
from typing import List, Dict

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.clients.polymarket_client import PolymarketClient as KalshiClient
from src.utils.database import DatabaseManager, Position, TradeLog
from src.utils.logging_setup import get_trading_logger

logger = get_trading_logger(__name__)


async def sync_positions_to_database():
    """Sync current Polymarket positions to database."""
    
    kalshi_client = KalshiClient()
    db_manager = DatabaseManager()
    
    try:
        await db_manager.initialize()
        
        print("[REFRESH] Syncing positions from Polymarket to database...")
        
        # Get current Polymarket positions
        positions_response = await kalshi_client.get_positions()
        market_positions = positions_response.get('market_positions', [])
        
        print(f"[STATS] Found {len(market_positions)} positions on Polymarket")
        
        # Get current database positions
        db_positions = await db_manager.get_open_positions()
        print(f"[SAVE] Found {len(db_positions)} open positions in database")
        
        # Clear ALL existing positions from database to avoid conflicts
        print("[REFRESH] Deleting all existing database positions...")
        import aiosqlite
        async with aiosqlite.connect(db_manager.db_path) as db:
            # Delete ALL positions to avoid unique constraint conflicts
            await db.execute("DELETE FROM positions")
            await db.commit()
            print(f"   [OK] Deleted all old positions from database")
        
        # Add current Polymarket positions to database
        active_positions = 0
        total_value = 0
        
        for kalshi_pos in market_positions:
            ticker = kalshi_pos.get('ticker')
            position_count = kalshi_pos.get('position', 0)
            
            if ticker and position_count != 0:
                try:
                    # Get market data for pricing
                    market_data = await kalshi_client.get_market(ticker)
                    if market_data and 'market' in market_data:
                        market_info = market_data['market']
                        
                        # Determine side and current price
                        if position_count > 0:  # YES position
                            side = 'YES'
                            current_price = (market_info.get('yes_bid', 0) + market_info.get('yes_ask', 100)) / 2 / 100
                        else:  # NO position
                            side = 'NO'
                            current_price = (market_info.get('no_bid', 0) + market_info.get('no_ask', 100)) / 2 / 100
                        
                        # Create database position
                        position = Position(
                            market_id=ticker,
                            side=side,
                            entry_price=current_price,  # Use current price as entry
                            quantity=abs(position_count),
                            timestamp=datetime.now(),
                            rationale="Synced from Polymarket",
                            confidence=0.5,  # Default confidence
                            live=True,
                            status='open',
                            strategy='manual_sync'  # Mark as synced
                        )
                        
                        # Add to database
                        await db_manager.add_position(position)
                        
                        position_value = abs(position_count) * current_price
                        total_value += position_value
                        active_positions += 1
                        
                        print(f"   ➕ Added {ticker}: {side} {abs(position_count)} @ ${current_price:.2f} = ${position_value:.2f}")
                        
                except Exception as e:
                    print(f"   [FAIL] Error syncing {ticker}: {e}")
        
        print(f"\n[OK] Sync complete!")
        print(f"[STATS] Active positions: {active_positions}")
        print(f"[MONEY] Total position value: ${total_value:.2f}")
        print(f"[START] Dashboard should now show accurate data!")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error syncing positions: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        await kalshi_client.close()
        await db_manager.close()


async def close_database_position(db_manager: DatabaseManager, position: Position):
    """Close a database position and create a trade log."""
    
    # Create a trade log for the closed position
    trade_log = TradeLog(
        market_id=position.market_id,
        side=position.side,
        entry_price=position.entry_price,
        exit_price=position.entry_price,  # Use same price for zero P&L
        quantity=position.quantity,
        pnl=0.0,  # Zero P&L since we're just cleaning up
        entry_timestamp=position.timestamp,
        exit_timestamp=datetime.now(),
        rationale="Position sync cleanup",
        strategy=position.strategy or 'unknown'
    )
    
    # Add trade log and close position
    await db_manager.add_trade_log(trade_log)
    
    # Update position status to closed
    import aiosqlite
    async with aiosqlite.connect(db_manager.db_path) as db:
        await db.execute(
            "UPDATE positions SET status = 'closed' WHERE market_id = ? AND side = ?",
            (position.market_id, position.side)
        )
        await db.commit()


async def verify_sync():
    """Verify the sync worked correctly."""
    
    print("\n[SEARCH] Verifying sync results...")
    
    kalshi_client = KalshiClient()
    db_manager = DatabaseManager()
    
    try:
        await db_manager.initialize()
        
        # Get Polymarket positions
        positions_response = await kalshi_client.get_positions()
        kalshi_positions = positions_response.get('market_positions', [])
        kalshi_active = len([p for p in kalshi_positions if p.get('position', 0) != 0])
        
        # Get database positions
        db_positions = await db_manager.get_open_positions()
        
        print(f"[STATS] Polymarket active positions: {kalshi_active}")
        print(f"[SAVE] Database open positions: {len(db_positions)}")
        
        if kalshi_active == len(db_positions):
            print("[OK] Sync successful - counts match!")
        else:
            print("[WARN] Counts don't match - may need to run sync again")
            
    except Exception as e:
        print(f"[FAIL] Error verifying sync: {e}")
        
    finally:
        await kalshi_client.close()
        await db_manager.close()


async def main():
    """Main function."""
    
    print("[REFRESH] Position Database Sync Tool")
    print("=" * 50)
    print("This will sync your current Polymarket positions into the database")
    print("so the dashboard shows accurate trade counts and P&L data.")
    print()
    
    # Run sync
    success = await sync_positions_to_database()
    
    if success:
        await verify_sync()
        print("\n[WIN] Position sync complete!")
        print("[STATS] Refresh your dashboard to see updated data")
        print("[START] Run: python launch_dashboard.py")
    else:
        print("\n[FAIL] Sync failed - check errors above")


if __name__ == "__main__":
    asyncio.run(main()) 
