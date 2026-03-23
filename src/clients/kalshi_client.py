"""
Compatibility shim for legacy `KalshiClient` imports.

The bot now trades on Polymarket. Older modules that still import
`src.clients.kalshi_client` receive the Polymarket client via these aliases.
"""

from src.clients.polymarket_client import (
    PolymarketAPIError as KalshiAPIError,
    PolymarketClient as KalshiClient,
)

__all__ = ["KalshiClient", "KalshiAPIError"]
