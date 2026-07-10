# Session log — Options Risk Range Forecaster

Chronological decisions and outcomes for machine migration. Not a full transcript dump.

## 2026-06-21 — Project born

- Source brief: `Stock_price_project.md` (options-implied next-day risk range).
- Built `forecast.py`, README, requirements.
- Pilot tickers/philosophy: blend IV+RV; display diagnostics (IV−RV, O/S, put/call, skew).
- First research-firm CSV (`signals.csv`, ~Jun 4–18 2026, 9 names).
- `backtest.py` + offline grid/regression scaffolding.
- Early finding (noisy N≈79): IV 2σ seemed to beat firm; live IV contamination risk noted.
- Explicit next need: **more firm observations** (target 3+ months).

## Pre-2026-07-10 — Hold

- User: working to get more observations; **pause**.
- No daily forecast cron created.
- Hermes environment restarts verified separately (gateway/Telegram); not project-blocking.

## 2026-07-10 — Resume + scale + hist IV + GitHub port

1. User added `risk_range_signals.csv` (1,359 rows; 2025-12-01 → 2026-07-10; 9×151).
2. Created venv, installed deps.
3. Built `run_full_backtest.py` (batch yfinance OHLCV). Result: firm 76.4% vs RV2σ 82.2%; firm≈2σ RV.
4. Confirmed **no** paid options keys in Hermes `.env` or shell.
5. Discovered AlphaQuery free JSON for daily equity IV (~62d).
6. Built `fetch_historical_iv.py` + `historical_iv.csv` + `run_iv_backtest.py`.
7. Result on N=549: IV2σ 83.2%, RV2σ 83.1%, firm 76.0%; best blend w≈0.5 → 84.0%; |ret| R² IV>RV; spread ~0.
8. Packaging for GitHub migration: docs/* , README overhaul, gitignore, requirements pin text, `.env.example`.

## Hermes-adjacent context that is **not** this repo

- Agentic Commerce Fraud project: `~/Documents/agentic_commerce_fraud` → `clawdius1/agentic-commerce-fraud`.
- Kanban auto-processor cron: paused; user prefers **manual** stage moves only.
- Gateway after reboot: launchd serve, port noted later as **8642** (not old 9119).

## Open items carried forward

- Paid hist IV for Dec 2025–Apr 2026 if required.
- No production scheduler for daily forecasts yet.
- Optional lookback / high-low / earnings filters.
