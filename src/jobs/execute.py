"""
Trade Execution Job

This job takes a position and executes it as a trade.
"""
import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict

from src.utils.database import DatabaseManager, Position
from src.config.settings import settings
from src.utils.logging_setup import get_trading_logger
from src.clients.polymarket_client import PolymarketClient as KalshiClient, PolymarketAPIError as KalshiAPIError


def _normalize_market_price(raw_price) -> Optional[float]:
    """Convert API price values to 0-1 dollars, returning None when unavailable."""
    if raw_price is None:
        return None

    price = float(raw_price)
    if price > 1.0:
        price /= 100.0
    return price


def _get_position_token_id(market: Dict, side: str) -> str:
    """Return the Polymarket token id for the YES/NO side being traded."""
    if side.upper() == "YES":
        return str(market.get("yes_token_id", "") or "")
    return str(market.get("no_token_id", "") or "")


def _get_side_price_cents(market: Dict, side: str) -> Optional[int]:
    """Return the best available Polymarket side price in cents."""
    side_lower = side.lower()
    for field in (
        f"{side_lower}_price",
        f"{side_lower}_ask_dollars",
        f"{side_lower}_bid_dollars",
        f"{side_lower}_ask",
        f"{side_lower}_bid",
    ):
        price = _normalize_market_price(market.get(field))
        if price and price > 0:
            return max(1, min(99, int(round(price * 100))))
    return None


def _calculate_position_pnl(entry_price: float, exit_price: float, quantity: int, side: str) -> float:
    """Calculate side-aware PnL for YES and NO positions with a small fee buffer."""
    if side == "YES":
        pnl = (exit_price - entry_price) * quantity
    else:
        pnl = (entry_price - exit_price) * quantity

    fee = 0.01  # 1 cent per contract approx
    return pnl - (fee * quantity)


async def execute_position(
    position: Position, 
    live_mode: bool, 
    db_manager: DatabaseManager, 
    kalshi_client: KalshiClient
) -> bool:
    """
    Executes a single trade position.
    
    Args:
        position: The position to execute.
        live_mode: Whether to execute a live or simulated trade.
        db_manager: The database manager instance.
        kalshi_client: The Polymarket client instance.
        
    Returns:
        True if execution was successful, False otherwise.
    """
    logger = get_trading_logger("trade_execution")
    logger.info(f"[TARGET] Executing position for market: {position.market_id}")
    logger.info(f"[CTRL] Live mode: {live_mode}")
    
    if live_mode:
        logger.warning(f"[MONEY] PLACING LIVE ORDER - Real money will be used for {position.market_id}")
        try:
            # Resolve the Polymarket token id and side-specific price.
            market_data = await kalshi_client.get_market(position.market_id)
            market = market_data.get('market', {})
            token_id = _get_position_token_id(market, position.side)
            if not token_id:
                logger.error(f"No token id found for {position.market_id} side={position.side}")
                return False

            side_lower = position.side.lower()
            client_order_id = str(uuid.uuid4())
            price_cents = _get_side_price_cents(market, position.side)
            if price_cents is None:
                logger.error(f"No valid {position.side} price for {position.market_id}")
                return False

            order_params = {
                "ticker": token_id,
                "client_order_id": client_order_id,
                "side": side_lower,
                "action": "buy",
                "count": position.quantity,
                "type_": "market"
            }

            if side_lower == "yes":
                order_params["yes_price"] = price_cents
            else:
                order_params["no_price"] = price_cents
            
            logger.info(f"Placing order with params: {order_params}")
            order_response = await kalshi_client.place_order(**order_params)
            
            # For a market order, the fill price is not guaranteed.
            # A more robust implementation would query the /fills endpoint
            # to confirm the execution price after the fact.
            # For now, we will optimistically assume it fills at the entry price.
            fill_price = position.entry_price

            if position.id is None:
                logger.error(
                    f"Cannot update position to live - position.id is None "
                    f"for market {position.market_id}. "
                    f"Position was not saved to DB before execute was called."
                )
                return False

            await db_manager.update_position_to_live(position.id, fill_price)
            logger.info(f"[OK] LIVE ORDER PLACED for {position.market_id}. Order ID: {order_response.get('order', {}).get('order_id')}")
            logger.info(f"[MONEY] Real money used: ${position.quantity * fill_price:.2f}")
            return True

        except KalshiAPIError as e:
            logger.error(f"[FAIL] FAILED to place LIVE order for {position.market_id}: {e}")
            return False
    else:
        # Simulate the trade
        if position.id is None:
            logger.error(
                f"Cannot update position to live - position.id is None "
                f"for market {position.market_id}. "
                f"Position was not saved to DB before execute was called."
            )
            return False

        await db_manager.update_position_to_live(position.id, position.entry_price)
        logger.info(f"[LOG] PAPER TRADE SIMULATED for {position.market_id} - No real money used")
        logger.info(f"[STATS] Would have used: ${position.quantity * position.entry_price:.2f}")
        return True


async def place_sell_limit_order(
    position: Position,
    limit_price: float,
    db_manager: DatabaseManager,
    kalshi_client: KalshiClient
) -> bool:
    """
    Place a sell limit order to close an existing position.
    
    Args:
        position: The position to close
        limit_price: The limit price for the sell order (in dollars)
        db_manager: Database manager
        kalshi_client: Polymarket API client
    
    Returns:
        True if order placed successfully, False otherwise
    """
    logger = get_trading_logger("sell_limit_order")
    
    try:
        import uuid
        client_order_id = str(uuid.uuid4())
        
        # Compatibility wrapper still accepts integer cents for limit prices.
        limit_price_cents = int(limit_price * 100)
        side = position.side.lower()  # "YES" -> "yes", "NO" -> "no"
        market_response = await kalshi_client.get_market(position.market_id)
        market_data = market_response.get("market", {})
        token_id = _get_position_token_id(market_data, position.side)
        if not token_id:
            logger.error(f"[FAIL] Missing token id for sell order on {position.market_id}")
            return False
        
        order_params = {
            "ticker": token_id,
            "client_order_id": client_order_id,
            "side": side,
            "action": "sell",
            "count": position.quantity,
            "type_": "limit"
        }
        
        # Add the appropriate price parameter based on what we're selling
        if side == "yes":
            order_params["yes_price"] = limit_price_cents
        else:
            order_params["no_price"] = limit_price_cents
        
        logger.info(f"[TARGET] Placing SELL LIMIT order: {position.quantity} {side.upper()} at {limit_price_cents}¢ for {position.market_id}")
        
        # Place the sell limit order
        response = await kalshi_client.place_order(**order_params)
        
        if response and 'order' in response:
            order_id = response['order'].get('order_id', client_order_id)
            
            # Record the sell order in the database (we could add a sell_orders table if needed)
            logger.info(f"[OK] SELL LIMIT ORDER placed successfully! Order ID: {order_id}")
            logger.info(f"   Market: {position.market_id}")
            logger.info(f"   Side: {side.upper()} (selling {position.quantity} shares)")
            logger.info(f"   Limit Price: {limit_price_cents}¢")
            logger.info(f"   Expected Proceeds: ${limit_price * position.quantity:.2f}")
            
            return True
        else:
            logger.error(f"[FAIL] Failed to place sell limit order: {response}")
            return False
            
    except Exception as e:
        logger.error(f"[FAIL] Error placing sell limit order for {position.market_id}: {e}")
        return False


async def place_profit_taking_orders(
    db_manager: DatabaseManager,
    kalshi_client: KalshiClient,
    profit_threshold: float = 0.25  # 25% profit target
) -> Dict[str, int]:
    """
    Place sell limit orders for positions that have reached profit targets.
    
    Args:
        db_manager: Database manager
        kalshi_client: Polymarket API client
        profit_threshold: Minimum profit percentage to trigger sell order
    
    Returns:
        Dictionary with results: {'orders_placed': int, 'positions_processed': int}
    """
    logger = get_trading_logger("profit_taking")
    
    results = {'orders_placed': 0, 'positions_processed': 0}
    
    try:
        # Get all open live positions
        positions = await db_manager.get_open_live_positions()
        
        if not positions:
            logger.info("No open positions to process for profit taking")
            return results
        
        logger.info(f"[STATS] Checking {len(positions)} positions for profit-taking opportunities")
        
        for position in positions:
            try:
                results['positions_processed'] += 1
                
                # Get current market data
                market_response = await kalshi_client.get_market(position.market_id)
                market_data = market_response.get('market', {})
                
                if not market_data:
                    logger.warning(f"Could not get market data for {position.market_id}")
                    continue
                
                # FIX: get token_id from market data
                yes_token_id = market_data.get("yes_token_id", "")
                no_token_id = market_data.get("no_token_id", "")
                token_id = yes_token_id if position.side.upper() == "YES" else no_token_id

                # FIX: fetch via token_id
                current_price = None
                if token_id:
                    current_price = await kalshi_client.get_token_price(token_id)

                # Fallback: market-level price field
                if not current_price or current_price <= 0.001:
                    field = "yes_price" if position.side.upper() == "YES" else "no_price"
                    raw = _normalize_market_price(market_data.get(field))
                    current_price = raw if raw and raw > 0.001 else None

                # Guard: skip if still no valid price
                if not current_price or current_price <= 0.001:
                    logger.warning(
                        f"Cannot get valid price for {position.market_id} "
                        f"(token={token_id[:16] if token_id else 'MISSING'}) - "
                        f"skipping profit/stop check"
                    )
                    continue
                
                # Calculate current profit
                if current_price > 0:
                    profit_pct = (
                        (current_price - position.entry_price) / position.entry_price
                        if position.side == "YES"
                        else (position.entry_price - current_price) / position.entry_price
                    )
                    unrealized_pnl = _calculate_position_pnl(
                        position.entry_price, current_price, position.quantity, position.side
                    )
                    
                    logger.info(
                        f"{position.market_id} | Entry: {position.entry_price:.3f} | "
                        f"Current: {current_price:.3f} | Side: {position.side}"
                    )
                    logger.debug(f"Position {position.market_id}: Entry=${position.entry_price:.3f}, Current=${current_price:.3f}, Profit={profit_pct:.1%}, PnL=${unrealized_pnl:.2f}")
                    
                    # Check if we should place a profit-taking sell order
                    if profit_pct >= profit_threshold:
                        # Calculate sell limit price (slightly below current to ensure execution)
                        sell_price = current_price * 0.98  # 2% below current price for quick execution
                        if sell_price <= 0:
                            logger.warning(f"Invalid exit price for {position.market_id}: {sell_price}, skipping.")
                            continue
                        
                        logger.info(f"[MONEY] PROFIT TARGET HIT: {position.market_id} - {profit_pct:.1%} profit (${unrealized_pnl:.2f})")
                        
                        # Place sell limit order
                        success = await place_sell_limit_order(
                            position=position,
                            limit_price=sell_price,
                            db_manager=db_manager,
                            kalshi_client=kalshi_client
                        )
                        
                        if success:
                            results['orders_placed'] += 1
                            logger.info(f"[OK] Profit-taking order placed for {position.market_id}")
                        else:
                            logger.error(f"[FAIL] Failed to place profit-taking order for {position.market_id}")
                
            except Exception as e:
                logger.error(f"Error processing position {position.market_id} for profit taking: {e}")
                continue
        
        logger.info(f"[TARGET] Profit-taking summary: {results['orders_placed']} orders placed from {results['positions_processed']} positions")
        return results
        
    except Exception as e:
        logger.error(f"Error in profit-taking order placement: {e}")
        return results


async def place_stop_loss_orders(
    db_manager: DatabaseManager,
    kalshi_client: KalshiClient,
    stop_loss_threshold: float = -0.10  # 10% stop loss
) -> Dict[str, int]:
    """
    Place sell limit orders for positions that need stop-loss protection.
    
    Args:
        db_manager: Database manager
        kalshi_client: Polymarket API client
        stop_loss_threshold: Maximum loss percentage before triggering stop loss
    
    Returns:
        Dictionary with results: {'orders_placed': int, 'positions_processed': int}
    """
    logger = get_trading_logger("stop_loss_orders")
    
    results = {'orders_placed': 0, 'positions_processed': 0}
    
    try:
        # Get all open live positions
        positions = await db_manager.get_open_live_positions()
        
        if not positions:
            logger.info("No open positions to process for stop-loss orders")
            return results
        
        logger.info(f"[SHIELD] Checking {len(positions)} positions for stop-loss protection")
        
        for position in positions:
            try:
                results['positions_processed'] += 1
                
                # Get current market data
                market_response = await kalshi_client.get_market(position.market_id)
                market_data = market_response.get('market', {})
                
                if not market_data:
                    logger.warning(f"Could not get market data for {position.market_id}")
                    continue
                
                # FIX: get token_id from market data
                yes_token_id = market_data.get("yes_token_id", "")
                no_token_id = market_data.get("no_token_id", "")
                token_id = yes_token_id if position.side.upper() == "YES" else no_token_id

                # FIX: fetch via token_id
                current_price = None
                if token_id:
                    current_price = await kalshi_client.get_token_price(token_id)

                # Fallback: market-level price field
                if not current_price or current_price <= 0.001:
                    field = "yes_price" if position.side.upper() == "YES" else "no_price"
                    raw = _normalize_market_price(market_data.get(field))
                    current_price = raw if raw and raw > 0.001 else None

                # Guard: skip if still no valid price
                if not current_price or current_price <= 0.001:
                    logger.warning(
                        f"Cannot get valid price for {position.market_id} "
                        f"(token={token_id[:16] if token_id else 'MISSING'}) - "
                        f"skipping profit/stop check"
                    )
                    continue
                
                # Calculate current loss
                if current_price > 0:
                    loss_pct = (
                        (current_price - position.entry_price) / position.entry_price
                        if position.side == "YES"
                        else (position.entry_price - current_price) / position.entry_price
                    )
                    unrealized_pnl = _calculate_position_pnl(
                        position.entry_price, current_price, position.quantity, position.side
                    )
                    logger.info(
                        f"{position.market_id} | Entry: {position.entry_price:.3f} | "
                        f"Current: {current_price:.3f} | Side: {position.side}"
                    )
                    
                    # Check if we need stop-loss protection
                    if loss_pct <= stop_loss_threshold:  # Negative loss percentage
                        # Calculate stop-loss sell price
                        stop_price = position.entry_price * (1 + stop_loss_threshold * 1.1)  # Slightly more aggressive
                        if stop_price <= 0:
                            logger.warning(f"Invalid exit price for {position.market_id}: {stop_price}, skipping.")
                            continue
                        stop_price = max(0.01, stop_price)  # Ensure price is at least 1¢
                        
                        logger.info(f"[SHIELD] STOP LOSS TRIGGERED: {position.market_id} - {loss_pct:.1%} loss (${unrealized_pnl:.2f})")
                        
                        # Place stop-loss sell order
                        success = await place_sell_limit_order(
                            position=position,
                            limit_price=stop_price,
                            db_manager=db_manager,
                            kalshi_client=kalshi_client
                        )
                        
                        if success:
                            results['orders_placed'] += 1
                            logger.info(f"[OK] Stop-loss order placed for {position.market_id}")
                        else:
                            logger.error(f"[FAIL] Failed to place stop-loss order for {position.market_id}")
                
            except Exception as e:
                logger.error(f"Error processing position {position.market_id} for stop loss: {e}")
                continue
        
        logger.info(f"[SHIELD] Stop-loss summary: {results['orders_placed']} orders placed from {results['positions_processed']} positions")
        return results
        
    except Exception as e:
        logger.error(f"Error in stop-loss order placement: {e}")
        return results
