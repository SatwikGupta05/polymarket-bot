import asyncio
import sys
import os

# Fix for Windows Emoji Crash
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def get_market_price(market, side: str = "YES"):
    """Return normalized market price (0.0-1.0) for the given side.

    The market object may provide integer bid/ask values in a 0-100 range
    (or larger integer cents). This function prefers midpoint when both
    bid and ask are available, falls back to whichever exists, and
    returns a float in 0.0-1.0 range.
    """
    if side == "YES":
        bid = market.get("yes_bid")
        ask = market.get("yes_ask")
    else:
        bid = market.get("no_bid")
        ask = market.get("no_ask")

    # Compute midpoint when both exist, otherwise prefer the available value
    if bid is not None and ask is not None:
        price = (float(bid) + float(ask)) / 2.0
    elif bid is not None:
        price = float(bid)
    elif ask is not None:
        price = float(ask)
    else:
        return None

    # If values look like integer cents (e.g., 5500), normalize to 55.0 first
    if price > 100.0:
        price = price / 100.0

    # Final normalization to 0.0-1.0
    return price / 100.0

def debug_market_price(market):
    print("\n" + "="*30)
    print(f"MARKET: {market.get('title', 'Unknown')}")
    
    # Show Raw Data
    print("\n[STATS] RAW DATA (Int 0-100):")
    for key in ['yes_bid', 'yes_ask', 'no_bid', 'no_ask']:
        print(f"  {key}: {market.get(key)}")

    # Show Computed Data
    yp = get_market_price(market, "YES")
    np = get_market_price(market, "NO")
    
    print("\n[MONEY] COMPUTED (Float 0.0-1.0):")
    print(f"  YES Price: {yp}")
    print(f"  NO Price:  {np}")

    if yp is None or yp == 0:
        print("\n[FAIL] ALERT: Price is missing or ZERO (Check liquidity)")
    else:
        print("\n[OK] Price logic is valid for trading")
    print("="*30 + "\n")

async def auto_debug():
    from src.clients.polymarket_client import PolymarketClient
    client = PolymarketClient()
    
    try:
        print("Fetching markets from Polymarket API...")
        markets_data = await client.get_markets(limit=5)
        mlist = markets_data if isinstance(markets_data, list) else markets_data.get("markets", [])

        if not mlist:
            print("[FAIL] No markets found.")
            return

        # Test the first market
        debug_market_price(mlist[0])
        
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(auto_debug())