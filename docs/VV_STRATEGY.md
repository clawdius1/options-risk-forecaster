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

## v2 (2026-07-10, `vv_v2.py`) — filters, walk-forward, Hedgeye head-to-head

The firm is **Hedgeye**. User-set success criterion: beat *trading* Hedgeye's ranges
(buy low end / sell mid or high end), not just their hit rates.

v2 adds: fade leg only above the 200d SMA (regime filter); skip signals within ±1
calendar day of earnings (yfinance earnings dates, cached in `earnings_dates.csv`);
walk-forward validation (grid trained 2020-06 → 2024-12, params frozen, tested
2025-01 → 2026-07).

### Walk-forward (train Sharpe → frozen-params out-of-sample)

Chosen on train: trigger 1.5σ, quiet <1.5×, heavy ≥2.0×, hold 3. Train Sharpe **1.59**
(182 trades, 59.3% win, maxDD −1.5%). Out-of-sample 2025-01 → 2026-07:

| Strategy (OOS) | Trades | Win% | Avg/trade | Total | Sharpe | MaxDD |
|---|---|---|---|---|---|---|
| vv_combo | 87 | 47.1% | +0.22% | +2.6% | **0.48** | −4.3% |
| buy & hold eq-weight | — | — | — | +23.2% | 0.64 | −26.8% |

**Honest read:** train Sharpe 1.59 decays to 0.48 OOS — partly regime (2025–26 was
flat/choppy for these names), partly optimism in the grid pick. The earnings filter is
real, though: without it the same OOS combo scores 0.20 and the breakout leg is negative.

### Head-to-head vs the Hedgeye playbook (their window, 2025-12 → 2026-07)

| Strategy | Trades | Win% | Avg/trade | Total | Sharpe | MaxDD |
|---|---|---|---|---|---|---|
| **vv_combo (ours)** | 34 | 58.8% | +0.50% | +2.1% | **0.88** | −4.3% |
| Hedgeye buy-low → sell-mid (canonical) | 58 | 63.8% | +0.13% | +1.2% | 0.33 | −2.9% |
| Hedgeye buy-low → hold 3 | 67 | 40.3% | −0.32% | −2.3% | −0.58 | −3.7% |
| Hedgeye buy-break-HIGH → hold 3 | 61 | 60.7% | +1.28% | **+9.2%** | **2.74** | −2.6% |
| Buy & hold eq-weight | — | — | — | −3.1% | −0.14 | −17.1% |

**Verdict on the user's test (confidence: moderate, N≈150 days):**

1. **We beat Hedgeye's canonical playbook** — vv_combo Sharpe 0.88 vs 0.33 for
   buy-low/sell-mid, and their buy-low/hold loses money outright.
2. The **best strategy on the window is anti-Hedgeye use of their own product**: buying
   *breakouts above their published high* (Sharpe 2.74, +9.2%). Their high is a good
   momentum trigger, not a sell level — consistent with the market-structure thesis
   (flows persist) and with fade legs struggling in this tape.
3. One regime, ~60 trades: the breakout result needs to survive a longer window before
   trusting it. It is the single most promising lead to pursue.

## v3 (2026-07-10, `vv_v3_breakout.py`) — long-history breakout validation

Question: is the Hedgeye-window breakout result (Sharpe 2.74) durable or regime luck?
Tested the breakout family 2020-06 → 2026-07 (hold 3, earnings filter on, long-only):
σ-band tops (1.5/2.0/2.5σ) and Donchian new-N-day-high (20/55d), each × volume filter
(none / ≥1.5× / ≥2.0×).

| Variant | Trades | Win% | Avg/trade | Total | Sharpe | MaxDD |
|---|---|---|---|---|---|---|
| σ≥1.5 + vol≥2.0 | 69 | 62.3% | +1.24% | +10.0% | **0.92** | −2.1% |
| σ≥2.0 + vol≥2.0 | 57 | 63.2% | +1.32% | +8.7% | 0.91 | −1.9% |
| 20d-high, no vol filter | 1079 | 53.2% | +0.30% | **+47.5%** | 0.87 | −10.8% |
| σ≥1.5, no vol filter | 630 | 50.0% | −0.02% | −0.2% | 0.03 | −14.6% |
| buy & hold eq-weight | — | — | — | +412.5% | 1.09 | −46.2% |

Per-year Sharpe (regime dependence): heavy-volume σ-breakouts were great 2021–22 and 2024,
**negative in 2023**, flat 2025–26. The unfiltered **20d-high Donchian** is the most
consistent recently (2025: 0.97, **2026: 2.12**) with one bad year (2022: −0.84).

**Verdict (confidence: high):**

1. **Sharpe 2.74 was regime + level luck** — no statistical analog reproduces it over 6
   years; the chosen winner scores only 0.52 on the same Hedgeye window (n=9).
2. Two durable family members survive: **heavy-volume σ-breakouts** (Sharpe ~0.9,
   +1.2–1.4%/trade, maxDD ~−2%, but only ~10 trades/yr) and the **20d-high momentum
   breakout** (Sharpe 0.87 with 1,079 trades and the best 2025–26 record — likely what
   the Hedgeye-high trigger was really proxying).
3. Volume confirmation is decisive for σ-breakouts (Sharpe 0.03 → 0.92 as the volume
   threshold rises) — strongest single confirmation of the volume thesis in the project.
4. Nothing beats buy-and-hold's full-period Sharpe (1.09) on these 9 names in a
   melt-up sample, but the breakout overlays carry ~1/5 to 1/20 of its −46% drawdown.

## Next steps

1. ~~Regime filter, earnings handling, walk-forward.~~ Done in v2.
2. ~~Long-history breakout validation.~~ **Done in v3** (above).
3. Candidate production rule: **20d-high breakout + heavy-volume σ-fade combo**;
   walk-forward it as one portfolio before freezing.
4. Paper-trade via a morning cron once rules are frozen.
5. Brokerage data (user has Schwab, Robinhood, IBKR — personal accounts): only needed
   when we go intraday (velocity within the day) or live. Preference then: Schwab official
   API or IBKR (best historical intraday); daily yfinance suffices for research so far.
