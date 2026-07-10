# Options Risk Range Forecaster

Blends **options-implied vol** with **realized vol** to forecast next-day risk ranges, then backtests against a research firm’s published low/high bands.

**Owner:** Hem Desai (`hem.desai@sas.com`)  
**GitHub:** `clawdius1/options-risk-forecaster`  
**Local path (source machine):** `~/Documents/options_risk_forecaster/`

---

## What this repo is

| Layer | Purpose |
|-------|---------|
| `forecast.py` | Live next-day IV/RV range forecast from yfinance |
| `backtest.py` | Original per-row backtest (slow; scrapes options per row) |
| `run_full_backtest.py` | Fast full-period firm + RV backtest (batch OHLCV) |
| `fetch_historical_iv.py` | Free historical IV pull from AlphaQuery |
| `run_iv_backtest.py` | Proper hist-IV vs firm vs RV backtest |
| `regression.py` / `grid_search_offline.py` | Offline analysis helpers |
| CSVs | Signals, IV cache, and backtest outputs |

## Data files

| File | Role | Keep? |
|------|------|-------|
| `risk_range_signals.csv` | Full firm sample (1,359 rows; 2025-12-01 → 2026-07-10; 9 tickers) | **yes – primary** |
| `signals.csv` | Older small sample (Jun 2026 only) | archive |
| `historical_iv.csv` | AlphaQuery 30D iv-mean cache (2026-04-10 → 2026-07-09) | **yes** |
| `backtest_results.csv` | Old small-sample run | archive |
| `backtest_results_full.csv` | Firm + RV full-period results (N=1,350 actuals) | **yes** |
| `backtest_results_hist_iv.csv` | Hist-IV head-to-head (N=549 overlap) | **yes** |

Tickers: `AAPL AMZN GOOGL META MSFT NFLX NVDA ORCL TSLA`

---

## Quick start (new machine)

```bash
git clone https://github.com/clawdius1/options-risk-forecaster.git
cd options-risk-forecaster

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Live forecast
python forecast.py --tickers TSLA NVDA META --weight 0.7

# Full firm + RV backtest (no hist IV)
python run_full_backtest.py \
  --signals risk_range_signals.csv \
  --weight 0.7 \
  --save-csv backtest_results_full.csv

# Refresh free hist IV then proper IV backtest
python fetch_historical_iv.py --per-type 30-Day --out historical_iv.csv
python run_iv_backtest.py \
  --signals risk_range_signals.csv \
  --iv historical_iv.csv \
  --weight 0.7 \
  --save-csv backtest_results_hist_iv.csv
```

Python **3.9+** works (developed on 3.9/3.11). No API keys needed for the free path.

---

## Core model

- **IV 1-day move:** `S * ATM_IV / sqrt(252)`
- **RV 1-day move:** `S * sigma_daily` (std of last 20 simple returns; already daily)
- **Blend:** `w * move_iv + (1-w) * move_rv` (default analysis weight often `0.7`; grid favors ~`0.5` on 2σ)
- Bands: 1σ ≈ 68%, 2σ ≈ 95% if normal; hit rates are empirical, not assumed

AlphaQuery field used as ATM proxy: **`30-Day iv-mean`** (also caches call/put IV, IV skew when present, and AQ historical vol).

---

## Latest results (as of 2026-07-10)

### Full firm + RV (OHLCV only, N=1,350) — confidence: **high**

| Method | Hit rate | Mean width |
|--------|----------|------------|
| Research Firm | 76.4% | $27.00 |
| RV 2σ | **82.2%** | $26.70 |
| RV 1σ | 52.2% | $13.35 |

Firm width ≈ **exactly 2σ of 20d RV** (firm/RV2 width ratio ≈ **1.01×**).

### Historical IV overlap (N=549, 2026-04-10 → 2026-07-09) — confidence: **high**

| Method | Hit rate | Mean width |
|--------|----------|------------|
| Blend 2σ (best w≈0.5) | **84.0%** | $29.91 |
| IV 2σ (hist) | **83.2%** | $29.52 |
| RV 2σ | **83.1%** | $30.30 |
| Research Firm | **76.0%** | $30.70 |

### Material conclusions

1. Sample-size problem is **solved** for firm + RV (1,350 obs).
2. Firm product ≈ **2σ realized-vol bands**, not clear 2σ-IV alpha over a statistical baseline.
3. On the free hist-IV window, **IV 2σ and RV 2σ both beat the firm** at similar width (~83% vs 76%).
4. **IV does not dominate RV** in coverage (tie); IV is a better predictor of **|next-day return|** (R² 0.148 vs 0.101). **IV−RV spread** has ~0 R² alone.
5. **Do not trust yfinance ATM IV for historical rows** — live chain only. Always use `historical_iv.csv` / paid feed for history.

### Hard limit

AlphaQuery free window ≈ **62 trading days**. Full Dec 2025 → Apr 2026 IV gap needs paid hist options (Polygon Options / ThetaData / ORATS / Tradier).

---

## Project docs (migration)

| Doc | Contents |
|-----|----------|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Full state, decisions, Hermes session context, migrate checklist |
| [`docs/RESULTS.md`](docs/RESULTS.md) | Tabular results detail + method caveats |
| [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) | Column definitions for all CSVs |
| [`docs/SESSION_LOG.md`](docs/SESSION_LOG.md) | Conversation/work log for this project |

Read **`docs/HANDOFF.md` first** on a new machine.

---

## Status

| Item | State |
|------|--------|
| Live forecast CLI | working |
| Firm CSV (1,359) | loaded |
| Full firm/RV backtest | complete |
| Free hist IV + IV backtest | complete on ~3mo window |
| Daily cron accumulation | **not** set up (on hold) |
| Paid full-history IV | **blocked** on API key |

## References

- Bollerslev, Tauchen & Zhou (2009) — IV−RV spread / variance risk premium
- Pan & Poteshman (2006) — option volume information
- Roll, Schwartz & Subrahmanyam — O/S ratio
- Poon & Granger (2003/2005) — IV vs historical vol survey (no consensus winner)

This harness is a **comparison tool**, not a guaranteed edge claim.
