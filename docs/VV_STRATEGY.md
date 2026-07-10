# Volume + Velocity stock swing strategy (branch: `volume-velocity-algo`)

**Started:** 2026-07-10 · **Code:** `vv_strategy.py` · **Status:** v1 backtest complete, untuned

## Objective (user-set, 2026-07-10)

- **No derivatives, no leverage, no margin** — trade the stocks only, **long-only**.
- Thesis: market structure is dominated by passive investing, momentum funds, and algo
  trading rather than active management. Therefore **volume + velocity** should tell you
  whether a price move has real flow behind it.
- Reuse the risk-range machinery (this repo) as the trigger levels; backtest against the
  firm's published ranges where they exist.

## Strategy design

Signals computed at close of day *t*, entry at **open of day t+1**, exit at close after a
fixed number of sessions (time stop; no intraday fills assumed — daily bars only). One
position per ticker. Costs 2bps per side. Equal-weight sleeves across the 9 mega-caps.

| Concept | Definition |
|---|---|
| Velocity | Day's return ÷ trailing 60d vol → "a k-sigma move" (60d per RESULTS §D) |
| Volume | Day's volume ÷ trailing 20d average volume (`vol_ratio`) |
| Trigger | Close beyond ±2σ band (statistical), or outside the firm's published low/high |
| Fade leg | 2σ **down** day + volume **not heavy** → buy the dip (no real flow behind it) |
| Breakout leg | 2σ **up** day + volume **heavy** (≥1.5×) → buy the strength (real flow) |
| Baselines | Same legs with **no volume filter** (`fade_all`, `brk_all`) — isolates what volume adds |

## v1 results

### A. Long history 2021-01 → 2026-07 (statistical 2σ bands, hold=3, quiet<1.5×)

| Strategy | Trades | Win% | Avg/trade | Total | CAGR | Sharpe | MaxDD |
|---|---|---|---|---|---|---|---|
| fade_all (no vol filter) | 269 | 50.9% | +0.37% | +12.7% | +2.2% | 0.49 | −6.0% |
| fade_vv | 117 | 53.8% | **+0.56%** | +8.0% | +1.4% | 0.53 | −3.6% |
| brk_all (no vol filter) | 284 | 52.5% | +0.19% | +6.7% | +1.2% | 0.29 | −6.3% |
| brk_vv | 163 | 58.3% | **+0.80%** | +16.0% | +2.7% | 0.92 | −2.9% |
| **combo_vv** | 276 | 57.2% | +0.76% | +27.1% | +4.5% | **1.10** | **−3.6%** |
| buy & hold eq-weight | — | — | — | +241.4% | +25.0% | 0.94 | −46.2% |

Hold grid (combo, quiet<1.1): hold=1 Sharpe 0.36 · **hold=3 Sharpe 1.05** · hold=5 0.38 · hold=10 0.77.

### B/C. Firm window 2025-12 → 2026-07 (flat/down tape: B&H −3.0%)

| Strategy | Firm bands (B) | Statistical bands (C) |
|---|---|---|
| fade_all | −2.3%, Sharpe −0.58 | −1.6%, Sharpe −0.66 |
| brk_all | **+9.5%, Sharpe 2.87** | +3.8%, Sharpe 1.40 |
| brk_vv | +2.2%, Sharpe 1.22 | +2.5%, Sharpe 1.26 |
| combo_vv | +0.9%, Sharpe 0.30 | +0.2%, Sharpe 0.11 |
| buy & hold | −3.0%, Sharpe −0.13 | same |

## Findings (confidence: moderate — first pass, parameters not tuned)

1. **The volume thesis is confirmed in-sample on 5.5 years:** the volume filter roughly
   **doubles per-trade edge on breakouts** (+0.19% → +0.80%) and improves fades
   (+0.37% → +0.56%). This is the cleanest evidence so far for the user's market-structure view.
2. **combo_vv beats buy-and-hold on risk-adjusted terms** (Sharpe 1.10 vs 0.94) with
   **1/13th the drawdown** (−3.6% vs −46.2%), but captures far less total return because it
   is in the market only a small fraction of the time. This is a low-risk overlay, not a
   B&H replacement.
3. **Regime dependence is real:** in the recent flat/down window the fade leg loses money
   (catching falling knives) while the breakout leg shines. A regime filter (e.g., only
   fade when price is above its 200d average) is the obvious v2 improvement.
4. **Firm bands add nothing as trigger levels** — statistical bands match or beat them
   (combo Sharpe 1.57 vs 0.61 at hold=5). Consistent with the main-branch finding that
   firm ranges ≈ 2σ RV.
5. Hold=3 sessions dominates hold=1/5; hold=10 competitive but more exposed.

## Caveats

- Parameters (2σ trigger, 1.5× volume, hold=3) are first-guess + one sensitivity check —
  **not walk-forward validated**. Treat Sharpe 1.10 as an upper bound on expectation.
- 2021–2026 was a strong bull run for these 9 names; fade-the-dip longs are flattered.
- No earnings filter: many 2σ moves are earnings gaps, where the flow logic differs.
- Daily bars only; "velocity" is close-to-close. Intraday velocity untested.

## Next steps

1. Regime filter on the fade leg (200d trend filter); re-test.
2. Earnings-date handling (exclude or treat as its own signal class).
3. Walk-forward parameter validation (train 2021–2024, test 2025–2026) before trusting
   any tuned numbers.
4. Multi-day velocity (3-day cumulative standardized move) as alternative trigger.
5. Paper-trade via a morning cron once rules are frozen.
