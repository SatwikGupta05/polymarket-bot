# Polymarket AI Trading Bot

**An autonomous trading bot for [Polymarket](https://polymarket.com) on-chain prediction markets powered by a five-model AI ensemble.**

Five frontier LLMs debate every trade. The system only enters when they agree.

[Quick Start](#quick-start) · [How It Works](#how-it-works) · [Features](#features) · [Configuration](#configuration-reference) · [Demo Guide](#demo-video-guide) · [Backtest Results](#backtest-results)

---

> **Disclaimer** — This is experimental software for educational and research purposes only. Trading involves substantial risk of loss. Only trade with capital you can afford to lose. Past performance does not guarantee future results. This software is not financial advice.

> **Why Discipline Mode Exists** — Through extensive paper trading on Polymarket across multiple strategies, we learned that trading without category enforcement and risk guardrails leads to significant losses. The most common mistakes: over-allocating to economic events with no real edge, and using aggressive position sizing. This repo ships with discipline systems enabled by default — category scoring, portfolio enforcement, and sane risk parameters.

---

## Quick Start

Three steps to get running in paper trading mode (no real money, no wallet needed):

```bash
# 1. Clone and set up
git clone <your-repo-url>
cd polymarket-bot
python setup.py

# 2. Add free API keys to .env
cp env.template .env
# Fill in:
#   GROQ_API_KEY   -> console.groq.com   (free, instant signup)
#   GEMINI_API_KEY -> aistudio.google.com (free, instant signup)

# 3. Reset demo state and run
python scripts/reset_demo.py
python cli.py run --paper
```

Open the live dashboard in a second terminal:

```bash
streamlit run dashboard.py
```

Run the backtest (no API keys needed at all):

```bash
python cli.py backtest --offline --report
```

---

## AI Model Tiers

### Free Tier (Demo-ready — default, no credit card needed)

| Role | Provider | Model | Weight |
|------|----------|-------|--------|
| Lead Forecaster | Groq | llama-3.3-70b-versatile | 30% |
| Bull Researcher | Groq | mixtral-8x7b-32768 | 20% |
| Bear Researcher | Gemini | gemini-1.5-flash | 15% |
| News Analyst | Gemini | gemini-1.5-flash | 20% |
| Risk Manager | Groq | llama-3.1-8b-instant | 15% |

**Free keys needed (both instant signup, no credit card):**
- `GROQ_API_KEY` — [console.groq.com](https://console.groq.com)
- `GEMINI_API_KEY` — [aistudio.google.com](https://aistudio.google.com/apikey)

### Paid Tier (Optional — higher accuracy)

| Role | Provider | Model | Weight |
|------|----------|-------|--------|
| Lead Forecaster | xAI | Grok-beta | 30% |
| Bull Researcher | OpenRouter | GPT-4o | 20% |
| Bear Researcher | OpenRouter | Gemini Flash 1.5 | 15% |
| News Analyst | OpenRouter | Claude 3.5 Sonnet | 20% |
| Risk Manager | OpenRouter | DeepSeek R1 | 15% |

The bot automatically uses the best available models based on keys present in `.env`. No code changes needed to switch tiers.

---

## Features

### Multi-Model AI Ensemble
- **Five frontier LLMs** collaborate on every decision via a structured Bull vs Bear debate protocol
- **Role-based specialization** — each model plays a distinct analytical role
- **Consensus gating** — positions are skipped when models diverge beyond the confidence threshold
- **Deterministic outputs** — temperature=0 for reproducible AI reasoning
- **Free-tier fallback** — always works with Groq + Gemini when no paid keys are present
- **Rate limiting** — built-in throttling and 429 retry logic for free tier stability

### Trading Strategies
- **Directional trading** (50% of capital) — AI-predicted probability edge with Kelly Criterion sizing
- **Market making** (40%) — automated limit orders capturing bid-ask spread
- **Arbitrage detection** (10%) — cross-market opportunity scanning

### Risk Management
- **Fractional Kelly** position sizing (quarter-Kelly for safety)
- **Hard daily loss limit** — stops trading at configurable drawdown threshold
- **Max drawdown circuit breaker** — halts entire system at portfolio-level loss
- **Sector concentration cap** — prevents over-concentration in any single category
- **Daily AI cost budget** — stops AI spending when configured limit is hit
- **Category scoring** — blocks historically losing market categories automatically
- **24h max hold exit** — force-closes positions that would otherwise be stuck forever

### On-Chain Integration (Polymarket)
- **Polygon-native** — USDC-based, on-chain order signing via ERC-1155 tokens
- **py-clob-client** official SDK for order placement
- **Paper trading mode** — simulate without any real orders or wallet needed
- **MetaMask / EOA wallet** support for live trading
- **Token-level price fetching** — uses YES/NO token IDs for accurate CLOB prices

### Dynamic Exit Strategies
- Trailing take-profit at configurable gain threshold
- Stop-loss at configurable per-position loss
- Confidence-decay exits when AI conviction drops
- Time-based exits (max hold hours)
- Market resolution exits (binary payout on settlement)

### Observability
- **Real-time Streamlit dashboard** — portfolio value, positions, P&L, AI decision logs
- **Paper trading mode** — simulate trades, track outcomes on settled markets
- **SQLite telemetry** — every trade, AI decision, and cost metric logged locally
- **Unified CLI** — run, dashboard, status, health, backtest, scores, models, history
- **Backtesting engine** — replay against real resolved Polymarket markets

---

## How It Works

The bot runs a four-stage pipeline on a continuous loop:

```
  INGEST                DECIDE (5-Model Ensemble)      EXECUTE        TRACK
 --------              --------------------------      ---------     --------

  Polymarket  -------> Groq/Grok    (Forecaster 30%)
  Gamma API            Gemini/Claude (News      20%)
                       Groq/GPT-4o  (Bull       20%) --> Polygon --> P&L
  CLOB API   -------> Gemini        (Bear       15%)    CLOB         Win Rate
  Order Book           Groq/DeepSeek (Risk      15%)    Order        Sharpe
                                                        Kelly        Drawdown
  RSS / News -------> Debate --> Consensus              Sizing       Category
  Feeds                Confidence Calibration                         Scores
```

### Stage 1 — Ingest
Active markets are fetched from the Polymarket Gamma API every 60 seconds. Markets are filtered by volume, time to expiry, and category score. Top 5 markets by volume are passed to the AI for cost-efficient analysis.

### Stage 2 — Decide (Multi-Model Ensemble)
Each of the five models analyzes the market from its assigned perspective and returns a probability estimate and confidence score. The ensemble combines weighted votes:

- If weighted confidence falls below `min_confidence_to_trade` (default: 0.40), the opportunity is skipped
- If models disagree significantly (high variance), position size is automatically reduced
- Edge is calculated as the difference between AI probability and current market price
- Minimum 2% edge required to proceed to execution

### Stage 3 — Execute
Qualifying trades are sized using the Kelly Criterion (quarter-Kelly) and either logged as paper signals or routed through the Polymarket CLOB API using the YES or NO token ID for the specific outcome. Market-making orders are placed symmetrically around the mid-price.

### Stage 4 — Track
Every decision is written to SQLite. Positions are monitored for stop-loss, take-profit, time-based, and market resolution exits. PnL is calculated correctly per side (YES: exit-entry, NO: entry-exit).

---

## Installation

### Prerequisites

- Python 3.9 or later
- Free API keys: Groq + Gemini (sufficient for paper trading and demo)
- Polygon wallet keys only needed for live trading

### Automated Setup (Recommended)

```bash
git clone <your-repo-url>
cd polymarket-bot
python setup.py
```

The setup script:
- Checks Python version compatibility
- Creates a virtual environment
- Installs all dependencies
- Copies `env.template` to `.env`
- Initialises the SQLite database
- Prints next steps

### Manual Installation

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
pip install -r dashboard_requirements.txt
```

### Configuration

```bash
cp env.template .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Free tier | Groq inference — [console.groq.com](https://console.groq.com) |
| `GEMINI_API_KEY` | Free tier | Gemini — [aistudio.google.com](https://aistudio.google.com) |
| `POLYGON_PRIVATE_KEY` | Live only | 64-char hex wallet key (no 0x prefix) |
| `POLYGON_PUBLIC_KEY` | Live only | Wallet public address |
| `LIVE_TRADING_ENABLED` | All | `false` for paper, `true` for live |
| `TRADING_MODE` | All | `paper` or `live` |
| `XAI_API_KEY` | Optional | Grok-beta paid tier |
| `OPENROUTER_API_KEY` | Optional | Claude / GPT-4o / DeepSeek paid tier |

For hackathon demos, set `LIVE_TRADING_ENABLED=false`. Paper trading works with only Groq and Gemini keys.

### Initialise the Database

```bash
python -c "import asyncio; from src.utils.database import DatabaseManager; asyncio.run(DatabaseManager().initialize())"
```

---

## CLI Reference

```bash
# Trading
python cli.py run --paper              # Paper trading (safe, default)
python cli.py run --live               # Live trading (real USDC)
python cli.py run --mode disciplined   # Explicit disciplined mode
python cli.py run --safe-compounder    # Most conservative strategy
python cli.py run --beast --paper      # Aggressive (paper only)

# Monitoring
python cli.py dashboard                # Streamlit live dashboard
python cli.py status                   # Balance and open positions
python cli.py health                   # API connection check
python cli.py scores                   # Category score table
python cli.py history                  # Last 50 trades
python cli.py history --limit 100      # Last 100 trades
python cli.py models                   # Active AI model routing

# Analysis
python cli.py backtest --offline       # Backtest on sample data
python cli.py backtest --report        # Save HTML report
python cli.py backtest --category crypto
```

---

## Paper Trading

Simulate trades without risking real money. Every signal is logged to SQLite.

```bash
# Single scan — log any signals found
python paper_trader.py

# Continuous scanning every 60 seconds
python paper_trader.py --loop --interval 60

# Settle markets and update win/loss outcomes
python paper_trader.py --settle

# Generate HTML performance report
python paper_trader.py --dashboard

# Print stats to terminal
python paper_trader.py --stats
```

The HTML report is written to `docs/paper_dashboard.html`.

---

## Trading Modes

### 1. Disciplined Mode (DEFAULT)

```bash
python cli.py run --paper
```

| Setting | Value |
|---------|-------|
| Max drawdown | 15% |
| Min confidence | 40% |
| Max position | 3% of portfolio |
| Sector cap | 30% |
| Kelly fraction | 0.25 |
| Category scoring | Active |

### 2. Safe Compounder

```bash
python cli.py run --safe-compounder
```

Most conservative. Only trades high-score categories. Resting maker orders only.

### 3. Beast Mode

```bash
python cli.py run --beast --paper   # Paper only recommended
```

| Setting | Value |
|---------|-------|
| Max drawdown | 50% |
| Min confidence | 50% |
| Max position | 5% |
| Sector cap | 90% |
| Kelly fraction | 0.75 |

---

## Category Scoring System

The category scorer evaluates each Polymarket market category on a 0-100 scale and enforces allocation limits based on historical performance.

### Scoring Formula

| Factor | Weight | Description |
|--------|--------|-------------|
| ROI | 40% | Average return across all trades |
| Recent Trend | 25% | Direction of last 10 trades (recency-weighted) |
| Sample Size | 20% | More data = more confidence |
| Win Rate | 15% | Percentage of winning trades |

### Allocation Tiers

| Score | Max Allocation | Status |
|-------|---------------|--------|
| 80-100 | 20% of portfolio | STRONG |
| 60-79 | 10% of portfolio | GOOD |
| 40-59 | 5% of portfolio | WEAK |
| 20-39 | 2% of portfolio | POOR |
| 0-19 | 0% (blocked) | BLOCKED |

Categories scoring below 30 are hard-blocked regardless of AI confidence.

```bash
python cli.py scores
```

---

## Backtest Results

Backtesting over 50 resolved Polymarket markets (offline sample data):

| Metric | Value |
|--------|-------|
| Starting balance | $1,000 USDC |
| Ending balance | $2,634 USDC |
| Total ROI | +163.5% |
| Win rate | 83.8% |
| Sharpe ratio | 6.63 |
| Max drawdown | 5.2% |
| Markets traded | 37 of 50 |
| Avg AI edge | 21.2 cents |

```bash
# Run yourself
python cli.py backtest --offline --report
open backtest_report.html
```

---

## Demo Video Guide

**Minimum setup for demo (both keys are free):**

```bash
GROQ_API_KEY=your_key
GEMINI_API_KEY=your_key
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
```

**Before recording:**

```bash
python scripts/remove_all_emojis.py   # Fix Windows encoding
python scripts/reset_demo.py          # Clear stale data
python cli.py health                  # Verify connections
python cli.py models                  # Verify free tier active
set PYTHONIOENCODING=utf-8            # Windows only
```

**Recording sequence (split screen):**

Terminal 1:
```bash
python cli.py run --paper
```

Terminal 2:
```bash
streamlit run dashboard.py
```

**Suggested 3-minute structure:**

| Time | What to show |
|------|-------------|
| 0:00-0:30 | Problem: prediction markets are inefficient, manual trading is slow |
| 0:30-1:15 | Architecture: 5-model AI ensemble, Kelly sizing, on-chain execution |
| 1:15-2:00 | Live demo: bot scanning markets, AI models debating, signals firing |
| 2:00-2:30 | Performance: backtest report, win rate, Sharpe ratio, category scores |
| 2:30-3:00 | What makes it different: consensus gating, free+paid tier, real Polymarket data |

---

## Utility Scripts

```bash
# Remove all emojis from Python files (fixes Windows cp1252 crash)
python scripts/remove_all_emojis.py

# Clear database for fresh session
python scripts/reset_demo.py

# Debug price fetching for a specific market token
python scripts/debug_price.py
python scripts/debug_price.py --market 0xabc123...

# Verify --live and --paper flags work correctly
python verify_fix.py
```

---

## Project Structure

```
polymarket-bot/
├── beast_mode_bot.py           # Main bot entry point and orchestrator loop
├── cli.py                      # Unified CLI (does not place trades itself)
├── paper_trader.py             # Signal-only paper trading mode
├── dashboard.py                # Streamlit live dashboard
├── backtest.py                 # Backtesting engine (offline + live data)
├── setup.py                    # Automated environment setup (no trades)
├── verify_fix.py               # Verifies --live and --paper flags correctly
├── env.template                # Environment variables template
├── requirements.txt            # Python dependencies
├── dashboard_requirements.txt  # Dashboard-specific dependencies
│
├── scripts/
│   ├── reset_demo.py           # Clear database for fresh demo session
│   ├── remove_all_emojis.py    # Remove emojis from all Python files
│   └── debug_price.py          # Inspect raw token price API responses
│
└── src/
    ├── agents/                 # 5-model ensemble agents
    ├── clients/
    │   ├── polymarket_client.py    # Polymarket Gamma + CLOB client
    │   ├── free_model_client.py    # Groq + Gemini free tier
    │   ├── model_router.py         # Routes to best available AI
    │   ├── xai_client.py           # xAI Grok paid tier
    │   └── openrouter_client.py    # OpenRouter paid tier
    ├── config/
    │   └── settings.py         # All thresholds and parameters
    ├── data/
    │   ├── news_aggregator.py  # RSS feed aggregation
    │   └── sentiment_analyzer.py
    ├── jobs/
    │   ├── ingest.py           # Market fetching and filtering
    │   ├── decide.py           # AI decision pipeline
    │   ├── execute.py          # Order execution (paper and live)
    │   └── track.py            # Position tracking and exits
    ├── strategies/
    │   ├── unified_trading_system.py
    │   ├── portfolio_optimization.py
    │   ├── market_making.py
    │   ├── category_scorer.py
    │   └── safe_compounder.py
    └── utils/
        ├── database.py
        ├── edge_filter.py
        ├── stop_loss_calculator.py
        ├── logging_setup.py
        ├── position_limits.py
        └── cash_reserves.py
```

---

## Configuration Reference

All trading parameters live in `src/config/settings.py`:

```python
# Position sizing
max_position_size_pct  = 3.0     # Max % of balance per position
max_positions          = 25      # Max concurrent open positions
kelly_fraction         = 0.25    # Quarter-Kelly multiplier

# Market filtering
min_volume             = 50      # Minimum USDC volume
max_time_to_expiry_days = 365    # Trade any timeline
min_confidence_to_trade = 0.40   # Minimum ensemble confidence

# Edge filtering
MIN_EDGE_REQUIREMENT   = 0.02    # 2% minimum edge to trade

# Risk management
max_daily_loss_pct     = 15.0    # Hard daily loss limit
daily_ai_cost_limit    = 50.0    # Max daily AI API spend (USD)
analysis_cooldown_hours = 0      # 0 = never skip (paper mode)
```

---

## Troubleshooting

**Windows UnicodeEncodeError (emoji crash):**
```bash
python scripts/remove_all_emojis.py
set PYTHONIOENCODING=utf-8
```

**No signals being placed (filters too strict):**
Lower thresholds in `src/config/settings.py`, then reset:
```bash
python scripts/reset_demo.py
python cli.py run --paper
```

**Groq / Gemini 429 rate limit errors:**
Free tier supports approximately 1 request per 6-8 seconds. Built-in throttling handles this automatically. If still seeing errors, increase scan interval in `settings.py`:
```python
scan_interval_seconds = 120
```

**Exit price = 0 for existing positions:**
Clear bad positions and restart:
```bash
python scripts/reset_demo.py
python cli.py run --paper
```

**Auth failed — Non-hexadecimal digit found:**
Check `POLYGON_PRIVATE_KEY` in `.env` — must be 64 raw hex chars with no `0x` prefix and no spaces.

---

## Resources

- [Polymarket CLOB API Docs](https://docs.polymarket.com)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- [Groq Console](https://console.groq.com)
- [Google AI Studio](https://aistudio.google.com)
- [OpenRouter](https://openrouter.ai)
- [xAI API](https://console.x.ai)

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

*Built for the Orderflow 001 — On-Chain Trading Systems Sprint*