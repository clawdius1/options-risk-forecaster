# Results detail — Options Risk Range Forecaster

**Run date:** 2026-07-10  
**Code:** `run_full_backtest.py`, `run_iv_backtest.py`

Confidence labels: **high** = large clean sample / clear computation · **moderate** = small window or method sensitivity · **low** / **unknown** = not established.

---

## A. Full period firm + RV (historical OHLCV only)

**Inputs:** `risk_range_signals.csv` (1,359 rows)  
**Valid with next-day close:** **1,350**  
**Range:** 2025-12-01 → 2026-07-10  
**Lookback RV:** 20d  
**Confidence: high**

| Method | Hits | N | Hit rate | Mean width |
|--------|------|---|----------|------------|
| Research Firm | 1031 | 1350 | **76.4%** | $27.00 |
| RV 2σ | 1110 | 1350 | **82.2%** | $26.70 |
| RV 1σ | 705 | 1350 | 52.2% | $13.35 |

### Width calibration

| Ratio | Value |
|-------|--------|
| firm / RV1 mean width | **2.02×** |
| firm / RV2 mean width | **1.01×** |

**Interpretation:** firm bands are economically **2σ of 20d realized vol**, not an independent exotic construction.

### Firm hit by ticker (full)

| Ticker | N | Firm hit | Mean firm width |
|--------|---|----------|-----------------|
| AAPL | 150 | 74.7% | $16.13 |
| AMZN | 150 | 79.3% | $19.29 |
| GOOGL | 150 | 77.3% | $26.72 |
| META | 150 | 72.7% | $56.48 |
| MSFT | 150 | 75.3% | $31.53 |
| NFLX | 150 | 79.3% | $9.21 |
| NVDA | 150 | 76.0% | $16.70 |
| ORCL | 150 | 76.0% | $26.93 |
| TSLA | 150 | 76.7% | $40.00 |

### Firm hit by month (full)

| Month | N | Firm hit |
|-------|---|----------|
| 2025-12 | 198 | 82.8% |
| 2026-01 | 180 | 70.0% |
| 2026-02 | 171 | 76.6% |
| 2026-03 | 198 | 78.3% |
| 2026-04 | 189 | **68.3%** (worst) |
| 2026-05 | 180 | 80.6% |
| 2026-06 | 180 | 77.2% |
| 2026-07 | 54 | 77.8% (partial) |

**Artifact:** `backtest_results_full.csv`

**Note:** live yfinance IV scars applied to history were logged but **must not** be used for conclusions (1,314/1,350 `contaminated_live`).

---

## B. Historical IV head-to-head (AlphaQuery)

**IV source:** AlphaQuery `perType=30-Day`, `identifier=iv-mean` (+ call/put/HV where available)  
**IV window:** 2026-04-10 → 2026-07-09  
**Rows with hist IV + actual:** **549**  
**Default reported blend weight:** 0.70 (grid also run offline)  
**Confidence: high** on method + this window; **moderate** for full-sample generalization (only ~3 months of free IV)

| Method | Hits | N | Hit rate | Mean width |
|--------|------|---|----------|------------|
| Research Firm | 417 | 549 | **76.0%** | $30.70 |
| IV 2σ (hist) | 457 | 549 | **83.2%** | $29.52 |
| RV 2σ | 456 | 549 | **83.1%** | $30.30 |
| Blend 2σ w=0.70 | 459 | 549 | **83.6%** | $29.75 |
| IV 1σ | 292 | 549 | 53.2% | $14.76 |
| RV 1σ | 281 | 549 | 51.2% | $15.15 |
| Blend 1σ w=0.70 | 287 | 549 | 52.3% | $14.87 |

### Same-width material ratios (IV subset)

| Metric | Value |
|--------|--------|
| Firm mean width | $30.70 |
| IV2 mean width | $29.52 (firm/IV2 ≈ **1.04×**) |
| RV2 mean width | $30.30 (firm/RV2 ≈ **1.01×**) |

### By ticker (IV-covered)

| Ticker | N | Firm | IV2σ | RV2σ | Blend2σ | Avg IV |
|--------|---|------|------|------|---------|--------|
| AAPL | 61 | 69% | 85% | 84% | 84% | 25.3% |
| AMZN | 61 | 82% | 93% | 89% | 92% | 34.5% |
| GOOGL | 61 | 82% | 85% | 82% | 85% | 33.7% |
| META | 61 | 77% | 77% | 82% | 80% | 37.0% |
| MSFT | 61 | 77% | 82% | 82% | 84% | 33.4% |
| NFLX | 61 | 77% | 89% | 84% | 85% | 35.8% |
| NVDA | 61 | 74% | 87% | 84% | 89% | 40.4% |
| ORCL | 61 | 70% | 75% | 85% | 77% | 61.5% |
| TSLA | 61 | 75% | 75% | 77% | 77% | 45.2% |

### By month (IV-covered)

| Month | N | Firm | IV2σ | RV2σ | Blend2σ |
|-------|---|------|------|------|---------|
| 2026-04 | 135 | 67.4% | 81.5% | 79.3% | 81.5% |
| 2026-05 | 180 | 80.6% | 88.9% | 86.7% | 88.3% |
| 2026-06 | 180 | 77.2% | 77.2% | 79.4% | 78.3% |
| 2026-07 | 54 | 77.8% | 88.9% | 92.6% | 90.7% |

### Offline blend weight grid (2σ hit, N=549)

| w(IV) | Hit rate | Mean width |
|-------|----------|------------|
| 0.0 (pure RV) | 83.1% | $30.30 |
| 0.1 | 83.2% | $30.22 |
| 0.2 | 83.4% | $30.14 |
| 0.3 | 83.4% | $30.06 |
| 0.4 | 83.4% | $29.98 |
| **0.5** | **84.0%** | $29.91 |
| 0.6 | 83.8% | $29.83 |
| 0.7 | 83.6% | $29.75 |
| 0.8 | 83.2% | $29.67 |
| 0.9 | 83.2% | $29.59 |
| 1.0 (pure IV) | 83.2% | $29.52 |

### Predictability of next-day |return|

| Variable | corr | R² |
|----------|------|----|
| atm_iv | +0.385 | **0.148** |
| rv_ann (20d) | +0.319 | 0.101 |
| iv_rv_spread | +0.001 | **0.000** |

**Artifact:** `backtest_results_hist_iv.csv`  
**IV cache:** `historical_iv.csv`

---

## C. Earlier small-sample claim (superseded for N, partially confirmed for direction)

Original mini-sample (~79 valid rows, mid-Jun 2026 firm lanes): IV2σ appeared to beat firm (~89.9% vs 84.8%). Methods then used **live** IV badly mid-backtest; treat that mini-run as exploratory only. Directional “simple 2σ model can beat firm on coverage” holds on the large clean runs; “IV uniquely dominates RV” does **not** hold on hist-IV window.

---

## D. Caveats (do not drop)

1. AlphaQuery free history length ~**62 sessions** — not firm full sample.
2. `30-Day iv-mean` is **mean IV for ~30D tenor**, not a pure ATM mark from a single strike/expiry.
3. Hit tests use **close-in-band**, not high/low period containment.
4. No earnings calendar filter.
5. yfinance history can omit dividends adjustments nuances depending on `auto_adjust` (runs used `auto_adjust=True` in batch scripts).
6. No transaction-cost/signal-publication lag model for firm prints.
