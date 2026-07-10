#!/usr/bin/env python3
"""
Reverse-engineer Hedgeye's TREND flag (BULLISH / BEARISH / NEUTRAL).

Why: the flag drives their playbook — bullish -> buy low end / sell high end;
bearish -> short high end / cover low end. If the flag is predictable from
observable data, we can generate it for ANY ticker and apply the full
conditional playbook without their subscription in the loop.

Two feature families:
  A. Band geometry (works for every instrument, needs only their published numbers):
     skew        = (prev_close - low) / (high - low)   [0 = at low end, 1 = at high end]
     width_rel   = (high - low) / prev_close
     mid_vs_prev = (mid - prev_close) / prev_close
  B. Price history (tickers mappable to yfinance): momentum & moving-average state
     ret_20/60/120, close vs sma20/50/100/200, 20d realized vol

Outputs univariate separability (AUC bullish-vs-bearish per feature), a depth-3
decision tree (interpretable rules), accuracy of the obvious hypotheses, and
what changes on the day the flag FLIPS. Re-run any time; uses hedgeye_rr_full.csv.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

ROOT = Path(__file__).resolve().parent

YF_MAP = {
    "SPX": "^GSPC", "COMPQ": "^IXIC", "RUT": "^RUT", "VIX": "^VIX",
    "NIKK": "^N225", "DAX": "^GDAXI", "SSEC": "000001.SS",
    "GOLD": "GC=F", "SILVER": "SI=F", "COPPER": "HG=F",
    "WTIC": "CL=F", "BRENT": "BZ=F", "NATGAS": "NG=F", "BITCOIN": "BTC-USD",
    # equities/ETFs map to themselves; FX and yields skipped for price features
}
SKIP_PRICE = {"UST30Y", "UST10Y", "UST2Y", "USD", "EUR/USD", "USD/YEN", "GBP/USD", "CAD/USD"}


def yf_symbol(t: str) -> str | None:
    if t in SKIP_PRICE:
        return None
    return YF_MAP.get(t, t)


def load_signals() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "hedgeye_rr_full.csv", parse_dates=["date"])
    df = df[df["trend"].isin(["BULLISH", "BEARISH", "NEUTRAL"])].copy()
    df["skew"] = (df["prev_close"] - df["low"]) / (df["high"] - df["low"])
    df["width_rel"] = (df["high"] - df["low"]) / df["prev_close"]
    df["mid_vs_prev"] = ((df["low"] + df["high"]) / 2 - df["prev_close"]) / df["prev_close"]
    return df


def add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    import yfinance as yf
    feats = []
    for t in sorted(df["ticker"].unique()):
        sym = yf_symbol(t)
        if sym is None:
            continue
        try:
            h = yf.download(sym, start="2012-06-01", auto_adjust=True, progress=False)
        except Exception:
            continue
        if h.empty:
            continue
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        c = h["Close"]
        c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
        f = pd.DataFrame({"close": c})
        f["ret20"] = c.pct_change(20)
        f["ret60"] = c.pct_change(60)
        f["ret120"] = c.pct_change(120)
        for n in (20, 50, 100, 200):
            f[f"vs_sma{n}"] = c / c.rolling(n).mean() - 1
        f["rv20"] = c.pct_change().rolling(20).std()
        f["ticker"] = t
        f = f.reset_index().rename(columns={"index": "date", "Date": "date"})
        feats.append(f)
    allf = pd.concat(feats, ignore_index=True)
    return df.merge(allf, on=["date", "ticker"], how="left")


def auc(pos: pd.Series, neg: pd.Series) -> float:
    """Rank-based AUC of feature separating pos from neg."""
    pos, neg = pos.dropna(), neg.dropna()
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    both = pd.concat([pos, neg]).rank()
    return float((both.iloc[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--no-price", action="store_true", help="band-geometry features only (no yfinance)")
    args = p.parse_args(argv)

    df = load_signals()
    print(f"Rows: {len(df)}  dates {df['date'].min().date()} → {df['date'].max().date()}  tickers {df['ticker'].nunique()}")
    print(f"Trend mix: {df['trend'].value_counts().to_dict()}")

    band_feats = ["skew", "width_rel", "mid_vs_prev"]
    price_feats = ["ret20", "ret60", "ret120", "vs_sma20", "vs_sma50", "vs_sma100", "vs_sma200", "rv20"]
    feats = band_feats[:]
    if not args.no_price:
        print("\nFetching price history for feature computation…")
        df = add_price_features(df)
        feats += price_feats

    bull = df[df["trend"] == "BULLISH"]
    bear = df[df["trend"] == "BEARISH"]
    print(f"\nBULLISH vs BEARISH separability (AUC, 0.5 = useless, ≥0.8 = strong):")
    rows = []
    for f in feats:
        if f not in df.columns:
            continue
        a = auc(bull[f], bear[f])
        rows.append([f, f"{a:.3f}", f"{bull[f].mean():+.4f}", f"{bear[f].mean():+.4f}"])
    rows.sort(key=lambda r: -abs(float(r[1]) - 0.5) if r[1] != 'nan' else 0)
    print(tabulate(rows, headers=["Feature", "AUC", "mean|BULL", "mean|BEAR"], tablefmt="simple"))

    # obvious hypotheses
    print("\nSingle-rule hypothesis accuracy (BULLISH vs BEARISH only):")
    hyp = []
    sub = df[df["trend"].isin(["BULLISH", "BEARISH"])].copy()
    y = (sub["trend"] == "BULLISH").to_numpy()
    for name, cond in [
        ("prev_close above band mid (skew > 0.5)", sub["skew"] > 0.5),
        ("positive 60d return", sub.get("ret60", pd.Series(np.nan, index=sub.index)) > 0),
        ("close above 100d SMA", sub.get("vs_sma100", pd.Series(np.nan, index=sub.index)) > 0),
        ("close above 200d SMA", sub.get("vs_sma200", pd.Series(np.nan, index=sub.index)) > 0),
        ("close above 50d SMA", sub.get("vs_sma50", pd.Series(np.nan, index=sub.index)) > 0),
    ]:
        mask = cond.notna() if hasattr(cond, "notna") else pd.Series(True, index=sub.index)
        valid = mask & sub["trend"].notna()
        if valid.sum() == 0:
            continue
        acc = float((cond[valid].astype(bool).to_numpy() == y[valid.to_numpy()]).mean())
        hyp.append([name, int(valid.sum()), f"{acc:.1%}"])
    print(tabulate(hyp, headers=["Rule: predict BULLISH when…", "N", "Accuracy"], tablefmt="simple"))

    # interpretable tree
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
        use = [f for f in feats if f in sub.columns]
        X = sub[use].to_numpy()
        ok = ~np.isnan(X).any(axis=1)
        if ok.sum() > 100:
            tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=25, random_state=0)
            tree.fit(X[ok], y[ok])
            acc = tree.score(X[ok], y[ok])
            print(f"\nDepth-3 decision tree (in-sample acc {acc:.1%}, N={int(ok.sum())}):")
            print(export_text(tree, feature_names=use, max_depth=3))
    except ImportError:
        print("\n(sklearn not installed — skipped tree)")

    # what changes on flip days
    sub2 = df.sort_values(["ticker", "date"]).copy()
    sub2["prev_trend"] = sub2.groupby("ticker")["trend"].shift(1)
    flips = sub2[(sub2["prev_trend"].notna()) & (sub2["trend"] != sub2["prev_trend"])]
    print(f"\nTrend flips observed: {len(flips)}")
    if len(flips):
        print(flips.groupby(["prev_trend", "trend"]).size().to_string())
        if "vs_sma50" in flips.columns:
            print("\nAt flip TO BULLISH: mean vs_sma50 = "
                  f"{flips[flips['trend'] == 'BULLISH']['vs_sma50'].mean():+.3f}; "
                  f"TO BEARISH: {flips[flips['trend'] == 'BEARISH']['vs_sma50'].mean():+.3f}")

    out = ROOT / "trend_deduction_features.csv"
    df.to_csv(out, index=False, float_format="%.6f")
    print(f"\nSaved feature table → {out.name}")


if __name__ == "__main__":
    main()
