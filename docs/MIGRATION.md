# Machine migration guide

## What to move

| Item | How |
|------|-----|
| This repo | `git clone https://github.com/clawdius1/options-risk-forecaster.git` |
| Secrets | None required for free path. Copy paid API keys only if you add them later (never committed). |
| Hermes memory (optional on agent host) | Career GTM prefs; Kanban manual-only; project path; preferred reasoning model. |
| Agentic commerce project (separate) | `clawdius1/agentic-commerce-fraud` if needed on the new machine |

## What **not** to move

- `.venv/` — always recreate
- Hermes `.env` tokens (Telegram/OpenRouter) unless the **same** Hermes host is being relocated — keep out of this repo
- Browser/cache junk

## Verify after copy

```bash
cd options-risk-forecaster
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
test -f risk_range_signals.csv && test -f historical_iv.csv
python -c "import pandas as pd; print(len(pd.read_csv('risk_range_signals.csv')), len(pd.read_csv('historical_iv.csv')))"
python run_iv_backtest.py --signals risk_range_signals.csv --iv historical_iv.csv --weight 0.7 --save-csv /tmp/smoke_iv.csv | tail -40
```

Expected: signals **1359**, hist IV **558** (or similar if re-fetched), IV-covered actual ~**549**.

## If GitHub is unavailable

Tarball the project excluding venv:

```bash
cd ~/Documents
tar --exclude='options_risk_forecaster/.venv' -czf options_risk_forecaster_migrate.tgz options_risk_forecaster
```

Copy `options_risk_forecaster_migrate.tgz` to the new machine and extract.
