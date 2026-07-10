#!/usr/bin/env python3
"""
v2 of the volume+velocity strategy: regime filter, earnings handling,
walk-forward validation, and the head-to-head test vs trading Hedgeye's
published risk ranges (buy low end / sell mid or high end).

Builds on vv_strategy.py (v1 engine). Long-only, stocks only, daily bars,
entry next open, 2bps/side.

Success criterion (user, 2026-07-10): beat the Hedgeye-range playbook as a
TRADING STRATEGY on the window where their ranges exist.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tabulate import tabulate

from vv_strategy import (TICKERS, download, features, load_firm, perf,
                         run_trades, sleeve_curve, trade_stats)

ROOT = Path(__file__).resolve().parent
EARNINGS_CACHE = ROOT / "earnings_dates.csv"


# ---------------------------------------------------------------- earnings

def fetch_earnings(tickers: list[str]) -> dict[str, set]:
    if EARNINGS_CACHE.exists():
        df = pd.read_csv(EARNINGS_CACHE, parse_dates=["date"])
        print(f"Earnings dates: loaded {len(df)} from cache {EARNINGS_CACHE.name}")
        return {t: set(g["date"].dt.normalize()) for t, g in df.groupby("ticker")}
    rows = []
    for t in tickers:
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=28)
            if ed is not None and not ed.empty:
                for d in pd.to_datetime(ed.index).tz_localize(None).normalize().unique():
                    rows.append({"ticker": t, "date": d})
                print(f"  {t}: {len(ed)} earnings dates")
        except Exception as e:
            print(f"  WARN {t}: earnings fetch failed ({e})")
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(EARNINGS_CACHE, index=False)
        print(f"Cached {len(df)} earnings dates → {EARNINGS_CACHE.name}")
    return {t: set(g["date"]) for t, g in df.groupby("ticker")} if not df.empty else {}


def near_earnings(index: pd.DatetimeIndex, dates: set, days: int = 1) -> pd.Series:
    """True if the day is within ±days calendar days of an earnings date."""
    mask = pd.Series(False, index=index)
    for d in dates:
        lo, hi = d - pd.Timedelta(days=days), d + pd.Timedelta(days=days)
        mask.loc[(index >= lo) & (index <= hi)] = True
    return mask


# ---------------------------------------------------------------- signals v2

def features_v2(h: pd.DataFrame, sigma_lb: int, vol_lb: int) -> pd.DataFrame:
    df = features(h, sigma_lb, vol_lb)
    df["sma200"] = df["Close"].rolling(200).mean()
    return df


def gen_signals_v2(df: pd.DataFrame, trigger: float, quiet_max: float, heavy_min: float,
                   trend_filter: bool, earnings: set | None) -> pd.DataFrame:
    down = df["std_move"] <= -trigger
    up = df["std_move"] >= trigger
    fade = down & (df["vol_ratio"] < quiet_max)
    if trend_filter:
        fade = fade & (df["Close"] > df["sma200"])  # only fade dips in uptrends
    brk = up & (df["vol_ratio"] >= heavy_min)
    out = pd.DataFrame(index=df.index)
    if earnings:
        ok = ~near_earnings(df.index, earnings)
        fade, brk = fade & ok, brk & ok
    out["fade"] = fade
    out["brk"] = brk
    out["combo"] = fade | brk
    return out


# ---------------------------------------------------------------- scoring

def score(hist: dict, sigs: dict, strat: str, hold: int, cost_bps: float,
          index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict, dict]:
    curves, trades_list = [], []
    for t, h in hist.items():
        tr = run_trades(h, sigs[t][strat], hold, cost_bps)
        if not tr.empty:
            tr["ticker"] = t
            trades_list.append(tr)
        curves.append(sleeve_curve(h, tr, index))
    port = pd.concat(curves, axis=1).mean(axis=1)
    trades = pd.concat(trades_list, ignore_index=True) if trades_list else pd.DataFrame()
    return trades, perf(port), trade_stats(trades)


def fmt_row(name, ts, pf):
    return [name, ts.get("n", 0),
            f"{ts.get('win', np.nan):.1%}" if ts.get("n") else "—",
            f"{ts.get('avg', np.nan):+.2%}" if ts.get("n") else "—",
            f"{pf.get('total', np.nan):+.1%}", f"{pf.get('cagr', np.nan):+.1%}",
            f"{pf.get('sharpe', np.nan):.2f}", f"{pf.get('max_dd', np.nan):.1%}"]


HEADERS = ["Strategy", "Trades", "Win%", "Avg/trade", "Total", "CAGR", "Sharpe", "MaxDD"]


# ---------------------------------------------------------------- hedgeye playbook

def hedgeye_trades(h: pd.DataFrame, firm: pd.DataFrame, ticker: str, mode: str,
                   hold_max: int, cost_bps: float) -> pd.DataFrame:
    """Trade Hedgeye ranges the way they are meant to be used.

    mode='fade_mid' : close <= published low -> buy next open, exit first close >=
                      that day's published range mid (or hold_max sessions).
    mode='fade_hold': same entry, fixed hold_max sessions (apples-to-apples with ours).
    mode='brk_hold' : close >= published high -> buy next open, fixed hold.
    """
    f = firm[firm["ticker"] == ticker].set_index("date").sort_index()
    lo = f["firm_low"].reindex(h.index)
    hi = f["firm_high"].reindex(h.index)
    mid = (lo + hi) / 2
    if mode.startswith("fade"):
        sig = h["Close"] <= lo
    else:
        sig = h["Close"] >= hi
    idx = h.index
    trades, in_pos_until = [], -1
    for i in np.flatnonzero(sig.fillna(False).to_numpy()):
        if i + 1 >= len(idx) or i + 1 <= in_pos_until:
            continue
        entry_i = i + 1
        buy = float(h["Open"].iloc[entry_i])
        exit_i = min(entry_i + hold_max - 1, len(idx) - 1)
        if mode == "fade_mid":
            for j in range(entry_i, min(entry_i + hold_max, len(idx))):
                tgt = mid.iloc[j]
                if pd.notna(tgt) and float(h["Close"].iloc[j]) >= float(tgt):
                    exit_i = j
                    break
        sell = float(h["Close"].iloc[exit_i])
        trades.append({"signal_date": idx[i], "entry_date": idx[entry_i], "exit_date": idx[exit_i],
                       "buy": buy, "sell": sell, "ret": sell / buy - 1 - 2 * cost_bps / 1e4,
                       "days_held": exit_i - entry_i + 1})
        in_pos_until = exit_i
    return pd.DataFrame(trades)


def score_hedgeye(hist: dict, firm: pd.DataFrame, mode: str, hold_max: int,
                  cost_bps: float, index: pd.DatetimeIndex) -> tuple[dict, dict]:
    curves, trades_list = [], []
    for t, h in hist.items():
        tr = hedgeye_trades(h, firm, t, mode, hold_max, cost_bps)
        if not tr.empty:
            tr["ticker"] = t
            trades_list.append(tr)
        curves.append(sleeve_curve(h, tr, index))
    port = pd.concat(curves, axis=1).mean(axis=1)
    trades = pd.concat(trades_list, ignore_index=True) if trades_list else pd.DataFrame()
    return perf(port), trade_stats(trades)


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(description="v2: regime+earnings filters, walk-forward, vs Hedgeye playbook")
    p.add_argument("--start", default="2020-06-01")  # extra runway for 200d SMA warmup
    p.add_argument("--train-end", default="2024-12-31")
    p.add_argument("--sigma-lookback", type=int, default=60)
    p.add_argument("--vol-lookback", type=int, default=20)
    p.add_argument("--cost-bps", type=float, default=2.0)
    p.add_argument("--firm-signals", default=str(ROOT / "risk_range_signals.csv"))
    p.add_argument("--no-earnings-filter", action="store_true")
    args = p.parse_args(argv)

    hist = download(TICKERS, args.start)
    index = pd.DatetimeIndex(sorted(set().union(*[h.index for h in hist.values()])))
    feats = {t: features_v2(h, args.sigma_lookback, args.vol_lookback) for t, h in hist.items()}
    earnings = {} if args.no_earnings_filter else fetch_earnings(TICKERS)

    train_end = pd.Timestamp(args.train_end)
    tr_idx = index[index <= train_end]
    te_idx = index[index > train_end]
    tr_hist = {t: h[h.index <= train_end] for t, h in hist.items()}
    te_hist = {t: h[h.index > train_end] for t, h in hist.items()}

    # ---------------- walk-forward grid on TRAIN
    grid = [(trig, qm, hm, hold)
            for trig in (1.5, 2.0, 2.5)
            for qm in (1.2, 1.5)
            for hm in (1.5, 2.0)
            for hold in (3, 5)]
    print(f"\n{'=' * 100}")
    print(f"  WALK-FORWARD  train {args.start} → {args.train_end}  |  test {args.train_end} → today")
    print(f"  filters: fade only above 200d SMA; skip signals within ±1d of earnings"
          f"{' (DISABLED)' if args.no_earnings_filter else ''}")
    print(f"{'=' * 100}\n")
    results = []
    for trig, qm, hm, hold in grid:
        sigs = {t: gen_signals_v2(f, trig, qm, hm, True, earnings.get(t)) for t, f in feats.items()}
        tr_sigs = {t: s.reindex(tr_idx).fillna(False) for t, s in sigs.items()}
        trades, pf, ts = score(tr_hist, tr_sigs, "combo", hold, args.cost_bps, tr_idx)
        if ts.get("n", 0) >= 30:
            results.append({"trigger": trig, "quiet_max": qm, "heavy_min": hm, "hold": hold,
                            "n": ts["n"], "win": ts["win"], "avg": ts["avg"],
                            "sharpe": pf.get("sharpe", np.nan), "total": pf.get("total", np.nan),
                            "max_dd": pf.get("max_dd", np.nan)})
    res = pd.DataFrame(results).sort_values("sharpe", ascending=False).reset_index(drop=True)
    disp = res.head(8).copy()
    for c, f in (("win", "{:.1%}"), ("avg", "{:+.2%}"), ("total", "{:+.1%}"), ("max_dd", "{:.1%}")):
        disp[c] = disp[c].map(f.format)
    disp["sharpe"] = disp["sharpe"].map("{:.2f}".format)
    print("  Top train configs (combo, min 30 trades):")
    print(tabulate(disp, headers="keys", tablefmt="simple", showindex=False))
    res.to_csv(ROOT / "vv_walkforward_grid.csv", index=False, float_format="%.6f")

    best = res.iloc[0]
    trig, qm, hm, hold = float(best["trigger"]), float(best["quiet_max"]), float(best["heavy_min"]), int(best["hold"])
    print(f"\n  Chosen on train Sharpe: trigger={trig}σ quiet<{qm} heavy≥{hm} hold={hold}")

    # ---------------- out-of-sample TEST
    sigs = {t: gen_signals_v2(f, trig, qm, hm, True, earnings.get(t)) for t, f in feats.items()}
    te_sigs = {t: s.reindex(te_idx).fillna(False) for t, s in sigs.items()}
    rows = []
    for strat in ("fade", "brk", "combo"):
        trades, pf, ts = score(te_hist, te_sigs, strat, hold, args.cost_bps, te_idx)
        rows.append(fmt_row(f"vv_{strat} (oos)", ts, pf))
    bh = pd.concat([h["Close"].pct_change().reindex(te_idx) for h in te_hist.values()], axis=1).mean(axis=1).fillna(0)
    rows.append(fmt_row("buy_hold_eqw", {}, perf(bh)))
    print(f"\n  OUT-OF-SAMPLE {te_idx.min().date()} → {te_idx.max().date()} (params frozen from train):")
    print(tabulate(rows, headers=HEADERS, tablefmt="simple"))

    # ---------------- HEAD-TO-HEAD vs Hedgeye playbook on the firm window
    firm = load_firm(Path(args.firm_signals))
    fstart, fend = firm["date"].min(), firm["date"].max()
    fidx = index[(index >= fstart) & (index <= fend + pd.Timedelta(days=10))]
    fhist = {t: h[h.index.isin(fidx)] for t, h in hist.items()}
    f_sigs = {t: sigs[t].reindex(fidx).fillna(False) for t in TICKERS}

    print(f"\n{'=' * 100}")
    print(f"  HEAD-TO-HEAD vs HEDGEYE PLAYBOOK  {fstart.date()} → {fend.date()}  (their range window)")
    print(f"{'=' * 100}\n")
    rows = []
    for strat in ("fade", "brk", "combo"):
        trades, pf, ts = score(fhist, f_sigs, strat, hold, args.cost_bps, fidx)
        rows.append(fmt_row(f"vv_{strat} (ours)", ts, pf))
    for mode, label in (("fade_mid", "hedgeye buy-low sell-mid"),
                        ("fade_hold", f"hedgeye buy-low hold-{hold}"),
                        ("brk_hold", f"hedgeye buy-break-high hold-{hold}")):
        pf, ts = score_hedgeye(fhist, firm, mode, hold if mode != "fade_mid" else 5,
                               args.cost_bps, fidx)
        rows.append(fmt_row(label, ts, pf))
    bh = pd.concat([h["Close"].pct_change().reindex(fidx) for h in fhist.values()], axis=1).mean(axis=1).fillna(0)
    rows.append(fmt_row("buy_hold_eqw", {}, perf(bh)))
    print(tabulate(rows, headers=HEADERS, tablefmt="simple"))
    print("\n  NOTE: window is ~150 trading days — treat as a scrimmage, not the season.")


if __name__ == "__main__":
    main()
