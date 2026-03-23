"""
Position Tracking Job

This job monitors open positions and implements smart exit strategies:
- Market resolution (original)
- Stop-loss exits
- Take-profit exits  
- Time-based exits
- Confidence-based exits
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.utils.database import DatabaseManager, Position, TradeLog
from src.config.settings import settings
from src.utils.logging_setup import setup_logging, get_trading_logger
from src.clients.polymarket_client import PolymarketClient as KalshiClient

MAX_HOLD_HOURS_OVERRIDE = 24  # Hard cap to prevent positions from getting stuck indefinitely


def _normalize_market_price(raw_price) -> Optional[float]:
    """Convert API price values to 0-1 dollars, returning None when unavailable."""
    if raw_price is None:
        return None

    price = float(raw_price)
    if price > 1.0:
        price /= 100.0
    return price


def _calculate_position_pnl(entry_price: float, exit_price: float, quantity: int, side: str) -> float:
    """Calculate side-aware PnL for YES and NO positions with a small fee buffer."""
    if side.upper() == "YES":
        pnl = (exit_price - entry_price) * quantity
    else:
        pnl = (entry_price - exit_price) * quantity

    fee = 0.01  # 1 cent per contract approx
    return pnl - (fee * quantity)


def _hours_since(timestamp: datetime) -> float:
    """Support both legacy naive timestamps and newer UTC-aware timestamps."""
    if timestamp.tzinfo is not None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.now()
    return (now - timestamp).total_seconds() / 3600


async def should_exit_position(
    position: Position, 
    current_yes_price: float, 
    current_no_price: float, 
    market_status: str,
    market_result: str = None
) -> tuple[bool, str, float]:
    """
    Determine if position should be exited based on smart exit strategies.
    
    Returns:
        (should_exit, exit_reason, exit_price)
    """
    current_price = current_yes_price if position.side == "YES" else current_no_price
    
    # 1. Market resolution (original logic)
    if market_status == 'closed':
        if market_result:
            exit_price = 1.0 if market_result.upper() == position.side.upper() else 0.0
        else:
            # No result yet: use the best current price, but never force a zero exit.
            exit_price = current_price if current_price >= 0.01 else position.entry_price
        return True, "market_resolution", exit_price
    
    # 2. ENHANCED Stop-loss exit using proper logic for YES/NO positions
    if position.stop_loss_price:
        from src.utils.stop_loss_calculator import StopLossCalculator
        
        should_trigger = StopLossCalculator.is_stop_loss_triggered(
            position_side=position.side,
            entry_price=position.entry_price,
            current_price=current_price,
            stop_loss_price=position.stop_loss_price
        )
        
        if should_trigger:
            # Calculate the actual loss to log it
            expected_pnl = StopLossCalculator.calculate_pnl_at_stop_loss(
                entry_price=position.entry_price,
                stop_loss_price=position.stop_loss_price,
                quantity=position.quantity,
                side=position.side
            )
            return True, f"stop_loss_triggered_pnl_{expected_pnl:.2f}", current_price
    
    # 3. Take-profit exit (enhanced logic for YES/NO)
    if position.take_profit_price:
        take_profit_triggered = False

        if position.side.upper() == "YES":
            # Take-profit must be above entry to be a real profit for YES.
            if position.take_profit_price > position.entry_price:
                take_profit_triggered = current_price >= position.take_profit_price
        else:
            # For NO, profit only exists when the target is below entry.
            if position.take_profit_price < position.entry_price:
                take_profit_triggered = current_price <= position.take_profit_price

        if take_profit_triggered:
            return True, "take_profit", current_price
    
    # 4. Time-based exit
    if position.max_hold_hours:
        hours_held = _hours_since(position.timestamp)
        if hours_held >= position.max_hold_hours:
            return True, "time_based", current_price
    
    # 5. Emergency exit for positions without stop-loss (legacy positions)
    if not position.stop_loss_price:
        # Calculate emergency stop-loss at 10% loss
        from src.utils.stop_loss_calculator import StopLossCalculator
        emergency_stop = StopLossCalculator.calculate_simple_stop_loss(
            entry_price=position.entry_price,
            side=position.side,
            stop_loss_pct=0.10  # 10% emergency stop
        )
        
        emergency_triggered = StopLossCalculator.is_stop_loss_triggered(
            position_side=position.side,
            entry_price=position.entry_price,
            current_price=current_price,
            stop_loss_price=emergency_stop
        )
        
        if emergency_triggered:
            return True, "emergency_stop_loss_10pct", current_price
    
    # 6. Confidence-based exit (placeholder - would need re-analysis)
    # This would require periodic re-analysis, which we're avoiding for cost reasons
    # Could be implemented as a separate, less frequent job
    
    return False, "", current_price

async def calculate_dynamic_exit_levels(position: Position) -> dict:
    """Calculate smart exit levels using Grok4 recommendations."""
    from src.utils.stop_loss_calculator import StopLossCalculator
    
    # Use the centralized stop-loss calculator
    exit_levels = StopLossCalculator.calculate_stop_loss_levels(
        entry_price=position.entry_price,
        side=position.side,
        confidence=position.confidence or 0.7,
        market_volatility=0.2,  # Default volatility estimate
        time_to_expiry_days=30.0  # Default time estimate
    )
    
    return exit_levels

async def run_tracking(db_manager: Optional[DatabaseManager] = None):
    """
    Enhanced position tracking with smart exit strategies and sell limit orders.
    
    Args:
        db_manager: Optional DatabaseManager instance for testing.
    """
    logger = get_trading_logger("position_tracking")
    logger.info("Starting enhanced position tracking job with sell limit orders.")

    if db_manager is None:
        db_manager = DatabaseManager()
        await db_manager.initialize()

    kalshi_client = KalshiClient()

    try:
        # Step 1: Place sell limit orders for profit-taking and stop-loss
        from src.jobs.execute import place_profit_taking_orders, place_stop_loss_orders
        
        logger.info("[TARGET] Checking for profit-taking opportunities...")
        profit_results = await place_profit_taking_orders(
            db_manager=db_manager,
            kalshi_client=kalshi_client,
            profit_threshold=0.20  # 20% profit target
        )
        
        logger.info("[SHIELD] Checking for stop-loss protection...")
        stop_loss_results = await place_stop_loss_orders(
            db_manager=db_manager,
            kalshi_client=kalshi_client,
            stop_loss_threshold=-0.15  # 15% stop loss
        )
        
        total_sell_orders = profit_results['orders_placed'] + stop_loss_results['orders_placed']
        if total_sell_orders > 0:
            logger.info(f"[UP] SELL LIMIT ORDERS SUMMARY: {total_sell_orders} orders placed")
            logger.info(f"   Profit-taking: {profit_results['orders_placed']} orders")
            logger.info(f"   Stop-loss: {stop_loss_results['orders_placed']} orders")
        
        # Step 2: Continue with existing position tracking (market resolution, etc.)
        try:
            open_positions = await db_manager.get_open_positions()
        except AttributeError:
            open_positions = await db_manager.get_open_live_positions()

        if not open_positions:
            logger.info("No open positions to track.")
            return

        logger.info(f"Found {len(open_positions)} open positions to track.")

        exits_executed = 0
        for position in open_positions:
            try:
                # Get current market data
                # Get token IDs from market data
                market_response = await kalshi_client.get_market(position.market_id)
                market_data = market_response.get("market", {})

                if not market_data:
                    logger.warning(f"No market data for {position.market_id} - skipping")
                    continue

                debug_mode = bool(getattr(settings, "debug", False))

                # FIX: use token_id not market_id for price fetch
                yes_token_id = market_data.get("yes_token_id", "")
                no_token_id = market_data.get("no_token_id", "")

                if debug_mode:
                    logger.info(f"[DEBUG] yes_token_id={yes_token_id[:20]} no_token_id={no_token_id[:20]}")

                # Fetch price via token_id with fallback chain
                current_yes_price = None
                current_no_price = None

                if yes_token_id:
                    current_yes_price = await kalshi_client.get_token_price(yes_token_id)
                if no_token_id:
                    current_no_price = await kalshi_client.get_token_price(no_token_id)

                # Fallback directly to market-level price fields - do not require token IDs.
                if not current_yes_price or current_yes_price <= 0.001:
                    raw = _normalize_market_price(market_data.get("yes_price"))
                    current_yes_price = raw if raw and raw > 0.001 else None

                if not current_no_price or current_no_price <= 0.001:
                    raw = _normalize_market_price(market_data.get("no_price"))
                    current_no_price = raw if raw and raw > 0.001 else None

                # Derive the missing side from the available complement.
                if current_yes_price and (not current_no_price or current_no_price <= 0.001):
                    current_no_price = round(1.0 - current_yes_price, 4)
                if current_no_price and (not current_yes_price or current_yes_price <= 0.001):
                    current_yes_price = round(1.0 - current_no_price, 4)

                current_price = current_yes_price if position.side.upper() == "YES" else current_no_price

                # FIX 5: max hold exit - force close after 24h if no other exit triggered.
                # Prevents positions being stuck forever when price data is intermittent.
                hours_held = _hours_since(position.timestamp)
                if hours_held >= MAX_HOLD_HOURS_OVERRIDE:
                    exit_price = current_price if (current_price and current_price >= 0.01) else position.entry_price
                    pnl = (
                        (exit_price - position.entry_price) * position.quantity
                        if position.side.upper() == "YES"
                        else (position.entry_price - exit_price) * position.quantity
                    )

                    logger.info(
                        f"MAX HOLD EXIT: {position.market_id} held {hours_held:.1f}h "
                        f"entry={position.entry_price:.3f} exit={exit_price:.3f} PnL=${pnl:.2f}"
                    )

                    trade_log = TradeLog(
                        market_id=position.market_id,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        pnl=pnl,
                        entry_timestamp=position.timestamp,
                        exit_timestamp=datetime.now(timezone.utc),
                        rationale=f"Max hold time exceeded ({hours_held:.1f}h > {MAX_HOLD_HOURS_OVERRIDE}h)",
                        strategy=position.strategy,
                    )
                    await db_manager.add_trade_log(trade_log)
                    await db_manager.update_position_status(position.id, "closed")
                    exits_executed += 1
                    continue

                if not current_price or current_price <= 0.001:
                    logger.warning(
                                    f"Skipping exit for {position.market_id} — "
                                    f"price invalid (yes={current_yes_price}, no={current_no_price}). "
                                    f"Holding position until valid price available."
)
                    continue

                # Safe to proceed - price is valid.
                current_yes_price = current_yes_price or 0.5
                current_no_price = current_no_price or 0.5
                market_status = market_data.get("status", "unknown")
                market_result = market_data.get("result", None)
                
                # If position doesn't have exit strategy set, calculate defaults
                if not position.stop_loss_price and not position.take_profit_price:
                    logger.info(f"Setting up exit strategy for position {position.market_id}")
                    exit_levels = await calculate_dynamic_exit_levels(position)
                    
                    # Update position with exit strategy (this would need a new DB method)
                    # For now, we'll apply them dynamically
                    position.stop_loss_price = exit_levels["stop_loss_price"]
                    position.take_profit_price = exit_levels["take_profit_price"] 
                    position.max_hold_hours = exit_levels["max_hold_hours"]
                    position.target_confidence_change = exit_levels["target_confidence_change"]

                # Check if position should be exited (market resolution, time-based, etc.)
                should_exit, exit_reason, exit_price = await should_exit_position(
                    position, current_yes_price, current_no_price, market_status, market_result
                )

                if should_exit:
                    if exit_price is None or exit_price < 0.01:
                        logger.warning(
                            f"Exit price {exit_price:.4f} invalid for {position.market_id} "
                            f"({exit_reason}) - holding until valid price"
                        )
                        continue

                    logger.info(
                        f"Exiting position {position.market_id} due to {exit_reason}. "
                        f"Entry: {position.entry_price:.3f}, Exit: {exit_price:.3f}"
                    )
                    
                    # Calculate PnL
                    pnl = _calculate_position_pnl(
                        position.entry_price, exit_price, position.quantity, position.side
                    )
                    
                    # Create trade log
                    trade_log = TradeLog(
                        market_id=position.market_id,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        pnl=pnl,
                        entry_timestamp=position.timestamp,
                        exit_timestamp=datetime.now(timezone.utc),
                        rationale=f"{position.rationale} | EXIT: {exit_reason}"
                    )

                    # Record the exit
                    await db_manager.add_trade_log(trade_log)
                    await db_manager.update_position_status(position.id, 'closed')
                    
                    exits_executed += 1
                    logger.info(
                        f"Position for market {position.market_id} closed via {exit_reason}. "
                        f"PnL: ${pnl:.2f}"
                    )
                else:
                    # Log current position status for monitoring
                    current_price = current_yes_price if position.side == "YES" else current_no_price
                    unrealized_pnl = _calculate_position_pnl(
                        position.entry_price, current_price, position.quantity, position.side
                    )
                    hours_held = _hours_since(position.timestamp)

                    logger.info(
                        f"{position.market_id} | Entry: {position.entry_price:.3f} | "
                        f"Current: {current_price:.3f} | Side: {position.side}"
                    )
                    
                    logger.debug(
                        f"Position {position.market_id} status: "
                        f"Entry: {position.entry_price:.3f}, Current: {current_price:.3f}, "
                        f"Unrealized P&L: ${unrealized_pnl:.2f}, Hours held: {hours_held:.1f}"
                    )

            except Exception as e:
                logger.error(f"Failed to process position for market {position.market_id}.", error=str(e))

        logger.info(f"Position tracking completed. Sell orders: {total_sell_orders}, Market exits: {exits_executed}")

    except Exception as e:
        logger.error("Error in position tracking job.", error=str(e), exc_info=True)
    finally:
        await kalshi_client.close()

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_tracking())
