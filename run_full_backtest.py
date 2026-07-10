#!/usr/bin/env python3
"""
Efficient full-period backtest for risk_range_signals.csv.

- Batch-downloads OHLCV for all tickers once (firm hit-rate + realized-vol model are clean)
- Options ATM IV from yfinance is LIVE only — contaminates historical rows.
  We still compute a "live IV scar" and mark rows where signal date is older than
  a recency cutoff with iv_status='contaminated'.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.interpolate import interp1d
from tabulate import tabulate

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
DEFAULT_SIGNALS = ROOT / "risk_range_signals.csv"


def load_signals(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    rename = {
        "Date": "date",
        "Ticker": "ticker",
        "Low": "firm_low",
        "High": "firm_high",
        "Prev. Close": "prev_close",
    }
    df = df.rename(columns=rename)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("firm_low", "firm_high", "prev_close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "ticker", "firm_low", "firm_high", "prev_close"]).copy()
    df = df.sort_values(["date", "ticker"]).reset_index(drop=True)
    return df


def download_history(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    start_s = (start - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    end_s = (end + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    print(f"Downloading OHLCV for {len(tickers)} tickers: {start_s} → {end_s}")
    raw = yf.download(
        tickers=tickers,
        start=start_s,
        end=end_s,
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        if multi:
            if t not in raw.columns.get_level_values(0):
                print(f"  WARN: no history for {t}")
                continue
            h = raw[t].copy()
        else:
            h = raw.copy()
        h = h.dropna(how="all")
        if h.empty:
            print(f"  WARN: empty history for {t}")
            continue
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_localize(None)
        h.index = pd.to_datetime(h.index).normalize()
        out[t] = h
        print(f"  {t}: {len(h)} bars  {h.index.min().date()} → {h.index.max().date()}")
    return out


def realized_vol(closes: pd.Series, lookback: int = 20) -> float:
    closes = closes.dropna()
    if len(closes) < 3:
        return np.nan
    rets = closes.pct_change().dropna()
    if rets.empty:
        return np.nan
    if len(rets) > lookback:
        rets = rets.tail(lookback)
    return float(rets.std())


def next_trading_row(hist: pd.DataFrame, signal_date: pd.Timestamp):
    future = hist[hist.index > signal_date.normalize()]
    if future.empty:
        return None
    return future.iloc[0], future.index[0]


def atm_iv_live(ticker: str, spot: float) -> tuple[float, float, float, float, float]:
    """Return (atm_iv, opt_vol, put_vol, call_vol, iv_skew). LIVE chain only."""
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return np.nan, 0.0, 0.0, 0.0, np.nan
        chain = t.option_chain(exps[0])
        calls = chain.calls.copy()
        puts = chain.puts.copy()
        call_vol = float(calls["volume"].fillna(0).sum()) if "volume" in calls else 0.0
        put_vol = float(puts["volume"].fillna(0).sum()) if "volume" in puts else 0.0
        if "impliedVolatility" not in calls.columns or "impliedVolatility" not in puts.columns:
            return np.nan, call_vol + put_vol, put_vol, call_vol, np.nan
        calls_iv = calls[["strike", "impliedVolatility"]].dropna().sort_values("strike")
        puts_iv = puts[["strike", "impliedVolatility"]].dropna().sort_values("strike")
        if calls_iv.empty and puts_iv.empty:
            return np.nan, call_vol + put_vol, put_vol, call_vol, np.nan
        merged = pd.merge(calls_iv, puts_iv, on="strike", suffixes=("_call", "_put"), how="outer").sort_values("strike")
        merged["avg_iv"] = merged[["impliedVolatility_call", "impliedVolatility_put"]].mean(axis=1, skipna=True)
        valid = merged.dropna(subset=["avg_iv"])
        if valid.empty:
            return np.nan, call_vol + put_vol, put_vol, call_vol, np.nan
        strikes = valid["strike"].values
        avg_ivs = valid["avg_iv"].values
        if len(strikes) >= 2:
            f = interp1d(strikes, avg_ivs, kind="linear", fill_value="extrapolate")
            atm_iv = float(np.clip(f(spot), avg_ivs.min(), avg_ivs.max()))
        else:
            atm_iv = float(avg_ivs[0])
        iv_skew = np.nan
        if not calls_iv.empty and not puts_iv.empty:
            ci = (calls_iv["strike"] - spot).abs().idxmin()
            pi = (puts_iv["strike"] - spot).abs().idxmin()
            iv_skew = float(puts_iv.loc[pi, "impliedVolatility"] - calls_iv.loc[ci, "impliedVolatility"])
        return atm_iv, call_vol + put_vol, put_vol, call_vol, iv_skew
    except Exception as e:
        print(f"  IV fetch failed {ticker}: {e}")
        return np.nan, 0.0, 0.0, 0.0, np.nan


def hit(lo, hi, x) -> bool:
    if any(pd.isna(v) for v in (lo, hi, x)):
        return False
    return lo <= x <= hi


def summarize(df: pd.DataFrame, label: str) -> None:
    valid = df.dropna(subset=["actual_close"]).copy()
    n = len(valid)
    print(f"\n{'=' * 80}")
    print(f"  {label}  |  N={n} with actual close  |  total rows={len(df)}")
    print(f"{'=' * 80}")
    if n == 0:
        print("  No valid rows.")
        return

    methods = [
        ("Research Firm", "firm_low", "firm_high", "firm_hit"),
        ("RV 1σ", "rv_1s_low", "rv_1s_high", "rv_1s_hit"),
        ("RV 2σ", "rv_2s_low", "rv_2s_high", "rv_2s_hit"),
        ("IV 1σ (live)", "iv_1s_low", "iv_1s_high", "iv_1s_hit"),
        ("IV 2σ (live)", "iv_2s_low", "iv_2s_high", "iv_2s_hit"),
        ("Blend 1σ", "blend_1s_low", "blend_1s_high", "blend_1s_hit"),
        ("Blend 2σ", "blend_2s_low", "blend_2s_high", "blend_2s_hit"),
    ]
    rows = []
    for name, lo, hi, hit_col in methods:
        sub = valid.dropna(subset=[lo, hi])
        if sub.empty:
            rows.append([name, 0, 0, "N/A", "N/A"])
            continue
        hits = int(sub[hit_col].sum()) if hit_col in sub else int(((sub["actual_close"] >= sub[lo]) & (sub["actual_close"] <= sub[hi])).sum())
        nn = len(sub)
        width = float((sub[hi] - sub[lo]).mean())
        rows.append([name, hits, nn, f"{hits/nn:.1%}", f"${width:.2f}"])
    print(tabulate(rows, headers=["Method", "Hits", "N", "Hit Rate", "Mean Width"], tablefmt="simple"))

    # Firm only — clean comparison
    print(f"\n  Firm hit rate by ticker")
    print(f"  {'-' * 60}")
    trows = []
    for t, g in valid.groupby("ticker"):
        fh = int(g["firm_hit"].sum())
        nn = len(g)
        width = float((g["firm_high"] - g["firm_low"]).mean())
        trows.append([t, nn, f"{fh/nn:.1%}", f"${width:.2f}"])
    print(tabulate(trows, headers=["Ticker", "N", "Firm Hit", "Mean Firm Width"], tablefmt="simple"))

    print(f"\n  Firm hit rate by month")
    print(f"  {'-' * 60}")
    valid["ym"] = valid["date"].dt.to_period("M").astype(str)
    mrows = []
    for ym, g in valid.groupby("ym"):
        fh = int(g["firm_hit"].sum())
        nn = len(g)
        mrows.append([ym, nn, f"{fh/nn:.1%}"])
    print(tabulate(mrows, headers=["Month", "N", "Firm Hit"], tablefmt="simple"))

    # Decide  confidence on sigma calibration
    firm_hit = valid["firm_hit"].mean()
    firm_width = (valid["firm_high"] - valid["firm_low"]).mean()
    rv1_width = (valid["rv_1s_high"] - valid["rv_1s_low"]).mean()
    rv2_width = (valid["rv_2s_high"] - valid["rv_2s_low"]).mean()
    print(f"\n  Calibration notes")
    print(f"  {'-' * 60}")
    print(f"  Firm overall hit rate: {firm_hit:.1%}")
    print(f"  Firm mean width:      ${firm_width:.2f}")
    print(f"  RV 1σ mean width:     ${rv1_width:.2f}")
    print(f"  RV 2σ mean width:     ${rv2_width:.2f}")
    print(f"  Width ratio firm/RV1: {firm_width/rv1_width:.2f}x" if rv1_width else "  Width ratio N/A")
    print(f"  Width ratio firm/RV2: {firm_width/rv2_width:.2f}x" if rv2_width else "  Width ratio N/A")
    if "iv_status" in valid.columns:
        print(f"  IV status counts:")
        print(valid["iv_status"].value_counts().to_string())


def run(signals_path: Path, weight: float, lookback: int, iv_recency_days: int, save_csv: Path) -> pd.DataFrame:
    signals = load_signals(signals_path)
    print(f"Loaded {len(signals)} signals from {signals_path}")
    print(f"Date range: {signals['date'].min().date()} → {signals['date'].max().date()}")
    print(f"Tickers: {sorted(signals['ticker'].unique())}")

    tickers = sorted(signals["ticker"].unique())
    hist = download_history(tickers, signals["date"].min(), signals["date"].max())

    # Live ATM IV scar per ticker (applied only within recency window)
    print("\nFetching LIVE ATM IV scars (historical application is contaminated):")
    live_iv: dict[str, dict] = {}
    for t in tickers:
        h = hist.get(t)
        spot = float(h["Close"].iloc[-1]) if h is not None and not h.empty else float("nan")
        atm, ovol, pvol, cvol, skew = atm_iv_live(t, spot if not np.isnan(spot) else 100.0)
        live_iv[t] = {"atm_iv": atm, "opt_vol": ovol, "put_vol": pvol, "call_vol": cvol, "iv_skew": skew, "spot_now": spot}
        print(f"  {t}: ATM IV={atm:.1%}" if not np.isnan(atm) else f"  {t}: ATM IV=N/A")

    asof = pd.Timestamp(datetime.now().date())
    recency_cut = asof - pd.Timedelta(days=iv_recency_days)

    rows = []
    for i, s in signals.iterrows():
        t = s["ticker"]
        d = pd.Timestamp(s["date"]).normalize()
        h = hist.get(t)
        row = {
            "date": d,
            "ticker": t,
            "firm_low": float(s["firm_low"]),
            "firm_high": float(s["firm_high"]),
            "prev_close": float(s["prev_close"]),
        }
        if h is None or h.empty:
            row.update({"actual_close": np.nan, "sigma_daily": np.nan, "next_date": ""})
            rows.append(row)
            continue

        # realized vol using closes up to and including signal date
        available = h[h.index <= d]
        sigma = realized_vol(available["Close"], lookback=lookback) if not available.empty else np.nan
        row["sigma_daily"] = sigma

        # next-day actual
        nxt = next_trading_row(h, d)
        if nxt is None:
            row.update({"actual_close": np.nan, "actual_high": np.nan, "actual_low": np.nan, "next_date": ""})
        else:
            bar, nd = nxt
            row["next_date"] = nd.strftime("%Y-%m-%d")
            row["actual_close"] = float(bar["Close"])
            row["actual_high"] = float(bar["High"])
            row["actual_low"] = float(bar["Low"])

        # RV bands
        S = float(s["prev_close"])
        if not np.isnan(sigma):
            move_rv = S * sigma
            row["rv_1s_low"] = S - move_rv
            row["rv_1s_high"] = S + move_rv
            row["rv_2s_low"] = S - 2 * move_rv
            row["rv_2s_high"] = S + 2 * move_rv
        else:
            row["rv_1s_low"] = row["rv_1s_high"] = row["rv_2s_low"] = row["rv_2s_high"] = np.nan

        # IV bands from LIVE scar
        iv_info = live_iv.get(t, {})
        atm = iv_info.get("atm_iv", np.nan)
        # Use signal-date prev_close for band center (as firm does)
        if d >= recency_cut and not np.isnan(atm):
            row["atm_iv"] = atm
            row["iv_status"] = "recent_live"
        else:
            # Still record live scar but flag
            row["atm_iv"] = atm
            row["iv_status"] = "contaminated_live" if not np.isnan(atm) else "missing"
        if not np.isnan(atm):
            move_iv = S * atm / np.sqrt(252)
            row["iv_1s_low"] = S - move_iv
            row["iv_1s_high"] = S + move_iv
            row["iv_2s_low"] = S - 2 * move_iv
            row["iv_2s_high"] = S + 2 * move_rv if False else S - 2 * move_iv  # noqa: intentional use of move_iv
            row["iv_2s_low"] = S - 2 * move_iv
            row["iv_2s_high"] = S + 2 * move_iv
        else:
            row["iv_1s_low"] = row["iv_1s_high"] = row["iv_2s_low"] = row["iv_2s_high"] = np.nan

        # Blend on 1σ / 2σ
        has_iv = not np.isnan(row.get("iv_1s_low", np.nan))
        has_rv = not np.isnan(row.get("rv_1s_low", np.nan))
        if has_iv and has_rv:
            move_iv = (row["iv_1s_high"] - row["iv_1s_low"]) / 2
            move_rv = (row["rv_1s_high"] - row["rv_1s_low"]) / 2
            move_b = weight * move_iv + (1 - weight) * move_rv
            row["blend_1s_low"] = S - move_b
            row["blend_1s_high"] = S + move_b
            row["blend_2s_low"] = S - 2 * move_b
            row["blend_2s_high"] = S + 2 * move_b
        elif has_rv:
            row["blend_1s_low"] = row["rv_1s_low"]
            row["blend_1s_high"] = row["rv_1s_high"]
            row["blend_2s_low"] = row["rv_2s_low"]
            row["blend_2s_high"] = row["rv_2s_high"]
        elif has_iv:
            row["blend_1s_low"] = row["iv_1s_low"]
            row["blend_1s_high"] = row["iv_1s_high"]
            row["blend_2s_low"] = row["iv_2s_low"]
            row["blend_2s_high"] = row["iv_2s_high"]
        else:
            row["blend_1s_low"] = row["blend_1s_high"] = row["blend_2s_low"] = row["blend_2s_high"] = np.nan

        # hits
        ac = row.get("actual_close", np.nan)
        row["firm_hit"] = hit(row["firm_low"], row["firm_high"], ac)
        row["rv_1s_hit"] = hit(row.get("rv_1s_low"), row.get("rv_1s_high"), ac)
        row["rv_2s_hit"] = hit(row.get("rv_2s_low"), row.get("rv_2s_high"), ac)
        row["iv_1s_hit"] = hit(row.get("iv_1s_low"), row.get("iv_1s_high"), ac)
        row["iv_2s_hit"] = hit(row.get("iv_2s_low"), row.get("iv_2s_high"), ac)
        row["blend_1s_hit"] = hit(row.get("blend_1s_low"), row.get("blend_1s_high"), ac)
        row["blend_2s_hit"] = hit(row.get("blend_2s_low"), row.get("blend_2s_high"), ac)
        rows.append(row)

        if (len(rows) % 200) == 0:
            print(f"  processed {len(rows)}/{len(signals)}…")

    out = pd.DataFrame(rows)
    out.to_csv(save_csv, index=False, float_format="%.6f")
    print(f"\nSaved: {save_csv}  ({len(out)} rows)")

    summarize(out, f"FULL BACKTEST  weight={weight:.2f}  lookback={lookback}d")

    # Clean subset: firm + RV only (the trustworthy part)
    print("\nNOTE (confidence: high): firm hit-rate and RV bands use historical OHLCV only.")
    print("NOTE (confidence: high): yfinance ATM IV is LIVE — most historical IV/blend rows are contaminated.")
    print(f"NOTE: only rows with date >= {recency_cut.date()} marked iv_status=recent_live.")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Full efficient backtest for risk_range_signals.csv")
    p.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    p.add_argument("--weight", type=float, default=0.7)
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--iv-recency-days", type=int, default=5,
                   help="Only mark IV as recent_live within this many calendar days of today")
    p.add_argument("--save-csv", default=str(ROOT / "backtest_results_full.csv"))
    args = p.parse_args(argv)
    run(Path(args.signals), args.weight, args.lookback, args.iv_recency_days, Path(args.save_csv))


if __name__ == "__main__":
    main()
