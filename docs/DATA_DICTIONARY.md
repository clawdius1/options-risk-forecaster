# Data dictionary

## `risk_range_signals.csv` / `signals.csv` (firm)

| Column | Type | Description |
|--------|------|-------------|
| Date | date (`M/D/YYYY` or parseable) | Signal date (prior trading day context) |
| Ticker | string | Equity ticker (may include trailing space — parsers strip) |
| Low | float | Firm band low |
| High | float | Firm band high |
| Prev. Close | float | Prior close used as firm anchor |

Primary file: **`risk_range_signals.csv`** (1,359 × 5).  
Legacy small file: `signals.csv`.

---

## `historical_iv.csv` (AlphaQuery cache)

| Column | Type | Description |
|--------|------|-------------|
| date | date | Session date |
| ticker | string | Uppercase ticker |
| atm_iv | float | 30-Day **iv-mean** (annualized decimal, e.g. 0.28 = 28%) |
| call_iv | float | 30-Day iv-call |
| put_iv | float | 30-Day iv-put |
| iv_skew | float | 30-Day iv-mean-skew (nulls possible) |
| aq_hist_vol | float | AlphaQuery historical-volatility series at same tenor window |

Fetched by `fetch_historical_iv.py`.  
Endpoint: `https://www.alphaquery.com/data/option-statistic-chart?ticker=…&perType=30-Day&identifier=iv-mean`

Observed free span when last pulled (2026-07-10): **2026-04-10 → 2026-07-09**, 62 rows/ticker.

---

## `backtest_results_full.csv` (firm + RV batch)

Key columns (subset):

| Column | Description |
|--------|-------------|
| date, ticker | Join keys |
| firm_low, firm_high, prev_close | Firm inputs |
| next_date, actual_close, actual_high, actual_low | Next session OHLC (close used for hits) |
| sigma_daily | 20d RV daily std |
| rv_1s_low/high, rv_2s_low/high | RV bands |
| iv_* , blend_* | **Mostly contaminated** if from live scar path — prefer hist IV file for IV conclusions |
| iv_status | `recent_live` / `contaminated_live` / `missing` |
| firm_hit, rv_*_hit, iv_*_hit, blend_*_hit | Boolean hit flags |

---

## `backtest_results_hist_iv.csv` (proper IV join)

Includes firm fields + `atm_iv`/`call_iv`/`put_iv`/`iv_skew` from historical cache + RV + IV + blend bands and hit flags.  
Use this for any **IV vs firm** statement.

Hit rule for all `*_hit` columns:  
`firm_low/high or model band contains actual_close`.

---

## `backtest_results.csv` (legacy)

Early small-sample run (Jun 2026 slice). Superseded by the two files above for reporting.

---

## Math fields (derived, not always stored raw)

| Symbol | Formula |
|--------|---------|
| move_iv | `prev_close * atm_iv / sqrt(252)` |
| move_rv | `prev_close * sigma_daily` |
| move_blend | `w * move_iv + (1-w) * move_rv` |
| kσ band | `[S - k*move, S + k*move]`, k∈{1,2} |
| rv_ann | `sigma_daily * sqrt(252)` |
| iv_rv_spread | `atm_iv - rv_ann` |
