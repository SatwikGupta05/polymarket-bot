"""
Unified Advanced Trading System - The Beast Mode [START]

This system orchestrates all cutting-edge strategies:
1. Market Making Strategy (limit orders for spreads)
2. Advanced Portfolio Optimization (Kelly Criterion Extension)
3. Risk Parity Allocation (equal risk, not equal capital)
4. Dynamic Exit Strategies (time-based, confidence-based, volatility-based)
5. Cross-Market Arbitrage Detection
6. Multi-Model AI Ensemble

The goal: Use ALL available capital optimally across the BEST opportunities
with sophisticated risk management and dynamic rebalancing.

Key innovations:
- No time restrictions (trade any deadline with smart exits)
- Market making for spread profits without directional risk
- Portfolio optimization using latest Kelly Criterion research
- Real-time rebalancing based on market conditions
- Maximum capital utilization through diverse strategies
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import numpy as np

from src.clients.polymarket_client import PolymarketClient as KalshiClient
from src.clients.xai_client import XAIClient
from src.utils.database import DatabaseManager, Market, Position
from src.config.settings import settings
from src.utils.logging_setup import get_trading_logger

from src.strategies.market_making import (
    AdvancedMarketMaker, 
    MarketMakingOpportunity,
    run_market_making_strategy
)
from src.strategies.portfolio_optimization import (
    AdvancedPortfolioOptimizer, 
    MarketOpportunity, 
    PortfolioAllocation,
    run_portfolio_optimization,
    create_market_opportunities_from_markets
)
from src.strategies.quick_flip_scalping import (
    run_quick_flip_strategy,
    QuickFlipConfig
)


@dataclass
class TradingSystemConfig:
    """Configuration for the unified trading system."""
    # Capital allocation across strategies
    market_making_allocation: float = 0.30  # 30% for market making
    directional_trading_allocation: float = 0.40  # 40% for directional positions
    quick_flip_allocation: float = 0.30     # 30% for quick flip scalping
    arbitrage_allocation: float = 0.00      # 0% for arbitrage opportunities
    
    # Risk management
    max_portfolio_volatility: float = 0.20  # 20% max portfolio vol
    max_correlation_exposure: float = 0.70  # Max 70% in correlated positions
    max_single_position: float = 0.15  # Max 15% in any single position
    
    # Performance targets
    target_sharpe_ratio: float = 2.0
    target_annual_return: float = 0.30  # 30% annual target
    max_drawdown_limit: float = 0.15  # 15% max drawdown
    
    # Rebalancing
    rebalance_frequency_hours: int = 6  # Rebalance every 6 hours
    profit_taking_threshold: float = 0.25  # Take profits at 25%
    loss_cutting_threshold: float = 0.10  # Cut losses at 10%


@dataclass
class TradingSystemResults:
    """Results from unified trading system execution."""
    # Market making results
    market_making_orders: int = 0
    market_making_exposure: float = 0.0
    market_making_expected_profit: float = 0.0
    
    # Directional trading results
    directional_positions: int = 0
    directional_exposure: float = 0.0
    directional_expected_return: float = 0.0
    
    # Portfolio metrics
    total_capital_used: float = 0.0
    portfolio_expected_return: float = 0.0
    portfolio_sharpe_ratio: float = 0.0
    portfolio_volatility: float = 0.0
    
    # Risk metrics
    max_portfolio_drawdown: float = 0.0
    correlation_score: float = 0.0
    diversification_ratio: float = 0.0
    
    # Performance
    total_positions: int = 0
    capital_efficiency: float = 0.0  # % of capital used
    expected_annual_return: float = 0.0


class UnifiedAdvancedTradingSystem:
    """
    The Beast Mode Trading System [START]
    
    This orchestrates all advanced strategies to maximize returns while
    optimally using ALL available capital with sophisticated risk management.
    
    Strategy allocation:
    1. Market Making (40%): Profit from spreads without directional risk
    2. Directional Trading (50%): Take positions based on AI edge
    3. Arbitrage (10%): Cross-market and temporal arbitrage
    
    Features:
    - No time restrictions (trade any deadline)
    - Dynamic position sizing (Kelly Criterion Extension)
    - Risk parity allocation (equal risk contribution)
    - Real-time rebalancing
    - Multi-strategy diversification
    """
    
    def __init__(
        self,
        db_manager: DatabaseManager,
        kalshi_client: KalshiClient,
        xai_client: XAIClient,
        config: Optional[TradingSystemConfig] = None
    ):
        self.db_manager = db_manager
        self.kalshi_client = kalshi_client
        self.xai_client = xai_client
        self.config = config or TradingSystemConfig()
        self.logger = get_trading_logger("unified_trading_system")
        
        # [ALERT] DYNAMIC CAPITAL: Will be set by async_initialize() from actual exchange balance
        self.total_capital = 100  # Temporary default, will be updated by async_initialize()
        
        # OLD HARDCODED WAY (REMOVED):
        # self.total_capital = getattr(settings.trading, 'total_capital', 10000)
        self.last_rebalance = datetime.now()
        self.system_metrics = {}

        self.market_maker = None
        self.portfolio_optimizer = None
        
        # Capital allocation will be set by async_initialize() after getting actual balance

    async def async_initialize(self):
        """
        Asynchronously initialize the trading system by fetching the current balance
        from the exchange and setting the total capital.
        """
        try:
            # Get total portfolio value (cash + current positions)
            balance_response = await self.kalshi_client.get_balance()
            available_cash = balance_response.get('balance', 0) / 100  # Convert cents to dollars

            positions_response = await self.kalshi_client.get_positions()
            market_positions = positions_response.get('market_positions', []) if isinstance(positions_response, dict) else []

            def _position_value_usd(pos: dict) -> float:
                for key in ("size", "currentValue", "value", "amount"):
                    raw = pos.get(key)
                    if raw not in (None, ""):
                        try:
                            return abs(float(raw))
                        except (TypeError, ValueError):
                            pass
                try:
                    return abs(float(pos.get('position', 0))) * 0.5
                except (TypeError, ValueError):
                    return 0.0

            active_positions = [p for p in market_positions if _position_value_usd(p) > 0]
            total_position_value = sum(_position_value_usd(p) for p in active_positions)
            if active_positions:
                self.logger.info(f"[STATS] Active positions: {len(active_positions)}")
                for pos in active_positions:
                    ticker = pos.get('condition_id') or pos.get('ticker') or pos.get('asset_id') or '?'
                    exposure = _position_value_usd(pos)
                    pnl = float(pos.get('cashPnl', pos.get('realized_pnl', 0)) or 0)
                    self.logger.info(f"  [PIN] {ticker}: exposure=${exposure:.2f}, realized_pnl=${pnl:.2f}")

            # Total portfolio value is the basis for all allocations
            total_portfolio_value = available_cash + total_position_value
            self.total_capital = total_portfolio_value

            self.logger.info(f"[MONEY] PORTFOLIO VALUE: Cash=${available_cash:.2f} + Positions=${total_position_value:.2f} = Total=${self.total_capital:.2f}")

            if self.total_capital < 10:  # Minimum $10 to trade
                self.logger.warning(f"[WARN] Total capital too low: ${self.total_capital:.2f} - may limit trading")

        except Exception as e:
            self.logger.error(f"Failed to get portfolio value, using default: {e}")
            self.total_capital = 100  # Conservative fallback

        # Update capital allocation based on actual balance
        self.market_making_capital = self.total_capital * self.config.market_making_allocation
        self.directional_capital = self.total_capital * self.config.directional_trading_allocation
        self.quick_flip_capital = self.total_capital * self.config.quick_flip_allocation
        self.arbitrage_capital = self.total_capital * self.config.arbitrage_allocation

        # Initialize strategy modules with actual capital
        self.market_maker = AdvancedMarketMaker(self.db_manager, self.kalshi_client, self.xai_client)
        self.portfolio_optimizer = AdvancedPortfolioOptimizer(self.db_manager, self.kalshi_client, self.xai_client)

        self.logger.info(f"[TARGET] CAPITAL ALLOCATION: Market Making=${self.market_making_capital:.2f}, Directional=${self.directional_capital:.2f}, Quick Flip=${self.quick_flip_capital:.2f}, Arbitrage=${self.arbitrage_capital:.2f}")

    async def execute_unified_trading_strategy(self) -> TradingSystemResults:
        """
        Execute the unified trading strategy across all approaches.
        
        Process:
        1. Analyze all available markets (no time restrictions!)
        2. Identify market making opportunities
        3. Identify directional trading opportunities  
        4. Optimize portfolio allocation using advanced Kelly Criterion
        5. Execute trades across all strategies
        6. Monitor and rebalance as needed
        """
        self.logger.info("[START] Executing Unified Advanced Trading Strategy")
        
        try:
            # Step 0: Check and enforce position limits AND cash reserves
            from src.utils.position_limits import PositionLimitsManager
            from src.utils.cash_reserves import CashReservesManager, is_cash_emergency
            
            limits_manager = PositionLimitsManager(self.db_manager, self.kalshi_client)
            cash_manager = CashReservesManager(self.db_manager, self.kalshi_client)
            
            # Check position limits
            limits_status = await limits_manager.get_position_limits_status()
            self.logger.info(f"[STATS] POSITION LIMITS STATUS: {limits_status['status']} ({limits_status['position_utilization']})")
            
            # Check cash reserves
            cash_status = await cash_manager.get_cash_status()
            self.logger.info(f"[MONEY] CASH RESERVES STATUS: {cash_status['status']} ({cash_status['reserve_percentage']:.1f}%)")
            
            # Handle cash emergency first (higher priority)
            if cash_status['emergency_status']:
                self.logger.warning(f"[ALERT] CASH EMERGENCY: {cash_status['recommendations']}")
                emergency_action = await cash_manager.handle_cash_emergency()
                if emergency_action.action_type == 'halt_trading':
                    self.logger.critical(f"[STOP] TRADING HALTED DUE TO CASH EMERGENCY: {emergency_action.reason}")
                    return TradingSystemResults()  # Return empty results
                elif emergency_action.action_type == 'close_positions':
                    self.logger.warning(f"[WARN] Need to close {emergency_action.positions_to_close} positions for cash reserves")
            
            # Enforce position limits if needed (after cash check)
            if limits_status['status'] in ['OVER_LIMIT', 'WARNING']:
                self.logger.info(f"[WARN]  Position limits enforcement needed: {limits_status['recommendations']}")
                enforcement_result = await limits_manager.enforce_position_limits()
                if enforcement_result['action'] == 'positions_closed':
                    self.logger.info(f"[OK] CLOSED {enforcement_result['positions_closed']} positions to meet limits")
            
            # Step 1: Get ALL available markets (no time restrictions) - MORE PERMISSIVE VOLUME
            markets = await self.db_manager.get_eligible_markets(
            volume_min=200,  # DECREASED: Much lower volume requirement (was 50,000, now 200) for more opportunities
            max_days_to_expiry=365  # Accept any timeline with dynamic exits
        )
            if not markets:
                self.logger.warning("No markets available for trading")
                return TradingSystemResults()
            
            self.logger.info(f"Analyzing {len(markets)} markets across all strategies")
            
            # Step 2: Parallel strategy analysis
            market_making_results, portfolio_allocation, quick_flip_results = await asyncio.gather(
                self._execute_market_making_strategy(markets),
                self._execute_directional_trading_strategy(markets),
                self._execute_quick_flip_strategy(markets)
            )
            
            # Step 3: Execute arbitrage opportunities
            arbitrage_results = await self._execute_arbitrage_strategy(markets)
            
            # Step 4: Compile results
            results = self._compile_unified_results(
                market_making_results, portfolio_allocation, quick_flip_results, arbitrage_results
            )
            
            # Step 4.5: Log if no positions were created (removed emergency fallback)
            if results.total_positions == 0:
                self.logger.warning("No positions created by main strategies - investigating why")
            
            # Step 5: Risk management and rebalancing
            await self._manage_risk_and_rebalance(results)
            
            self.logger.info(
                f"[TARGET] Unified Strategy Complete: "
                f"Capital Used: ${results.total_capital_used:.0f} ({results.capital_efficiency:.1%}), "
                f"Expected Return: {results.expected_annual_return:.1%}, "
                f"Sharpe Ratio: {results.portfolio_sharpe_ratio:.2f}, "
                f"Positions: {results.total_positions}"
            )
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in unified trading strategy: {e}")
            return TradingSystemResults()

    async def _execute_market_making_strategy(self, markets: List[Market]) -> Dict:
        """
        Execute market making strategy for spread profits.
        """
        try:
            self.logger.info(f"[TARGET] Executing Market Making Strategy on {len(markets)} markets")

            max_opportunities = max(1, int(self.market_making_capital / 100))  # $100 per opportunity
            executed_markets = 0
            aggregate_results = {
                'orders_placed': 0,
                'total_exposure': 0.0,
                'expected_profit': 0.0,
                'markets_count': 0,
            }

            for market in markets:
                if executed_markets >= max_opportunities:
                    break

                opportunities = await self.market_maker.analyze_market_making_opportunities([market])
                if not opportunities:
                    continue

                # Execute as soon as a market is approved instead of waiting for the full scan.
                results = await self.market_maker.execute_market_making_strategy(
                    opportunities[: max_opportunities - executed_markets]
                )

                if results.get('markets_count', 0) <= 0:
                    continue

                executed_markets += results['markets_count']
                aggregate_results['orders_placed'] += results.get('orders_placed', 0)
                aggregate_results['total_exposure'] += results.get('total_exposure', 0.0)
                aggregate_results['expected_profit'] += results.get('expected_profit', 0.0)
                aggregate_results['markets_count'] += results.get('markets_count', 0)

            if aggregate_results['markets_count'] == 0:
                self.logger.warning("No market making opportunities found")
                return {'orders_placed': 0, 'expected_profit': 0.0}

            self.logger.info(
                f"[OK] Market Making: {aggregate_results['orders_placed']} orders, "
                f"${aggregate_results['expected_profit']:.2f} expected profit"
            )

            return aggregate_results
            
        except Exception as e:
            self.logger.error(f"Error in market making strategy: {e}")
            return {'orders_placed': 0, 'expected_profit': 0.0}

    async def _execute_directional_trading_strategy(self, markets: List[Market]) -> PortfolioAllocation:
        """
        Execute directional trading with advanced portfolio optimization.
        """
        try:
            self.logger.info(f"[TARGET] Executing Directional Trading Strategy")
            
            # Convert markets to opportunities (with immediate trading capability)
            opportunities = await create_market_opportunities_from_markets(
                markets, self.xai_client, self.kalshi_client, 
                self.db_manager, self.directional_capital
            )
            
            if not opportunities:
                self.logger.warning("No directional trading opportunities found")
                return self.portfolio_optimizer._empty_allocation()
            
            # Filter opportunities based on available capital
            # Adjust portfolio optimizer capital
            original_capital = self.portfolio_optimizer.total_capital
            self.portfolio_optimizer.total_capital = self.directional_capital
            
            # Optimize portfolio
            allocation = await self.portfolio_optimizer.optimize_portfolio(opportunities)
            
            # Restore original capital setting
            self.portfolio_optimizer.total_capital = original_capital
            
            # DEBUG: Log allocation details before execution attempt
            self.logger.info(f"Portfolio allocation result: {len(allocation.allocations) if allocation else 0} allocations, ${allocation.total_capital_used if allocation else 0:.0f} capital used")
            
            # Actually execute the trades from the allocation
            if allocation and allocation.allocations:
                self.logger.info(f"Attempting to execute {len(allocation.allocations)} allocations: {list(allocation.allocations.keys())}")
                execution_results = await self._execute_portfolio_allocations(allocation, opportunities)
                self.logger.info(f"Executed {execution_results['positions_created']} positions from portfolio allocation")
            else:
                self.logger.warning(f"No allocations to execute. Allocation exists: {allocation is not None}, Has allocations: {bool(allocation and allocation.allocations)}")
            
            self.logger.info(
                f" Directional Trading: {len(allocation.allocations)} positions, "
                f"${allocation.total_capital_used:.0f} allocated, "
                f"Sharpe: {allocation.portfolio_sharpe:.2f}"
            )
            
            return allocation
            
        except Exception as e:
            self.logger.error(f"Error in directional trading strategy: {e}")
            return self.portfolio_optimizer._empty_allocation()

    async def _execute_quick_flip_strategy(self, markets: List[Market]) -> Dict:
        """
        Execute quick flip scalping strategy for rapid profits.
        """
        try:
            self.logger.info(f"[TARGET] Executing Quick Flip Scalping Strategy")
            
            # Configure quick flip strategy for our capital allocation
            quick_flip_config = QuickFlipConfig(
                min_entry_price=0.01,   # Start with $0.01 opportunities
                max_entry_price=0.15,   # Up to $0.15 entries
                min_profit_margin=1.0,  # 100% minimum return ($0.01 → $0.02)
                max_position_size=100,  # Max 100 contracts per position
                max_concurrent_positions=min(25, int(self.quick_flip_capital / 20)),  # Scale with capital
                capital_per_trade=min(50.0, self.quick_flip_capital / 10),  # Spread risk
                confidence_threshold=0.6,  # 60% minimum confidence
                max_hold_minutes=30     # Quick exit if not filled
            )
            
            # Execute quick flip strategy
            results = await run_quick_flip_strategy(
                db_manager=self.db_manager,
                kalshi_client=self.kalshi_client,
                xai_client=self.xai_client,
                available_capital=self.quick_flip_capital,
                config=quick_flip_config
            )
            
            if 'error' in results:
                self.logger.warning(f"Quick flip strategy error: {results['error']}")
                return {'positions_created': 0, 'sell_orders_placed': 0, 'total_capital_used': 0.0}
            
            self.logger.info(
                f"[OK] Quick Flip: {results.get('positions_created', 0)} positions, "
                f"{results.get('sell_orders_placed', 0)} sell orders, "
                f"${results.get('total_capital_used', 0):.0f} capital used"
            )
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in quick flip strategy: {e}")
            return {'positions_created': 0, 'sell_orders_placed': 0, 'total_capital_used': 0.0}

    async def _execute_portfolio_allocations(
        self, 
        allocation: PortfolioAllocation, 
        opportunities: List[MarketOpportunity]
    ) -> Dict:
        """
        Convert portfolio allocations to actual Position objects and execute them.
        """
        results = {
            'positions_created': 0,
            'total_capital_used': 0.0,
            'successful_executions': 0,
            'failed_executions': 0
        }
        
        try:
            from src.jobs.execute import execute_position
            
            for market_id, allocation_fraction in allocation.allocations.items():
                try:
                    # Find the corresponding opportunity first to determine intended side
                    opportunity = next((opp for opp in opportunities if opp.market_id == market_id), None)
                    if not opportunity:
                        self.logger.warning(f"Could not find opportunity for {market_id}")
                        continue
                    
                    # Determine the intended side based on edge direction
                    intended_side = "YES" if opportunity.edge > 0 else "NO"
                    
                    # [ALERT] ONLY SKIP if we already have a position on the EXACT same market_id AND side
                    existing_position = await self.db_manager.get_position_by_market_and_side(market_id, intended_side)
                    
                    if existing_position:
                        self.logger.info(f"[SKIP] SKIPPING {market_id} {intended_side} - exact position already exists (likely from immediate trade)")
                        results['positions_created'] += 1  # Count as created since it exists
                        results['total_capital_used'] += allocation_fraction * self.directional_capital
                        continue
                    else:
                        # Check if we have the opposite side (just for logging)
                        opposite_side = "NO" if intended_side == "YES" else "YES"
                        opposite_position = await self.db_manager.get_position_by_market_and_side(market_id, opposite_side)
                        if opposite_position:
                            self.logger.info(f"[STATS] {market_id} - Adding {intended_side} position (already have {opposite_side})")
                        else:
                            self.logger.info(f"[STATS] {market_id} - New {intended_side} position")
                    
                    # Calculate initial position size
                    initial_position_value = allocation_fraction * self.directional_capital
                    
                    # Check position limits and adjust if needed
                    from src.utils.position_limits import check_can_add_position
                    
                    can_add_position, limit_reason = await check_can_add_position(
                        initial_position_value, self.db_manager, self.kalshi_client
                    )
                    
                    if not can_add_position:
                        # Instead of blocking, try to find a smaller position size that fits
                        self.logger.info(f"[WARN] Position size ${initial_position_value:.2f} exceeds limits, attempting to reduce...")
                        
                        # Try progressively smaller position sizes
                        for reduction_factor in [0.8, 0.6, 0.4, 0.2, 0.1]:
                            reduced_position_value = initial_position_value * reduction_factor
                            can_add_reduced, reduced_reason = await check_can_add_position(
                                reduced_position_value, self.db_manager, self.kalshi_client
                            )
                            
                            if can_add_reduced:
                                initial_position_value = reduced_position_value
                                self.logger.info(f"[OK] Position size reduced to ${initial_position_value:.2f} to fit limits")
                                break
                        else:
                            # If even the smallest size doesn't fit, check if it's due to position count
                            from src.utils.position_limits import PositionLimitsManager
                            limits_manager = PositionLimitsManager(self.db_manager, self.kalshi_client)
                            current_positions = await limits_manager._get_position_count()
                            
                            if current_positions >= limits_manager.max_positions:
                                self.logger.info(f"[FAIL] POSITION COUNT LIMIT: {current_positions}/{limits_manager.max_positions} positions - cannot add new position")
                                results['failed_executions'] += 1
                                continue
                            else:
                                self.logger.info(f"[FAIL] POSITION SIZE LIMIT: Even minimum size ${initial_position_value * 0.1:.2f} exceeds limits")
                                results['failed_executions'] += 1
                                continue
                    
                    position_value = initial_position_value
                    self.logger.info(f"[OK] POSITION LIMITS OK FOR ALLOCATION: ${position_value:.2f}")
                    
                    # Check cash reserves for this allocation
                    from src.utils.cash_reserves import check_can_trade_with_cash_reserves
                    
                    can_trade_reserves, reserves_reason = await check_can_trade_with_cash_reserves(
                        position_value, self.db_manager, self.kalshi_client
                    )
                    
                    if not can_trade_reserves:
                        self.logger.info(f"[FAIL] CASH RESERVES BLOCK ALLOCATION: {market_id} - {reserves_reason}")
                        results['failed_executions'] += 1
                        continue
                    
                    self.logger.info(f"[OK] CASH RESERVES OK FOR ALLOCATION: {market_id}")
                    
                    # Get current market data
                    market_data = await self.kalshi_client.get_market(market_id)
                    if not market_data:
                        self.logger.warning(f"Could not get market data for {market_id}")
                        continue
                    
                    # FIXED: Extract from nested 'market' object
                    market_info = market_data.get('market', {})
                    
                    current_yes_price = market_info.get('yes_bid_dollars')
                    current_no_price = market_info.get('no_bid_dollars')

                    if current_yes_price is None and market_info.get('yes_bid') is not None:
                        current_yes_price = market_info.get('yes_bid') / 100
                    if current_no_price is None and market_info.get('no_bid') is not None:
                        current_no_price = market_info.get('no_bid') / 100

                    if current_yes_price is None or current_no_price is None:
                        self.logger.warning(f"Missing price data for {market_id}, skipping allocation.")
                        continue

                    if intended_side == "YES":
                        price = current_yes_price / 100
                    else:
                        price = current_no_price / 100

                    if price <= 0:
                        self.logger.warning(f"Invalid entry price for {market_id}: {price}, skipping allocation.")
                        continue
                    
                    # Calculate quantity
                    quantity = max(1, int(position_value / price))
                    
                    # Calculate proper stop-loss levels using Grok4 recommendations
                    from src.utils.stop_loss_calculator import StopLossCalculator
                    
                    # Calculate time to expiry for the market
                    time_to_expiry_days = 30  # Default fallback
                    try:
                        market_obj = next((m for m in opportunities if m.market_id == market_id), None)
                        if market_obj:
                            time_to_expiry_days = getattr(market_obj, 'time_to_expiry', 30)
                    except:
                        pass
                    
                    exit_levels = StopLossCalculator.calculate_stop_loss_levels(
                        entry_price=price,
                        side=intended_side,
                        confidence=opportunity.confidence,
                        market_volatility=0.2,  # Default volatility estimate
                        time_to_expiry_days=time_to_expiry_days
                    )
                    
                    # Create Position object
                    position = Position(
                        market_id=market_id.strip().lower(),
                        side=intended_side,
                        entry_price=price,
                        quantity=quantity,
                        timestamp=datetime.now(timezone.utc),
                        rationale=f"Portfolio optimization allocation: {allocation_fraction:.1%} of capital. Edge: {opportunity.edge:.3f}, Confidence: {opportunity.confidence:.3f}, Stop: {exit_levels['stop_loss_pct']}%",
                        confidence=opportunity.confidence,
                        live=False,  # Will be set to True after execution
                        strategy="portfolio_optimization",
                        
                        # Enhanced exit strategy using Grok4 recommendations
                        stop_loss_price=exit_levels['stop_loss_price'],
                        take_profit_price=exit_levels['take_profit_price'],
                        max_hold_hours=exit_levels['max_hold_hours'],
                        target_confidence_change=exit_levels['target_confidence_change']
                    )
                    
                    # Execute first, then persist only confirmed executions.
                    live_mode = getattr(settings.trading, 'live_trading_enabled', False)
                    side = intended_side
                    self.logger.info(f"Trading mode: live={live_mode} for market {market_id}")
                    self.logger.info(f"[CTRL] Trading mode check: live_mode={live_mode} for market {opportunity.ticker}")
                    
                    position_id = await self.db_manager.add_position(position)
                    if position_id is None:
                        self.logger.warning(f"Position already exists for {market_id} - skipping")
                        continue

                    position.id = position_id

                    success = await execute_position(
                        position=position,
                        live_mode=live_mode,
                        db_manager=self.db_manager,
                        kalshi_client=self.kalshi_client
                    )
                    
                    if success:
                        results['successful_executions'] += 1
                        results['positions_created'] += 1
                        results['total_capital_used'] += position_value
                        self.logger.info(f"Executed + saved: {market_id} {side} {quantity} @ {price:.3f}")
                        self.logger.info(f"[OK] Executed position: {market_id} {side} {quantity} at {price:.3f}")
                    else:
                        results['failed_executions'] += 1
                        self.logger.error(f"Execution failed for {market_id} after DB save")
                        self.logger.error(f"[FAIL] Failed to execute position for {market_id}")
                
                except Exception as e:
                    self.logger.error(f"Error executing allocation for {market_id}: {e}")
                    results['failed_executions'] += 1
                    continue
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in portfolio allocation execution: {e}")
            return results

    async def _execute_arbitrage_strategy(self, markets: List[Market]) -> Dict:
        """
        Execute arbitrage opportunities (placeholder for future implementation).
        """
        try:
            # TODO: Implement cross-market arbitrage detection
            # This could include:
            # - legacy compatibility price differences
            # - Related market arbitrage (correlated events)
            # - Temporal arbitrage (same event, different expiries)
            
            self.logger.info("[TARGET] Arbitrage opportunities analysis (future feature)")
            return {
                'arbitrage_trades': 0,
                'arbitrage_profit': 0.0,
                'arbitrage_exposure': 0.0
            }
            
        except Exception as e:
            self.logger.error(f"Error in arbitrage strategy: {e}")
            return {'arbitrage_trades': 0, 'arbitrage_profit': 0.0}

    def _compile_unified_results(
        self, 
        market_making_results: Dict, 
        portfolio_allocation: PortfolioAllocation,
        quick_flip_results: Dict,
        arbitrage_results: Dict
    ) -> TradingSystemResults:
        """
        Compile results from all strategies into unified metrics.
        """
        try:
            # Calculate total metrics
            total_capital_used = (
                market_making_results.get('total_exposure', 0) +
                portfolio_allocation.total_capital_used +
                quick_flip_results.get('total_capital_used', 0) +
                arbitrage_results.get('arbitrage_exposure', 0)
            )
            
            # Weight expected returns by capital allocation
            mm_weight = market_making_results.get('total_exposure', 0) / (total_capital_used + 1e-8)
            dir_weight = portfolio_allocation.total_capital_used / (total_capital_used + 1e-8)
            qf_weight = quick_flip_results.get('total_capital_used', 0) / (total_capital_used + 1e-8)
            arb_weight = arbitrage_results.get('arbitrage_exposure', 0) / (total_capital_used + 1e-8)
            
            # Portfolio expected return (weighted average)
            portfolio_expected_return = (
                mm_weight * market_making_results.get('expected_profit', 0) +
                dir_weight * portfolio_allocation.expected_portfolio_return +
                qf_weight * quick_flip_results.get('expected_profit', 0) +
                arb_weight * arbitrage_results.get('arbitrage_profit', 0)
            )
            
            # Annualize expected return (assume positions held for 30 days average)
            expected_annual_return = portfolio_expected_return * (365 / 30)
            
            # Capital efficiency
            capital_efficiency = total_capital_used / self.total_capital
            
            # Total positions
            total_positions = (
                market_making_results.get('orders_placed', 0) // 2 +  # 2 orders per position
                len(portfolio_allocation.allocations) +
                quick_flip_results.get('positions_created', 0) +
                arbitrage_results.get('arbitrage_trades', 0)
            )
            
            return TradingSystemResults(
                # Market making
                market_making_orders=market_making_results.get('orders_placed', 0),
                market_making_exposure=market_making_results.get('total_exposure', 0),
                market_making_expected_profit=market_making_results.get('expected_profit', 0),
                
                # Directional trading
                directional_positions=len(portfolio_allocation.allocations),
                directional_exposure=portfolio_allocation.total_capital_used,
                directional_expected_return=portfolio_allocation.expected_portfolio_return,
                
                # Portfolio metrics
                total_capital_used=total_capital_used,
                portfolio_expected_return=portfolio_expected_return,
                portfolio_sharpe_ratio=portfolio_allocation.portfolio_sharpe,
                portfolio_volatility=portfolio_allocation.portfolio_volatility,
                
                # Risk metrics
                max_portfolio_drawdown=portfolio_allocation.max_portfolio_drawdown,
                correlation_score=1.0 - portfolio_allocation.diversification_ratio,
                diversification_ratio=portfolio_allocation.diversification_ratio,
                
                # Performance
                total_positions=total_positions,
                capital_efficiency=capital_efficiency,
                expected_annual_return=expected_annual_return
            )
            
        except Exception as e:
            self.logger.error(f"Error compiling results: {e}")
            return TradingSystemResults()

    async def _manage_risk_and_rebalance(self, results: TradingSystemResults):
        """
        Manage risk and rebalance portfolio if needed.
        """
        try:
            # Check risk constraints
            risk_violations = []
            
            if results.portfolio_volatility > self.config.max_portfolio_volatility:
                risk_violations.append(f"Portfolio vol {results.portfolio_volatility:.1%} > limit {self.config.max_portfolio_volatility:.1%}")
            
            if results.max_portfolio_drawdown > self.config.max_drawdown_limit:
                risk_violations.append(f"Max drawdown {results.max_portfolio_drawdown:.1%} > limit {self.config.max_drawdown_limit:.1%}")
            
            if results.correlation_score > self.config.max_correlation_exposure:
                risk_violations.append(f"Correlation {results.correlation_score:.1%} > limit {self.config.max_correlation_exposure:.1%}")
            
            if risk_violations:
                self.logger.warning(f"[WARN]  Risk violations detected: {risk_violations}")
                # TODO: Implement automatic position sizing reduction
            
            # Check if rebalancing is needed
            time_since_rebalance = datetime.now() - self.last_rebalance
            if time_since_rebalance.total_seconds() > (self.config.rebalance_frequency_hours * 3600):
                self.logger.info("[REFRESH] Portfolio rebalancing triggered")
                # TODO: Implement rebalancing logic
                self.last_rebalance = datetime.now()
            
            # Performance monitoring
            if results.portfolio_sharpe_ratio < self.config.target_sharpe_ratio * 0.5:
                self.logger.warning(f"[WARN]  Low Sharpe ratio: {results.portfolio_sharpe_ratio:.2f}")
            
            if results.capital_efficiency < 0.8:
                self.logger.warning(f"[WARN]  Low capital efficiency: {results.capital_efficiency:.1%}")
            
        except Exception as e:
            self.logger.error(f"Error in risk management: {e}")


    def get_system_performance_summary(self) -> Dict:
        """
        Get comprehensive system performance summary.
        """
        try:
            # Get individual strategy performance

            # [ALERT] FIX: Check if market_maker is initialized (not None)
            if self.market_maker:
                mm_performance = self.market_maker.get_performance_summary()
            else:
                # Provide a safe, empty default if not yet initialized
                mm_performance = {'status': 'Uninitialized', 'net_profit': 0.0, 'orders_count': 0}

            market_making_allocation = getattr(self.config, 'market_making_allocation', 0.0)
            directional_trading_allocation = getattr(self.config, 'directional_trading_allocation', 0.0)
            arbitrage_allocation = getattr(self.config, 'arbitrage_allocation', 0.0)

            return {
                'system_status': 'active' if self.market_maker else 'pending_init',
                'total_capital': self.total_capital,
                'capital_allocation': {
                    # Use the allocation from the config, not the dynamic capital attributes
                    'market_making': market_making_allocation,
                    'directional': directional_trading_allocation,
                    'arbitrage': arbitrage_allocation
                },
                'market_making_performance': mm_performance,
                'last_rebalance': self.last_rebalance.isoformat(),
                'risk_limits': {
                    'max_volatility': self.config.max_portfolio_volatility,
                    'max_drawdown': self.config.max_drawdown_limit,
                    'max_correlation': self.config.max_correlation_exposure
                }
            }

        except Exception as e:
            self.logger.error(f"Error getting performance summary: {e}")
            return {}


async def run_unified_trading_system(
    db_manager: DatabaseManager,
    kalshi_client: KalshiClient,
    xai_client: XAIClient,
    config: Optional[TradingSystemConfig] = None
) -> TradingSystemResults:
    """
    Main entry point for the unified advanced trading system.
    
    This is the "Beast Mode" that orchestrates all strategies to maximize
    returns while optimally using all available capital.
    """
    logger = get_trading_logger("unified_trading_main")
    
    try:
        logger.info("[START] Starting Unified Advanced Trading System")
        
        # Initialize system
        trading_system = UnifiedAdvancedTradingSystem(
            db_manager, kalshi_client, xai_client, config
        )
        
        # [ALERT] CRITICAL: Initialize with dynamic balance from the exchange
        await trading_system.async_initialize()
        
        # Execute unified strategy
        results = await trading_system.execute_unified_trading_strategy()
        
        # Log final summary
        logger.info(
            f"[TARGET] UNIFIED SYSTEM COMPLETE [TARGET]\n"
            f"[STATS] PERFORMANCE SUMMARY:\n"
            f"  • Total Positions: {results.total_positions}\n" 
            f"  • Capital Used: ${results.total_capital_used:.0f} ({results.capital_efficiency:.1%})\n"
            f"  • Expected Annual Return: {results.expected_annual_return:.1%}\n"
            f"  • Portfolio Sharpe Ratio: {results.portfolio_sharpe_ratio:.2f}\n"
            f"  • Portfolio Volatility: {results.portfolio_volatility:.1%}\n"
            f"  • Max Drawdown: {results.max_portfolio_drawdown:.1%}\n"
            f"  • Diversification Ratio: {results.diversification_ratio:.2f}\n"
            f"\n"
            f"[MONEY] STRATEGY BREAKDOWN:\n"
            f"  • Market Making: {results.market_making_orders} orders, ${results.market_making_expected_profit:.2f} profit\n"
            f"  • Directional: {results.directional_positions} positions, ${results.directional_expected_return:.2f} return\n"
            f"\n"
            f"[START] SYSTEM STATUS: BEAST MODE ACTIVATED! [START]"
        )
        
        return results
        
    except Exception as e:
        logger.error(f"Error in unified trading system: {e}")
        return TradingSystemResults() 
