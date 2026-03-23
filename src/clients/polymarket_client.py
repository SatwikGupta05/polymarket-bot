"""
Polymarket CLOB Client
======================
Drop-in replacement for kalshi_client.py.

Fixes:
- 422 error on get_market(condition_id): Gamma API needs slug or numeric ID,
  not the raw condition_id for the /markets/{id} endpoint.
  Now uses a search-by-conditionId query instead.
- Graceful fallback when py_clob_client is not installed.
"""

import os
import logging
import asyncio
from typing import Optional
import httpx

from src.config.settings import settings
from src.utils.logging_setup import TradingLoggerMixin

logger = logging.getLogger(__name__)

CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_API  = "https://gamma-api.polymarket.com"
CHAIN_ID   = 137
REQUEST_TIMEOUT = 10.0


class PolymarketAPIError(Exception):
    pass


class PolymarketClient(TradingLoggerMixin):
    """
    Polymarket CLOB client. Mirrors KalshiClient's public interface.
    """

    def __init__(self):
        private_key = os.getenv("POLYGON_PRIVATE_KEY", "")
        funder      = os.getenv("POLYGON_PUBLIC_KEY",  "")
        self._authenticated = bool(private_key and funder)
        self._clob = None

        if self._authenticated:
            try:
                from py_clob_client.client import ClobClient
                self._clob = ClobClient(
                    CLOB_HOST,
                    key=private_key,
                    chain_id=CHAIN_ID,
                    signature_type=0,
                    funder=funder,
                )
                self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
                self.logger.info("PolymarketClient authenticated (Level 2)")
            except Exception as e:
                self.logger.warning(f"Auth failed, falling back to read-only: {e}")
                self._authenticated = False
        else:
            try:
                from py_clob_client.client import ClobClient
                self._clob = ClobClient(CLOB_HOST)
            except ImportError:
                self.logger.info("py_clob_client not installed — using HTTP-only mode")
            self.logger.info("PolymarketClient: read-only mode")

        self._http = httpx.Client(
            timeout=15.0,
            headers={"User-Agent": "PolymarketAIBot/1.0"},
        )

    def _safe_get(self, url: str, **kwargs) -> Optional[httpx.Response]:
        """Wrap blocking HTTP GET calls so network failures don't break async pipelines."""
        try:
            return self._http.get(url, **kwargs)
        except Exception as e:
            self.logger.error(f"API failed: {e}", url=url)
            return None

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    async def get_markets(self, limit: int = 100, cursor: Optional[str] = None, **kwargs) -> dict:
        """Fetch active markets from Gamma API."""
        params = {"limit": min(limit, 100), "active": "true", "closed": "false"}
        if cursor:
            params["offset"] = cursor
        try:
            resp = self._safe_get(
                f"{GAMMA_API}/markets",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp is None:
                return {"markets": [], "cursor": None}
            resp.raise_for_status()
            raw  = resp.json()
            return {"markets": [self._normalise(m) for m in raw], "cursor": None}
        except Exception as e:
            self.logger.error(f"get_markets failed: {e}")
            return {"markets": [], "cursor": None}

    async def get_market(self, ticker: str) -> dict:
        """
        Fetch a single market. 
        FIX: Gamma /markets/{id} returns 422 for condition_ids.
        We use ?conditionId= query param instead which always works.
        """
        # Guard: if ticker is purely numeric, it's likely a token_id not a condition_id.
        # Gamma API cannot look up markets by token_id.
        if ticker and str(ticker).isdigit():
            self.logger.warning(
                f"get_market called with numeric token_id '{ticker}' - "
                f"this cannot be used to look up a market. "
                f"Check where this position's market_id was set."
            )
            return {"market": {}}

        # Strategy 1: query by conditionId (most reliable)
        try:
            resp = self._safe_get(
                f"{GAMMA_API}/markets",
                params={"conditionId": ticker, "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            if resp is None:
                raise RuntimeError("conditionId lookup returned no response")
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return {"market": self._normalise(data[0])}
        except Exception as e:
            self.logger.warning(f"get_market conditionId lookup failed for {ticker[:20]}: {e}")

        # Strategy 2: try direct slug lookup
        try:
            resp = self._safe_get(
                f"{GAMMA_API}/markets/{ticker}",
                timeout=REQUEST_TIMEOUT,
            )
            if resp is None:
                raise RuntimeError("direct lookup returned no response")
            if resp.status_code == 200:
                return {"market": self._normalise(resp.json())}
        except Exception as e:
            self.logger.warning(f"get_market direct lookup failed for {ticker[:20]}: {e}")

        # Strategy 3: return a minimal stub so the pipeline doesn't crash
        self.logger.warning(f"get_market({ticker[:20]}…): all strategies failed, using stub")
        return {"market": {
            "ticker": ticker, "title": "Unknown Market", "rules": "",
            "yes_token_id": "", "no_token_id": "",
            "yes_bid": 49, "yes_ask": 51, "no_bid": 49, "no_ask": 51,
            "yes_price": 0.5, "no_price": 0.5,
            "yes_bid_dollars": 0.49, "yes_ask_dollars": 0.51,
            "no_bid_dollars": 0.49, "no_ask_dollars": 0.51,
            "volume": 0, "volume_fp": "0",
            "expiration_time": "2099-01-01T00:00:00Z",
            "status": "active", "category": "misc",
        }}

    async def get_orderbook(self, ticker: str, depth: int = 20) -> dict:
        if self._clob:
            try:
                return self._clob.get_order_book(ticker) or {}
            except Exception:
                pass
        return {}

    async def get_token_price(self, token_id: str) -> Optional[float]:
        """
        Fetch price for a token_id using the same bid/ask logic
        that is proven to work from debug testing.
        """
        for attempt in range(3):
            try:
                # Method 1: CLOB /book endpoint - returns bid/ask same as market data
                try:
                    resp = self._http.get(
                        f"{CLOB_HOST}/book",
                        params={"token_id": token_id},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()

                        asks = data.get("asks", [])
                        bids = data.get("bids", [])

                        best_ask = float(asks[0].get("price", 0)) if asks else 0
                        best_bid = float(bids[0].get("price", 0)) if bids else 0

                        # Normalize: CLOB returns 0-1 already but check range.
                        # If values look like cents (>1.0) divide by 100.
                        if best_ask > 1.0:
                            best_ask = best_ask / 100.0
                        if best_bid > 1.0:
                            best_bid = best_bid / 100.0

                        if best_ask > 0.001 and best_bid > 0.001:
                            price = round((best_ask + best_bid) / 2, 4)
                            if 0.01 <= price <= 0.99:
                                return price

                        if best_ask > 0.001:
                            return round(best_ask, 4)
                        if best_bid > 0.001:
                            return round(best_bid, 4)

                except Exception as e:
                    self.logger.debug(f"CLOB /book attempt {attempt + 1}: {e}")

                # Method 2: CLOB /price endpoint
                try:
                    resp = self._http.get(
                        f"{CLOB_HOST}/price",
                        params={"token_id": token_id, "side": "BUY"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        price = float(data.get("price", 0) or 0)
                        if price > 1.0:
                            price = price / 100.0
                        if 0.01 <= price <= 0.99:
                            return round(price, 4)

                except Exception as e:
                    self.logger.debug(f"CLOB /price attempt {attempt + 1}: {e}")

                # Method 3: CLOB /midpoint endpoint
                try:
                    resp = self._http.get(
                        f"{CLOB_HOST}/midpoint",
                        params={"token_id": token_id},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        price = float(data.get("mid", 0) or 0)
                        if price > 1.0:
                            price = price / 100.0
                        if 0.01 <= price <= 0.99:
                            return round(price, 4)

                except Exception as e:
                    self.logger.debug(f"CLOB /midpoint attempt {attempt + 1}: {e}")

            except Exception as e:
                self.logger.warning(f"get_token_price attempt {attempt + 1}/3: {e}")

            if attempt < 2:
                await asyncio.sleep(1.5)

        self.logger.warning(f"get_token_price failed for {token_id[:20]}")
        return None

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_balance(self) -> dict:
        if self._authenticated and self._clob:
            try:
                raw = self._clob.get_balance()
                usdc = int(raw) / 1e6
                return {"balance": int(usdc * 100)}
            except Exception as e:
                self.logger.error(f"get_balance failed: {e}")
        # Paper mode virtual balance: $1000
        return {"balance": 100000}

    async def get_positions(self, ticker: Optional[str] = None) -> dict:
        if self._authenticated and self._clob:
            try:
                return {"market_positions": self._clob.get_positions() or []}
            except Exception:
                pass
        return {"market_positions": []}

    async def get_orders(self, ticker=None, status=None) -> dict:
        if self._authenticated and self._clob:
            try:
                return {"orders": self._clob.get_orders() or []}
            except Exception:
                pass
        return {"orders": []}

    async def get_fills(self, ticker=None, limit=100) -> dict:
        return {"fills": []}

    async def get_trades(self, ticker=None, limit=100, cursor=None) -> dict:
        return {"trades": [], "cursor": None}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_order(
        self,
        ticker: str,
        client_order_id: str,
        side: str,
        action: str,
        count: int,
        type_: str = "market",
        yes_price: Optional[int] = None,
        no_price:  Optional[int] = None,
        expiration_ts: Optional[int] = None,
        paper_mode: bool = None,
    ) -> dict:
        if paper_mode is None:
            paper_mode = settings.trading.paper_trading_mode

        price_cents = yes_price if side.lower() == "yes" else no_price
        price = (price_cents or 50) / 100
        # Legacy callers still pass a contract/share count.
        # Convert that into a dollar notional for the Polymarket SDK.
        size_usdc = round(max((count or 1) * price, 0.01), 4)

        if paper_mode:
            self.logger.info(
                f"[PAPER] {action.upper()} {side.upper()} "
                f"token={ticker[:16]}… price={price:.2f} size=${size_usdc:.2f}"
            )
            return {"order": {
                "order_id": f"paper_{client_order_id[:8]}",
                "status": "paper", "ticker": ticker,
                "side": side, "price": price, "size": size_usdc,
            }}

        if not self._authenticated or not self._clob:
            raise PolymarketAPIError("Live trading requires POLYGON_PRIVATE_KEY + POLYGON_PUBLIC_KEY")

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
            order_args = OrderArgs(
                token_id=ticker, price=price, size=size_usdc,
                side=BUY if action.lower() == "buy" else SELL,
            )
            signed = self._clob.create_order(order_args)
            resp   = self._clob.post_order(
                signed, OrderType.FOK if type_.lower() == "market" else OrderType.GTC
            )
            return {"order": resp}
        except Exception as e:
            raise PolymarketAPIError(str(e))

    async def cancel_order(self, order_id: str) -> dict:
        if self._authenticated and self._clob:
            try:
                return self._clob.cancel(order_id) or {}
            except Exception as e:
                self.logger.error(f"cancel_order failed: {e}")
        return {}

    async def health_check(self) -> bool:
        try:
            resp = self._safe_get(
                f"{GAMMA_API}/markets",
                params={"limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            if resp is None:
                return False
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        self._http.close()

    async def __aenter__(self): return self
    async def __aexit__(self, *a): await self.close()

    # ------------------------------------------------------------------
    # Normalise Gamma market → internal format
    # ------------------------------------------------------------------

    def _normalise(self, raw: dict) -> dict:
        tokens    = raw.get("tokens", [])
        yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), {})
        no_token  = next((t for t in tokens if t.get("outcome", "").upper() == "NO"),  {})

        # Use the same bid/ask averaging logic validated by the debug script.
        def _price_from_token(token: dict) -> float:
            bid = token.get("yes_bid") or token.get("bid")
            ask = token.get("yes_ask") or token.get("ask")
            p = token.get("price")

            if bid is not None and ask is not None:
                price = (float(bid) + float(ask)) / 2
            elif p is not None:
                price = float(p)
            elif bid is not None:
                price = float(bid)
            elif ask is not None:
                price = float(ask)
            else:
                return 0.5

            # Normalize if returned in 0-100 range.
            if price > 1.0:
                price = price / 100.0

            return round(price, 4) if 0.01 <= price <= 0.99 else 0.5

        yes_price = _price_from_token(yes_token)
        no_price = _price_from_token(no_token)

        # Derive the missing side from the other when one side is unavailable.
        if yes_price == 0.5 and no_price != 0.5:
            yes_price = round(1.0 - no_price, 4)
        if no_price == 0.5 and yes_price != 0.5:
            no_price = round(1.0 - yes_price, 4)

        yes_cents = yes_price * 100
        no_cents  = no_price  * 100

        end_date = raw.get("endDate") or raw.get("endDateIso", "2099-01-01T00:00:00Z")

        # Determine the correct market identifier.
        # Priority: conditionId > condition_id > slug > never use numeric "id".
        raw_condition_id = (
            raw.get("conditionId")
            or raw.get("condition_id")
            or ""
        )

        # If conditionId is missing, try to recover it from the tokens.
        if not raw_condition_id:
            raw_condition_id = (
                yes_token.get("condition_id", "")
                or yes_token.get("conditionId", "")
                or no_token.get("condition_id", "")
                or no_token.get("conditionId", "")
            )

        condition_id = raw_condition_id.strip().lower() if raw_condition_id else ""

        raw_ticker = (
            condition_id
            or raw.get("slug")
            or raw.get("market_slug")
            or ""
        )

        # Consistent format - always lowercase, always stripped.
        market_ticker = raw_ticker.strip().lower() if raw_ticker else ""

        if not market_ticker:
            logging.getLogger("polymarket_client").warning(
                f"Could not find condition_id for market: {raw.get('question', 'Unknown')[:50]} "
                f"raw keys: {list(raw.keys())}"
            )
        elif market_ticker.isdigit():
            logging.getLogger("polymarket_client").warning(
                f"market_ticker is numeric ({market_ticker}) - "
                f"this is a token_id not a condition_id. Raw keys: {list(raw.keys())}"
            )

        return {
            "ticker":          market_ticker,
            "condition_id":    condition_id,
            "yes_token_id":    yes_token.get("token_id", "") or yes_token.get("tokenId", ""),
            "no_token_id":     no_token.get("token_id",  "") or no_token.get("tokenId", ""),
            "title":           raw.get("question", raw.get("title", "Unknown")),
            "rules":           raw.get("description", ""),
            "category":        (raw.get("category") or "misc").lower(),
            "yes_price":       yes_price,
            "no_price":        no_price,
            # cents — for legacy helpers that divide by 100
            "yes_bid":         int(yes_cents * 0.99),
            "yes_ask":         int(yes_cents * 1.01),
            "no_bid":          int(no_cents  * 0.99),
            "no_ask":          int(no_cents  * 1.01),
            # dollars — for newer helpers
            "yes_bid_dollars": yes_price * 0.99,
            "yes_ask_dollars": yes_price * 1.01,
            "no_bid_dollars":  no_price  * 0.99,
            "no_ask_dollars":  no_price  * 1.01,
            "volume":          int(float(raw.get("volume", 0) or 0)),
            "volume_fp":       str(raw.get("volume", "0")),
            "expiration_time": end_date,
            "status":          "active" if raw.get("active", True) else "closed",
            "liquidity":       float(raw.get("liquidity", 0) or 0),
            "_raw":            raw,
        }
