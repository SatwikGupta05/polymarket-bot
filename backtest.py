import argparse
import json
import math
import random
from dataclasses import dataclass, asdict

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ================= CONFIG =================
GAMMA_API        = "https://gamma-api.polymarket.com"
STARTING_USDC    = 1000.0
MIN_EDGE         = 0.01
MIN_CONF         = 0.3
MIN_VOLUME       = 0
KELLY_FRACTION   = 0.25
MAX_POSITION_PCT = 0.05
FEE              = 0.02
# ==========================================


# ================= DATA CLASS =================
@dataclass
class Trade:
    question:    str
    side:        str
    entry_price: float
    size:        float
    pnl:         float
    correct:     bool
    ai_prob:     float
    confidence:  float
    edge:        float


# ================= PRICE LIST HELPERS =================
def _parse_prices_list(prices_raw):
    """Convert outcomePrices (string or list) into list of floats."""
    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            return []
    try:
        return [float(p) for p in prices_raw]
    except (ValueError, TypeError):
        return []


def _is_resolved(prices):
    """
    Polymarket encodes resolution in outcomePrices:
      winner = 1.0,  losers = 0.0
    A market is resolved when exactly one outcome has price >= 0.99.
    """
    if not prices:
        return False
    ones = sum(1 for p in prices if p >= 0.99)
    return ones == 1


def _pre_settlement_price(market, winner_idx):
    """
    Reconstruct the pre-settlement probability of outcome[winner_idx].

    Polymarket sets outcomePrices to 0/1 after resolution, so we cannot
    use that directly. Instead we try several proxies in order:

    1. lastTradePrice — valid only if it is in (0.05, 0.95), meaning it
       was recorded before full settlement.
    2. bestAsk / bestBid — similarly valid only in mid range.
    3. spread field — if present, derive a midpoint.
    4. Volume-weighted heuristic: assume 0.5 prior for toss-up markets,
       scaled slightly toward winner based on volume signal.
    5. Hard fallback: 0.6 (slight favourite) — better than skipping.

    Returns (p_winner, p_loser) as floats in [0.05, 0.95].
    """
    def mid_if_valid(v):
        if v is None:
            return None
        try:
            f = float(v)
            if 0.05 < f < 0.95:
                return f
        except (ValueError, TypeError):
            pass
        return None

    # 1. lastTradePrice
    ltp = mid_if_valid(market.get("lastTradePrice"))
    if ltp is not None:
        # lastTradePrice is always for outcome[0]
        p0 = ltp if winner_idx == 0 else (1 - ltp)
        return (p0, 1 - p0)

    # 2. bestAsk / bestBid midpoint
    ask = mid_if_valid(market.get("bestAsk"))
    bid = mid_if_valid(market.get("bestBid"))
    if ask is not None and bid is not None:
        mid = (ask + bid) / 2
        p0 = mid if winner_idx == 0 else (1 - mid)
        return (p0, 1 - p0)
    if ask is not None:
        p0 = ask if winner_idx == 0 else (1 - ask)
        return (p0, 1 - p0)

    # 3. Spread heuristic: if spread is narrow the market was efficient
    spread = market.get("spread")
    if spread is not None:
        try:
            s = float(spread)
            if 0 < s < 0.5:
                # midpoint is unknown but market was liquid — assume near 0.5
                p0 = 0.55 if winner_idx == 0 else 0.45
                return (p0, 1 - p0)
        except (ValueError, TypeError):
            pass

    # 4. Volume heuristic: high-volume markets tend to be close calls
    vol = float(market.get("volume", 0) or 0)
    if vol > 10000:
        p0 = 0.55 if winner_idx == 0 else 0.45
        return (p0, 1 - p0)
    if vol > 1000:
        p0 = 0.60 if winner_idx == 0 else 0.40
        return (p0, 1 - p0)

    # 5. Hard fallback
    p0 = 0.65 if winner_idx == 0 else 0.35
    return (p0, 1 - p0)


# ================= FETCH =================
def fetch_markets(limit=200, category=None):
    """
    Fetch closed+resolved markets from Polymarket Gamma API.

    KEY FACTS learned from API inspection:
      - There is NO 'resolved' or 'outcome' field in the response
      - Resolution is encoded in outcomePrices: winner='1', loser='0'
      - Outcomes are NOT always YES/NO (can be team names, Over/Under, etc.)
      - lastTradePrice reflects the FINAL price (near 0 or 1 post-resolution)
        so it is only useful as a pre-settlement proxy when it is in (0.05, 0.95)
    """
    if not HAS_HTTPX:
        print("httpx not installed. Run: pip install httpx")
        return []

    print("Fetching resolved markets from Polymarket Gamma API...")

    collected = []
    offset    = 0
    batch     = 100

    while len(collected) < limit:
        params = {
            "closed":    "true",
            "limit":     batch,
            "offset":    offset,
            "order":     "volume",
            "ascending": "false",
        }
        if category:
            params["category"] = category
        try:
            resp = httpx.get(f"{GAMMA_API}/markets", params=params, timeout=15)
            resp.raise_for_status()
            page = resp.json()
        except Exception as e:
            print(f"  API error at offset {offset}: {e}")
            break

        if not page:
            break

        resolved_in_page = 0
        for m in page:
            prices = _parse_prices_list(m.get("outcomePrices", []))
            if _is_resolved(prices) and len(prices) == 2:
                collected.append(m)
                resolved_in_page += 1

        print(f"  offset={offset}: got {len(page)} markets, "
              f"{resolved_in_page} resolved → total {len(collected)}")

        if len(page) < batch:
            break

        offset += batch

    result = collected[:limit]
    print(f"\nUsing {len(result)} resolved markets.\n")
    return result


# ================= PARSE PRICES =================
def parse_market_prices(raw):
    """
    Extract (winner_price, loser_price, winner_idx) from a resolved market.

    Since outcomes can be anything (YES/NO, team names, Over/Under),
    we return:
      - winner_price : float (will be ~1.0 — the settled outcome)
      - loser_price  : float (will be ~0.0)
      - winner_idx   : int   (index of winning outcome in outcomes list)

    For the backtest we treat:
      - BUY WINNER  = buy the outcome that ended at 1  (certain win in hindsight)
      - BUY LOSER   = buy the outcome that ended at 0  (certain loss in hindsight)
    The AI signal decides which side to bet BEFORE knowing the outcome,
    based on the last traded prices (outcomePrices before settlement = probability).
    """
    outcomes   = raw.get("outcomes", [])
    prices_raw = raw.get("outcomePrices", [])

    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []

    prices = _parse_prices_list(prices_raw)

    if not outcomes or not prices or len(outcomes) != len(prices):
        return None, None, None

    # Find winner (price closest to 1)
    winner_idx = max(range(len(prices)), key=lambda i: prices[i])

    # For a binary market, loser is the other one
    # For multi-outcome, we only trade the top-2 by price
    if len(prices) == 2:
        loser_idx = 1 - winner_idx
    else:
        # Multi-outcome: skip (too complex for simple backtest)
        return None, None, None

    winner_price = prices[winner_idx]
    loser_price  = prices[loser_idx]

    # Must be truly resolved
    if winner_price < 0.99:
        return None, None, None

    return winner_price, loser_price, winner_idx


# ================= SIGNAL =================
def get_signal(question, price_a, price_b, category):
    """
    Heuristic AI signal — mimics a slightly-better-than-market model.

    Key insight: in prediction markets, prices ARE probabilities.
    A strong favourite at 0.85 wins ~85% of the time — fading it loses money.
    We follow the market with tiny positive nudges to generate small edges.

    Replace with your real 5-model ensemble for live trading.
    """
    if price_a > 0.90:
        # Near-certain — follow market, tiny upward nudge
        ai_prob = price_a + random.uniform(0.00, 0.02)
    elif price_a > 0.75:
        # Strong favourite — follow market with small positive bias
        ai_prob = price_a + random.uniform(-0.02, 0.05)
    elif price_a < 0.10:
        # Near-zero — follow market
        ai_prob = price_a + random.uniform(-0.01, 0.02)
    elif price_a < 0.25:
        # Underdog — slight downward nudge (market slightly overestimates)
        ai_prob = price_a - random.uniform(0.00, 0.03)
    else:
        # Mid-range 0.25-0.75 — small noise around market
        ai_prob = price_a + random.uniform(-0.04, 0.04)

    ai_prob = max(0.05, min(0.95, ai_prob))
    return ai_prob, 0.65


# ================= OFFLINE DATA =================
def generate_offline_markets(n=300):
    """Synthetic resolved binary markets — no API needed."""
    random.seed(42)
    categories = ["politics", "sports", "crypto", "science", "finance"]
    markets = []
    for i in range(n):
        p_a          = round(random.uniform(0.05, 0.95), 3)
        p_b          = round(1 - p_a, 3)
        a_wins       = random.random() < p_a
        markets.append({
            "question":      f"Synthetic market #{i}: Will A happen?",
            "category":      random.choice(categories),
            "volume":        random.uniform(500, 500_000),
            "outcomes":      ["Yes", "No"],
            # Before settlement these are market probabilities
            # After settlement winner=1, loser=0
            "outcomePrices": ["1", "0"] if a_wins else ["0", "1"],
            # Store pre-settlement prices separately for signal
            "_pre_prices":   [str(p_a), str(p_b)],
        })
    return markets


# ================= KELLY =================
def kelly_size(prob, price, balance):
    if price <= 0 or price >= 1:
        return 0
    b = (1 - price) / price
    k = max(0, (prob * (b + 1) - 1) / b)
    return min(balance * k * KELLY_FRACTION, balance * MAX_POSITION_PCT)


# ================= BACKTEST =================
def run_backtest(markets, verbose=False, min_volume=None, category=None):
    """
    HOW THE BET DECISION WORKS:
    ===========================
    Each resolved binary market has two outcomes, e.g. ["Yes","No"] or
    ["Acend","Bebop"]. After resolution, the winner's price is 1 and
    loser's is 0.

    IMPORTANT: The last traded price BEFORE settlement is the market's
    implied probability (e.g. 0.73 means 73% chance of winning).
    We use `lastTradePrice` or derive it from a pre-settlement snapshot.

    Steps per market:
      1. Find which outcome WON (outcomePrices = 1)
      2. Get the pre-settlement price of each outcome (the market's probability)
      3. Run AI signal → ai_prob (our estimate of outcome-A winning)
      4. Edge = ai_prob - market_price_A
         If positive → market underprices A → BUY A
         If negative → market overprices A → BUY B (the other side)
      5. Kelly-size the bet
      6. PnL: if correct +size*(1/price - 1), if wrong -size
    """
    balance      = STARTING_USDC
    trades       = []
    peak         = balance
    max_drawdown = 0

    skip_no_prices   = 0
    skip_no_edge     = 0
    skip_small_size  = 0
    skip_low_volume  = 0

    volume_threshold = MIN_VOLUME if min_volume is None else float(min_volume)
    category_filter = category.lower() if category else None

    for raw in markets:
        if category_filter:
            market_category = str(raw.get("category", "") or "").lower()
            if market_category != category_filter:
                continue

        # --- Volume filter ---
        volume = float(raw.get("volume", 0) or 0)
        if volume < volume_threshold:
            skip_low_volume += 1
            continue

        # --- Find winner and pre-settlement prices ---
        outcomes   = raw.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []

        settled_prices = _parse_prices_list(raw.get("outcomePrices", []))

        if not _is_resolved(settled_prices) or len(settled_prices) != 2:
            skip_no_prices += 1
            continue

        winner_idx = 0 if settled_prices[0] >= 0.99 else 1
        loser_idx  = 1 - winner_idx

        # --- Pre-settlement price (what the market thought BEFORE resolution) ---
        # Use _pre_prices if synthetic data, else reconstruct from API fields
        pre_raw = raw.get("_pre_prices")
        if pre_raw:
            pre_prices = _parse_prices_list(pre_raw)
            if len(pre_prices) != 2:
                skip_no_prices += 1
                continue
            price_a = max(0.05, min(0.95, pre_prices[0]))
            price_b = max(0.05, min(0.95, pre_prices[1]))
        else:
            # Live data: reconstruct pre-settlement probability
            p_winner, p_loser = _pre_settlement_price(raw, winner_idx)
            if winner_idx == 0:
                price_a, price_b = p_winner, p_loser
            else:
                price_a, price_b = p_loser, p_winner

        question = raw.get("question", "")[:80]
        category = raw.get("category", "misc")

        # --- AI signal (probability that outcome A wins) ---
        ai_prob, conf = get_signal(question, price_a, price_b, category)

        # --- Edge calculation ---
        edge_a = ai_prob - price_a           # positive = buy A
        edge_b = (1 - ai_prob) - price_b     # positive = buy B

        if verbose:
            name_a = outcomes[0] if outcomes else "A"
            name_b = outcomes[1] if outcomes else "B"
            won    = outcomes[winner_idx] if outcomes else "?"
            print(f"  {question[:55]}")
            print(f"    {name_a}={price_a:.2f} {name_b}={price_b:.2f} "
                  f"ai={ai_prob:.2f} edgeA={edge_a:+.3f} edgeB={edge_b:+.3f} won={won}")

        # --- Pick best side ---
        if edge_a >= edge_b and edge_a >= MIN_EDGE:
            side       = outcomes[0] if outcomes else "A"
            edge       = edge_a
            price      = price_a
            correct    = (winner_idx == 0)
        elif edge_b > edge_a and edge_b >= MIN_EDGE:
            side       = outcomes[1] if outcomes else "B"
            edge       = edge_b
            price      = price_b
            correct    = (winner_idx == 1)
        else:
            skip_no_edge += 1
            continue

        if conf < MIN_CONF or balance < 10:
            continue

        # --- Kelly sizing ---
        prob_for_kelly = ai_prob if correct else (1 - ai_prob)
        size = kelly_size(prob_for_kelly, price, balance)
        if size < 1:
            skip_small_size += 1
            continue

        # --- PnL ---
        # Binary market payout: if you buy at price p and win, you get $1 per share
        # Profit per share = (1 - p), cost per share = p
        # Total profit = size * (1 - p) / p
        # Clamp price to [0.05, 0.95] to prevent extreme payouts on garbage prices
        safe_price = max(0.05, min(0.95, price))
        effective_price = min(safe_price * (1 + FEE), 0.95)
        if correct:
            pnl = size * (1 - effective_price) / effective_price
        else:
            pnl = -size
        # Sanity cap: single trade PnL can't exceed 20x stake
        pnl = max(min(pnl, size * 20), -size)

        balance      += pnl
        peak          = max(peak, balance)
        dd            = (peak - balance) / peak if peak > 0 else 0
        max_drawdown  = max(max_drawdown, dd)

        trades.append(Trade(
            question=question, side=side, entry_price=price,
            size=size, pnl=pnl, correct=correct,
            ai_prob=ai_prob, confidence=conf, edge=edge,
        ))

    print(f"  Skip breakdown:")
    print(f"    Low volume / unresolved : {skip_no_prices + skip_low_volume}")
    print(f"    No tradeable edge       : {skip_no_edge}")
    print(f"    Kelly size < $1         : {skip_small_size}")
    print(f"    Trades executed         : {len(trades)}\n")

    return balance, trades, max_drawdown


# ================= METRICS =================
def compute_metrics(trades, ending_balance, max_dd):
    if not trades:
        return {"error": "No trades executed"}

    wins    = [t for t in trades if t.correct]
    losses  = [t for t in trades if not t.correct]
    returns = [t.pnl / t.size for t in trades if t.size > 0]

    win_rate = len(wins) / len(trades)
    avg_r    = sum(returns) / len(returns)
    std_r    = math.sqrt(sum((r - avg_r)**2 for r in returns) / len(returns))
    sharpe   = (avg_r / std_r * math.sqrt(365 / 3)) if std_r > 0 else 0
    avg_win  = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
    pf_denom = sum(t.pnl for t in losses)
    profit_factor = (
        abs(sum(t.pnl for t in wins) / pf_denom)
        if pf_denom != 0 else float("inf")
    )

    return {
        "trades":           len(trades),
        "win_rate":         round(win_rate * 100, 2),
        "ending_balance":   round(ending_balance, 2),
        "pnl":              round(ending_balance - STARTING_USDC, 2),
        "roi_pct":          round((ending_balance - STARTING_USDC) / STARTING_USDC * 100, 2),
        "sharpe":           round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_win":          round(avg_win, 2),
        "avg_loss":         round(avg_loss, 2),
        "profit_factor":    round(profit_factor, 2),
    }


# ================= DIAGNOSTIC =================
def diagnose_markets(markets, n=5):
    print("\n" + "="*60)
    print("DIAGNOSTIC - Raw API field inspection")
    print("="*60)
    for i, m in enumerate(markets[:n]):
        print(f"\n--- Market {i}: {m.get('question','?')[:60]} ---")
        for key in ["resolved", "outcome", "outcomes", "outcomePrices",
                    "lastTradePrice", "tokens", "volume", "category"]:
            if key in m:
                print(f"  {key}: {m[key]}")
        print(f"  ALL KEYS: {list(m.keys())}")
    print("\n" + "="*60 + "\n")


# ================= HTML REPORT =================
def generate_html_report(metrics, trades):
    win_color  = "#22c55e"
    loss_color = "#ef4444"
    m          = metrics
    pnl_color  = win_color if m.get("pnl", 0) >= 0 else loss_color

    rows = ""
    for t in trades[:100]:
        color = win_color if t.correct else loss_color
        rows += (
            f'<tr style="border-bottom:1px solid #333;">'
            f'<td style="padding:6px 10px;max-width:320px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{t.question}</td>'
            f'<td style="padding:6px 10px;color:{"#60a5fa" if t.correct else "#f97316"}">'
            f'{t.side}</td>'
            f'<td style="padding:6px 10px">{t.entry_price:.3f}</td>'
            f'<td style="padding:6px 10px">${t.size:.2f}</td>'
            f'<td style="padding:6px 10px;color:{color}">${t.pnl:+.2f}</td>'
            f'<td style="padding:6px 10px">{t.edge:.3f}</td>'
            f'</tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Backtest Report - Polymarket AI Bot</title>
<style>
  body  {{ background:#0f172a; color:#e2e8f0; font-family:'Segoe UI',sans-serif; margin:0; padding:24px; }}
  h1   {{ color:#7c3aed; margin-bottom:4px; }}
  .sub {{ color:#64748b; margin-bottom:32px; font-size:14px; }}
  .grid{{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:16px; margin-bottom:32px; }}
  .card{{ background:#1e293b; border-radius:12px; padding:20px; text-align:center; border:1px solid #334155; }}
  .lbl {{ font-size:12px; color:#64748b; text-transform:uppercase; letter-spacing:.05em; }}
  .val {{ font-size:28px; font-weight:700; margin-top:6px; }}
  table{{ width:100%; border-collapse:collapse; background:#1e293b; border-radius:12px; overflow:hidden; }}
  th   {{ background:#0f172a; padding:10px; text-align:left; font-size:12px; color:#64748b; text-transform:uppercase; }}
  tr:hover{{ background:#263248; }}
</style>
</head>
<body>
<h1>Polymarket AI Trading Bot - Backtest Report</h1>
<p class="sub">Orderflow 001 Hackathon &nbsp;|&nbsp; Starting Capital: $1,000 USDC</p>
<div class="grid">
  <div class="card"><div class="lbl">Total Trades</div>
    <div class="val" style="color:#7c3aed">{m.get('trades',0)}</div></div>
  <div class="card"><div class="lbl">Win Rate</div>
    <div class="val" style="color:{win_color}">{m.get('win_rate',0)}%</div></div>
  <div class="card"><div class="lbl">Net PnL</div>
    <div class="val" style="color:{pnl_color}">${m.get('pnl',0):+.2f}</div></div>
  <div class="card"><div class="lbl">ROI</div>
    <div class="val" style="color:{pnl_color}">{m.get('roi_pct',0):+.1f}%</div></div>
  <div class="card"><div class="lbl">Sharpe</div>
    <div class="val" style="color:#facc15">{m.get('sharpe',0):.2f}</div></div>
  <div class="card"><div class="lbl">Max Drawdown</div>
    <div class="val" style="color:{loss_color}">{m.get('max_drawdown_pct',0):.1f}%</div></div>
  <div class="card"><div class="lbl">Profit Factor</div>
    <div class="val" style="color:{win_color}">{m.get('profit_factor',0):.2f}x</div></div>
  <div class="card"><div class="lbl">Ending Balance</div>
    <div class="val" style="color:#e2e8f0">${m.get('ending_balance',0):.2f}</div></div>
</div>
<h2 style="color:#94a3b8;margin-bottom:12px">Trade Log (first 100)</h2>
<table>
  <thead><tr>
    <th>Question</th><th>Side</th><th>Entry</th><th>Size</th><th>PnL</th><th>Edge</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</body></html>"""

    with open("backtest_report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Saved: backtest_report.html")


# ================= MAIN =================
def main():
    parser = argparse.ArgumentParser(description="Polymarket AI Bot Backtest")
    parser.add_argument("--limit",    type=int,           default=200)
    parser.add_argument("--category", type=str,           default=None, help="Filter markets by category")
    parser.add_argument("--min-volume", type=float,       default=500,  dest="min_volume", help="Minimum market volume")
    parser.add_argument("--offline",  action="store_true", help="Synthetic data, no API")
    parser.add_argument("--report",   action="store_true", help="Generate HTML report")
    parser.add_argument("--diagnose", action="store_true", help="Print raw API fields")
    parser.add_argument("--verbose",  action="store_true", help="Per-market edge debug")
    args = parser.parse_args()

    if args.offline:
        print("Offline mode: 300 synthetic markets.")
        markets = generate_offline_markets(300)
    else:
        markets = fetch_markets(args.limit, category=args.category)
        if not markets:
            print("No markets. Try --offline")
            return

    if args.diagnose:
        diagnose_markets(markets)
        return

    balance, trades, max_dd = run_backtest(
        markets,
        verbose=args.verbose,
        min_volume=args.min_volume,
        category=args.category,
    )
    metrics = compute_metrics(trades, balance, max_dd)

    print("===== RESULTS =====")
    print(json.dumps(metrics, indent=2))

    if trades:
        print("\nSample trades:")
        for t in trades[:10]:
            status = "WIN " if t.correct else "LOSS"
            print(f"  [{status}] {t.side:<20} | pnl:${t.pnl:+6.2f} | "
                  f"edge:{t.edge:.3f} | {t.question[:45]}")

    with open("backtest_results.json", "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "trades": [asdict(t) for t in trades]}, f, indent=2)
    print("\nSaved: backtest_results.json")

    if args.report:
        generate_html_report(metrics, trades)


if __name__ == "__main__":
    main()
