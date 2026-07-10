# Hedgeye Risk Range™ Signals — official process notes

Source: Hedgeye's product write-up (provided by Hem, 2026-07-10). Condensed for
encoding their playbook as (a) the fair benchmark and (b) rules for our own system.

## Model inputs (their description)

- Rate of change of **Price, Volume, Volatility** ("PVV") + proprietary fractal patterns.
- NOT a single-factor price-momentum model (their claim; our deduction on Sep–Nov 2025
  data still hits 94% with momentum features alone — see `deduce_trend.py`).

## Durations

| Duration | Horizon | Deliverable |
|---|---|---|
| TRADE | ≤3 weeks | the Risk Range itself (LRR = buy zone, TRR = sell zone) |
| TREND | ≥3 months | the Bullish/Bearish/Neutral flag |
| TAIL | ≤3 years | (not in the daily signal product) |

## Their stated range dynamics (testable)

- Bullish environment (price+volume up, volatility down): ranges **narrow**.
- Bearish environment (price down, volume+volatility up): ranges **widen**.

## The playbook (encode exactly this as the benchmark)

1. **Trend tells you WHAT, range tells you WHEN.**
2. BULLISH trend: buy/add near LRR (low end), sell/trim near TRR (high end).
3. BEARISH trend: do not buy. Reduce/exit; short near TRR, cover near LRR.
   They avoid shorting BULLISH-trend assets.
4. NEUTRAL: wait and watch. Middle of range: wait zone.
5. Trend change TO BULLISH → start buying dips; TO BEARISH → exit/short; TO NEUTRAL → wait.
6. **Ideal buy setup:** bullish on TRADE and TREND + higher lows/higher highs + price at/near LRR.
7. **#OUTBUCKET** = trend broken, not actionable until reinstated.

## Convergences with our independent findings

- Their "only buy dips in bullish trend" = our v2 fade leg's 200d-SMA regime filter
  (independently derived, improved our results).
- Their band width ≈ 2σ of 20d realized vol (firm/RV2 width ratio 1.01 on N=1,350).
- Their TREND flag ≈ price vs ~100d SMA + momentum confirms (87% single rule, 94% tree).

## FAQ details (from their site)

- **Exact buy rule:** bullish TREND + price at/near LRR = "buying the dip." No other buy condition.
- **Exact short rule:** bearish TREND + price rallies to TRR → short there ("immediate-term
  overbought within a bearish intermediate trend"). Never short bullish-trend assets.
- **Why ranges move daily:** the model widens with rising volatility, narrows when vol
  collapses — stated goal is to "front-run risk." (Matches our finding: width ≈ 3.5–4×
  trailing daily vol, i.e. a ~2σ vol-scaled band recomputed daily.)
- **FX convention:** pair BULLISH = first currency strengthens vs second (EUR/USD bullish
  = euro strengthens).

## Open questions for the full 2020+ dataset

1. Does their flag LEAD or LAG the simple momentum rule at flips? (Lead = real info.)
2. Verify "bullish → narrower ranges" after conditioning width on realized vol.
3. Is the ~6% of flags the momentum tree gets wrong systematic (volume/volatility
   conditions per PVV) or noise?
4. Do LRR/TRR placements skew asymmetrically vs prev_close by trend (buy zone deeper
   in bearish etc.)?
