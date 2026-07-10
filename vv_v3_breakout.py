#!/usr/bin/env python3
"""
v3: long-history validation of the breakout lead from v2.

On the Hedgeye window, "buy a close above the published range high, hold 3" scored
Sharpe 2.74. Hedgeye highs only exist for ~150 days, so this script tests the
breakout FAMILY over ~6 years to separate durable edge from regime luck:

  sigma_k   : close more than k*sigma above the prior close (statistical range top)
  don_N     : close at a new N-day closing high (Donchian breakout — the classic
              "cleared a widely watched level" trigger, closest analog to a
              published range high)

Each trigger is run with no volume filter and with heavy-volume confirmation
(vol_ratio >= 1.5 / 2.0), all with the earnings filter from v2 (skip signals
within +/-1 day of earnings). Entry next open, fixed hold, long-only, 2bps/side.

Outputs: full variant table, per-year Sharpe for the top variants (regime
dependence), and the winner replayed on the Hedgeye window for continuity.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

from vv_strategy import TICKERS, download, load_firm, perf, run_trades, sleeve_curve, trade_stats
from vv_v2 import features_v2, fetch_earnings, near_earnings

ROOT = Path(__file__).resolve().parent


def breakout_signal(df: pd.DataFrame, kind: str, param: float,
                    vol_min: float | None, earnings: set | None) -> pd.Series:
    if kind == "sigma":
        sig = df["std_move"] >= param
    elif kind == "don":
        prior_max = df["Close"].rolling(int(param)).max().shift(1)
        sig = df["Close"] > prior_max
    else:
        raise ValueError(kind)
    if vol_min is not None:
        sig = sig & (df["vol_ratio"] >= vol_min)
    if earnings:
        sig = sig & ~near_earnings(df.index, earnings)
    return sig.fillna(False)


def score(hist, signals, hold, cost_bps, index):
    curves, trades_list = [], []
    for t, h in hist.items():
        tr = run_trades(h, signals[t], hold, cost_bps)
        if not tr.empty:
            tr["ticker"] = t
            trades_list.append(tr)
        curves.append(sleeve_curve(h, tr, index))
    port = pd.concat(curves, axis=1).mean(axis=1)
    trades = pd.concat(trades_list, ignore_index=True) if trades_list else pd.DataFrame()
    return port, trades


def yearly_sharpe(port: pd.Series) -> dict[int, float]:
    out = {}
    for y, g in port.groupby(port.index.year):
        vol = g.std() * np.sqrt(252)
        out[int(y)] = float((g.mean() * 252) / vol) if vol > 0 else np.nan
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Long-history breakout validation")
    p.add_argument("--start", default="2020-06-01")
    p.add_argument("--hold", type=int, default=3)
    p.add_argument("--cost-bps", type=float, default=2.0)
    p.add_argument("--sigma-lookback", type=int, default=60)
    p.add_argument("--vol-lookback", type=int, default=20)
    p.add_argument("--firm-signals", default=str(ROOT / "risk_range_signals.csv"))
    args = p.parse_args(argv)

    hist = download(TICKERS, args.start)
    index = pd.DatetimeIndex(sorted(set().union(*[h.index for h in hist.values()])))
    feats = {t: features_v2(h, args.sigma_lookback, args.vol_lookback) for t, h in hist.items()}
    earnings = fetch_earnings(TICKERS)

    variants = []
    for kind, param, label in (("sigma", 1.5, "σ≥1.5"), ("sigma", 2.0, "σ≥2.0"), ("sigma", 2.5, "σ≥2.5"),
                               ("don", 20, "20d-high"), ("don", 55, "55d-high")):
        for vol_min, vlabel in ((None, "no vol"), (1.5, "vol≥1.5"), (2.0, "vol≥2.0")):
            variants.append((kind, param, vol_min, f"{label} {vlabel}"))

    print(f"\n{'=' * 100}")
    print(f"  BREAKOUT FAMILY — {args.start} → today, hold={args.hold}, earnings filter ON, long-only")
    print(f"{'=' * 100}\n")
    rows, curves = [], {}
    for kind, param, vol_min, label in variants:
        sigs = {t: breakout_signal(f, kind, param, vol_min, earnings.get(t)) for t, f in feats.items()}
        port, trades = score(hist, sigs, args.hold, args.cost_bps, index)
        pf, ts = perf(port), trade_stats(trades)
        curves[label] = port
        rows.append([label, ts.get("n", 0),
                     f"{ts.get('win', np.nan):.1%}" if ts.get("n") else "—",
                     f"{ts.get('avg', np.nan):+.2%}" if ts.get("n") else "—",
                     f"{pf.get('total', np.nan):+.1%}", f"{pf.get('sharpe', np.nan):.2f}",
                     f"{pf.get('max_dd', np.nan):.1%}"])
    bh = pd.concat([h["Close"].pct_change().reindex(index) for h in hist.values()], axis=1).mean(axis=1).fillna(0)
    pf = perf(bh)
    rows.append(["buy_hold_eqw", "—", "—", "—", f"{pf['total']:+.1%}", f"{pf['sharpe']:.2f}", f"{pf['max_dd']:.1%}"])
    print(tabulate(rows, headers=["Variant", "Trades", "Win%", "Avg/trade", "Total", "Sharpe", "MaxDD"],
                   tablefmt="simple"))

    # per-year Sharpe for the top variants
    ranked = sorted(((label, perf(c).get("sharpe", np.nan)) for label, c in curves.items()),
                    key=lambda x: -(x[1] if not np.isnan(x[1]) else -9))
    top = [label for label, _ in ranked[:4]]
    years = sorted(set(index.year))
    print(f"\n  Per-year Sharpe (regime dependence) — top variants + benchmark:")
    yrows = []
    for label in top + ["buy_hold_eqw"]:
        c = curves[label] if label in curves else bh
        ys = yearly_sharpe(c)
        yrows.append([label] + [f"{ys.get(y, np.nan):.2f}" if not np.isnan(ys.get(y, np.nan)) else "—" for y in years])
    print(tabulate(yrows, headers=["Variant"] + [str(y) for y in years], tablefmt="simple"))

    # winner replayed on the Hedgeye window
    firm = load_firm(Path(args.firm_signals))
    fstart, fend = firm["date"].min(), firm["date"].max()
    fidx = index[(index >= fstart) & (index <= fend + pd.Timedelta(days=10))]
    best_label = top[0]
    kind, param, vol_min = next((k, pm, vm) for k, pm, vm, lb in variants if lb == best_label)
    fhist = {t: h[h.index.isin(fidx)] for t, h in hist.items()}
    fsigs = {t: breakout_signal(feats[t], kind, param, vol_min, earnings.get(t)).reindex(fidx).fillna(False)
             for t in TICKERS}
    port, trades = score(fhist, fsigs, args.hold, args.cost_bps, fidx)
    pf, ts = perf(port), trade_stats(trades)
    print(f"\n  Winner '{best_label}' on the Hedgeye window {fstart.date()} → {fend.date()}: "
          f"n={ts.get('n', 0)}, total={pf.get('total', np.nan):+.1%}, sharpe={pf.get('sharpe', np.nan):.2f}, "
          f"maxDD={pf.get('max_dd', np.nan):.1%}")
    print("  (Hedgeye buy-break-high hold-3 on the same window: +9.2%, Sharpe 2.74 — v2 result)")


if __name__ == "__main__":
    main()
