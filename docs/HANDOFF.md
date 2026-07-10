# Handoff — Options Risk Range Forecaster

**Purpose of this file:** get a new machine + agent productive without replaying chat history.  
**As of:** 2026-07-10  
**Owner:** Hem Desai · GitHub `clawdius1` · local was `~/Documents/options_risk_forecaster/`

---

## 60-second state

- Project paused for data, then resumed when `risk_range_signals.csv` (1,359 rows) landed.
- Full firm + RV backtest finished: **firm 76.4% vs RV2σ 82.2%** (N=1,350). Firm ≈ 2σ RV width.
- Free **historical IV** sourced from AlphaQuery (not yfinance). Overlap N=549:
  - **IV2σ 83.2% / RV2σ 83.1% / firm 76.0%**
  - Blend best at **w≈0.5 → 84.0%**
- Daily cron **not** configured. Only Hermes cron present elsewhere was a **paused** Kanban processor.
- No paid options API keys on this machine.

---

## New-machine checklist

1. Install: Python 3.9+, `git`, `gh` (optional), network for yfinance + AlphaQuery.
2. Clone:
   ```bash
   git clone https://github.com/clawdius1/options-risk-forecaster.git
   cd options-risk-forecaster
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Sanity:
   ```bash
   python forecast.py --tickers AAPL --weight 0.7
   python run_iv_backtest.py --signals risk_range_signals.csv --iv historical_iv.csv --weight 0.7
   ```
4. Read `docs/RESULTS.md` and `docs/DATA_DICTIONARY.md`.
5. Do **not** re-run contaminated live-IV historical scoring via yfinance option chains.
6. Optional Hermes memory to set on the new host: Career GTM note + this project path + “do not auto-promote Kanban tasks.”

---

## Repo layout

```
options-risk-forecaster/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── forecast.py                 # live CLI forecast
├── backtest.py                 # original slow harness
├── run_full_backtest.py        # batch OHLCV firm+RV (1,359)
├── fetch_historical_iv.py      # AlphaQuery free hist IV
├── run_iv_backtest.py          # hist IV + firm + RV join
├── grid_search_offline.py
├── regression.py
├── risk_range_signals.csv      # PRIMARY firm dataset
├── signals.csv                 # old small sample
├── historical_iv.csv           # AlphaQuery cache
├── backtest_results.csv
├── backtest_results_full.csv
├── backtest_results_hist_iv.csv
└── docs/
    ├── HANDOFF.md              # this file
    ├── RESULTS.md
    ├── DATA_DICTIONARY.md
    └── SESSION_LOG.md
```

---

## Model rules already locked in

| Rule | Detail |
|------|--------|
| Spot | firm `Prev. Close` for band center on backtest dates |
| RV | 20 trading-day std of simple returns (daily; **no** extra /√252 for move) |
| IV move | `S * atm_iv / √252` |
| Default analysis blend | often w=0.7; grid on hist-IV set prefers ~0.5 for 2σ hit |
| Hit definition | next trading-day **close** inside band |
| Exclude set (legacy) | `2026-06-17_NFLX` was a prior typo concern in small sample |

---

## Data provenance

| Source | What | Auth |
|--------|------|------|
| Research firm CSV (`risk_range_signals.csv`) | Date, Ticker, Low, High, Prev. Close | manual drop-in by Hem |
| Yahoo Finance via `yfinance` | OHLCV history for actuals + RV | none |
| AlphaQuery chart API | `/data/option-statistic-chart?ticker=&perType=30-Day&identifier=iv-mean` (+ call/put/HV) | free unauthenticated; ~62d window |
| yfinance `option_chain` | live only | **contaminated for history** — do not use for past dates |

---

## Open decisions / next work

1. **Paid hist IV** for Dec 2025–Apr 2026 gap (~800 rows) if full-period IV vs firm is required.
2. ~~Pierce rates on next-day **high/low** not just close.~~ **Done 2026-07-10** — `analyze_extensions.py`, RESULTS.md §C. Ranking vs firm unchanged, gap wider.
3. ~~Lookback grid on RV (10/20/30/60d) vs firm.~~ **Done 2026-07-10** — RESULTS.md §D. 60d wins monotonically; new recommended default.
4. Optional: DTE filter if using options chains with paid data.
5. Optional: morning cron to append daily forecasts (user previously held this off while gathering firm points).
6. Optional: earnings-calendar filter (pierces likely cluster on earnings days).

---

## Hermes / agent operating notes

- Persona: Clawdius — bullets, no praise, confidence levels, numbers by materiality.
- Preference: local project paths under `~/Documents/<project>/`; structured CSVs.
- Kanban: user creates tasks but moves them **manually** — never auto-promote/auto-process.
- Prefer model `qwen/qwen3.6-plus` for complex reasoning when available.
- Prior related project (separate repo): `clawdius1/agentic-commerce-fraud` (issuer agent-commerce fraud ATC work).

---

## Commands the next agent should reuse (not reinvent)

```bash
# Refresh free IV + rerun IV backtest
python fetch_historical_iv.py --per-type 30-Day --out historical_iv.csv
python run_iv_backtest.py --signals risk_range_signals.csv --iv historical_iv.csv \
  --weight 0.7 --save-csv backtest_results_hist_iv.csv

# Full firm+RV only
python run_full_backtest.py --signals risk_range_signals.csv --weight 0.7 \
  --save-csv backtest_results_full.csv
```

---

## What “done” looked like for the hist-IV step

- Source historical IV without inventing paid access
- Join to firm signals on `(date, ticker)`
- Score firm / IV1 / IV2 / RV1 / RV2 / blend on shared subset
- Offline weight grid without refetch
- Univariate |ret| predictability for atm_iv, rv_ann, iv_rv_spread
- Persist results CSVs + document caveats
