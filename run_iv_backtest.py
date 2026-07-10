#!/usr/bin/env python3
"""
Proper historical-IV backtest.

Joins:
  risk_range_signals.csv  (firm bands)
  historical_iv.csv       (AlphaQuery daily IV)
  yfinance OHLCV          (actual next-day + realized vol)

Only rows with both firm signal AND historical IV are scored for IV/blend.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tabulate import tabulate

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent


def load_signals(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(
        columns={
            "Date": "date",
            "Ticker": "ticker",
            "Low": "firm_low",
            "High": "firm_high",
            "Prev. Close": "prev_close",
        }
    )
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for c in ("firm_low", "firm_high", "prev_close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["date", "ticker", "firm_low", "firm_high", "prev_close"])


def load_iv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df


def download_history(tickers, start, end) -> dict[str, pd.DataFrame]:
    start_s = (start - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    end_s = (end + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    print(f"Downloading OHLCV {start_s} → {end_s}")
    raw = yf.download(
        tickers=list(tickers),
        start=start_s,
        end=end_s,
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    out = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        h = raw[t].copy() if multi else raw.copy()
        h = h.dropna(how="all")
        if h.empty:
            continue
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_localize(None)
        h.index = pd.to_datetime(h.index).normalize()
        out[t] = h
        print(f"  {t}: {len(h)} bars")
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


def next_close(hist: pd.DataFrame, signal_date: pd.Timestamp):
    future = hist[hist.index > signal_date.normalize()]
    if future.empty:
        return None, np.nan, np.nan, np.nan
    bar = future.iloc[0]
    return future.index[0], float(bar["Close"]), float(bar["High"]), float(bar["Low"])


def hit(lo, hi, x) -> bool:
    if any(pd.isna(v) for v in (lo, hi, x)):
        return False
    return float(lo) <= float(x) <= float(hi)


def rate(mask) -> str:
    n = int(mask.notna().sum()) if hasattr(mask, "notna") else len(mask)
    # handle boolean series
    s = mask.dropna() if hasattr(mask, "dropna") else mask
    if len(s) == 0:
        return "N/A"
    return f"{s.mean():.1%} ({int(s.sum())}/{len(s)})"


def print_report(df: pd.DataFrame, weight: float) -> None:
    valid = df.dropna(subset=["actual_close"]).copy()
    iv_ok = valid.dropna(subset=["atm_iv"]).copy()
    print(f"\n{'=' * 80}")
    print(f"  HISTORICAL-IV BACKTEST  |  weight(IV)={weight:.2f}")
    print(f"  rows total={len(df)}  with actual={len(valid)}  with hist IV={len(iv_ok)}")
    if len(iv_ok):
        print(f"  IV window: {iv_ok['date'].min().date()} → {iv_ok['date'].max().date()}")
    print(f"{'=' * 80}")

    methods = [
        ("Research Firm", "firm_low", "firm_high", "firm_hit"),
        ("IV 1σ (hist)", "iv_1s_low", "iv_1s_high", "iv_1s_hit"),
        ("IV 2σ (hist)", "iv_2s_low", "iv_2s_high", "iv_2s_hit"),
        ("RV 1σ", "rv_1s_low", "rv_1s_high", "rv_1s_hit"),
        ("RV 2σ", "rv_2s_low", "rv_2s_high", "rv_2s_hit"),
        (f"Blend 1σ w={weight:.2f}", "blend_1s_low", "blend_1s_high", "blend_1s_hit"),
        (f"Blend 2σ w={weight:.2f}", "blend_2s_low", "blend_2s_high", "blend_2s_hit"),
    ]

    rows = []
    for name, lo, hi, hcol in methods:
        sub = iv_ok.dropna(subset=[lo, hi]) if "IV" in name or "Blend" in name else valid.dropna(subset=[lo, hi])
        # For fair comparison on the IV-covered subset:
        sub = iv_ok.dropna(subset=[lo, hi, "actual_close"])
        if sub.empty:
            rows.append([name, 0, 0, "N/A", "N/A"])
            continue
        hits = int(sub[hcol].sum())
        n = len(sub)
        width = float((sub[hi] - sub[lo]).mean())
        rows.append([name, hits, n, f"{hits/n:.1%}", f"${width:.2f}"])
    print("\n  Hit rates (on rows with historical IV + next-day actual)")
    print(tabulate(rows, headers=["Method", "Hits", "N", "Hit Rate", "Mean Width"], tablefmt="simple"))

    # Same-width comparison vs firm
    print("\n  Material ratios (IV-covered subset)")
    sub = iv_ok.dropna(subset=["firm_low", "iv_2s_low", "rv_2s_low", "actual_close"])
    if not sub.empty:
        fw = (sub["firm_high"] - sub["firm_low"]).mean()
        iw = (sub["iv_2s_high"] - sub["iv_2s_low"]).mean()
        rw = (sub["rv_2s_high"] - sub["rv_2s_low"]).mean()
        print(f"  Firm mean width: ${fw:.2f}")
        print(f"  IV 2σ mean width: ${iw:.2f}   firm/IV2 = {fw/iw:.2f}x" if iw else "  IV 2σ N/A")
        print(f"  RV 2σ mean width: ${rw:.2f}   firm/RV2 = {fw/rw:.2f}x" if rw else "  RV 2σ N/A")
        print(f"  Firm hit:  {sub['firm_hit'].mean():.1%}")
        print(f"  IV 2σ hit: {sub['iv_2s_hit'].mean():.1%}")
        print(f"  RV 2σ hit: {sub['rv_2s_hit'].mean():.1%}")

    # By ticker
    print("\n  By ticker (IV-covered)")
    trows = []
    for t, g in iv_ok.groupby("ticker"):
        g = g.dropna(subset=["actual_close"])
        if g.empty:
            continue
        trows.append(
            [
                t,
                len(g),
                f"{g['firm_hit'].mean():.0%}",
                f"{g['iv_2s_hit'].mean():.0%}" if g["iv_2s_hit"].notna().any() else "N/A",
                f"{g['rv_2s_hit'].mean():.0%}" if g["rv_2s_hit"].notna().any() else "N/A",
                f"{g['blend_2s_hit'].mean():.0%}" if g["blend_2s_hit"].notna().any() else "N/A",
                f"{g['atm_iv'].mean():.1%}",
            ]
        )
    print(tabulate(trows, headers=["Ticker", "N", "Firm", "IV2σ", "RV2σ", "Blend2σ", "Avg IV"], tablefmt="simple"))

    # By month
    print("\n  By month (IV-covered)")
    iv_ok = iv_ok.copy()
    iv_ok["ym"] = iv_ok["date"].dt.to_period("M").astype(str)
    mrows = []
    for ym, g in iv_ok.groupby("ym"):
        g = g.dropna(subset=["actual_close"])
        mrows.append(
            [
                ym,
                len(g),
                f"{g['firm_hit'].mean():.1%}",
                f"{g['iv_2s_hit'].mean():.1%}",
                f"{g['rv_2s_hit'].mean():.1%}",
                f"{g['blend_2s_hit'].mean():.1%}",
            ]
        )
    print(tabulate(mrows, headers=["Month", "N", "Firm", "IV2σ", "RV2σ", "Blend2σ"], tablefmt="simple"))

    # Weight grid on opcompressible subset (no refetch)
    print("\n  Offline blend-weight grid (2σ hit rate on IV-covered rows)")
    grid = []
    base = iv_ok.dropna(subset=["atm_iv", "sigma_daily", "actual_close", "prev_close"]).copy()
    S = base["prev_close"]
    move_iv = S * base["atm_iv"] / np.sqrt(252)
    move_rv = S * base["sigma_daily"]
    for w in [round(x * 0.1, 1) for x in range(0, 11)]:
        move_b = w * move_iv + (1 - w) * move_rv
        lo = S - 2 * move_b
        hi = S + 2 * move_b
        hits = ((base["actual_close"] >= lo) & (base["actual_close"] <= hi)).sum()
        n = len(base)
        width = float((hi - lo).mean())
        grid.append([w, int(hits), n, f"{hits/n:.1%}", f"${width:.2f}"])
    print(tabulate(grid, headers=["w(IV)", "Hits", "N", "Hit Rate", "Mean Width"], tablefmt="simple"))

    # Univariate predictability: |ret| ~ atm_iv / sigma / spread
    print("\n  Predictability: next-day |return| correlations (IV-covered)")
    base = iv_ok.dropna(subset=["atm_iv", "sigma_daily", "actual_close", "prev_close"]).copy()
    base["abs_ret"] = (base["actual_close"] / base["prev_close"] - 1.0).abs()
    base["rv_ann"] = base["sigma_daily"] * np.sqrt(252)
    base["iv_rv_spread"] = base["atm_iv"] - base["rv_ann"]
    for col in ["atm_iv", "rv_ann", "iv_rv_spread"]:
        if base[col].std() == 0 or base["abs_ret"].std() == 0:
            corr = np.nan
        else:
            corr = float(base[[col, "abs_ret"]].corr().iloc[0, 1])
        # R^2 of univariate OLS
        x = base[col].values
        y = base["abs_ret"].values
        if len(x) > 2 and np.std(x) > 0:
            b = np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1)
            a = y.mean() - b * x.mean()
            yhat = a + b * x
            ss_res = ((y - yhat) ** 2).sum()
            ss_tot = ((y - y.mean()) ** 2).sum()
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
        else:
            r2 = np.nan
        print(f"  {col:14s}  corr={corr:+.3f}  R²={r2:.3f}")


def run(signals_path: Path, iv_path: Path, weight: float, lookback: int, save_csv: Path) -> pd.DataFrame:
    signals = load_signals(signals_path)
    iv = load_iv(iv_path)
    print(f"Signals: {len(signals)} | Historical IV rows: {len(iv)}")

    merged = signals.merge(iv, on=["date", "ticker"], how="left")
    print(f"After merge: {len(merged)} rows; with atm_iv: {merged['atm_iv'].notna().sum()}")

    tickers = sorted(merged["ticker"].unique())
    hist = download_history(tickers, merged["date"].min(), merged["date"].max())

    rows = []
    for _, s in merged.iterrows():
        t = s["ticker"]
        d = pd.Timestamp(s["date"]).normalize()
        S = float(s["prev_close"])
        row = {
            "date": d,
            "ticker": t,
            "firm_low": float(s["firm_low"]),
            "firm_high": float(s["firm_high"]),
            "prev_close": S,
            "atm_iv": float(s["atm_iv"]) if pd.notna(s.get("atm_iv")) else np.nan,
            "call_iv": float(s["call_iv"]) if pd.notna(s.get("call_iv")) else np.nan,
            "put_iv": float(s["put_iv"]) if pd.notna(s.get("put_iv")) else np.nan,
            "iv_skew": float(s["iv_skew"]) if pd.notna(s.get("iv_skew")) else np.nan,
        }
        h = hist.get(t)
        if h is None or h.empty:
            rows.append(row)
            continue
        available = h[h.index <= d]
        sigma = realized_vol(available["Close"], lookback=lookback) if not available.empty else np.nan
        row["sigma_daily"] = sigma
        nd, ac, ah, al = next_close(h, d)
        row["next_date"] = nd.strftime("%Y-%m-%d") if nd is not None else ""
        row["actual_close"] = ac
        row["actual_high"] = ah
        row["actual_low"] = al

        # RV bands
        if not np.isnan(sigma):
            m = S * sigma
            row["rv_1s_low"], row["rv_1s_high"] = S - m, S + m
            row["rv_2s_low"], row["rv_2s_high"] = S - 2 * m, S + 2 * m
        else:
            row["rv_1s_low"] = row["rv_1s_high"] = row["rv_2s_low"] = row["rv_2s_high"] = np.nan

        # IV bands from historical series
        atm = row["atm_iv"]
        if not np.isnan(atm):
            m = S * atm / np.sqrt(252)
            row["iv_1s_low"], row["iv_1s_high"] = S - m, S + m
            row["iv_2s_low"], row["iv_2s_high"] = S - 2 * m, S + 2 * m
        else:
            row["iv_1s_low"] = row["iv_1s_high"] = row["iv_2s_low"] = row["iv_2s_high"] = np.nan

        # Blend
        has_iv = not np.isnan(row["iv_1s_low"]) if not isinstance(row["iv_1s_low"], float) else not np.isnan(row.get("iv_1s_low", np.nan))
        has_iv = pd.notna(row.get("iv_1s_low"))
        has_rv = pd.notna(row.get("rv_1s_low"))
        if has_iv and has_rv:
            m_iv = (row["iv_1s_high"] - row["iv_1s_low"]) / 2
            m_rv = (row["rv_1s_high"] - row["rv_1s_low"]) / 2
            m_b = weight * m_iv + (1 - weight) * m_rv
            row["blend_1s_low"], row["blend_1s_high"] = S - m_b, S + m_b
            row["blend_2s_low"], row["blend_2s_high"] = S - 2 * m_b, S + 2 * m_b
        elif has_iv:
            row["blend_1s_low"], row["blend_1s_high"] = row["iv_1s_low"], row["iv_1s_high"]
            row["blend_2s_low"], row["blend_2s_high"] = row["iv_2s_low"], row["iv_2s_high"]
        elif has_rv:
            row["blend_1s_low"], row["blend_1s_high"] = row["rv_1s_low"], row["rv_1s_high"]
            row["blend_2s_low"], row["blend_2s_high"] = row["rv_2s_low"], row["rv_2s_high"]
        else:
            row["blend_1s_low"] = row["blend_1s_high"] = row["blend_2s_low"] = row["blend_2s_high"] = np.nan

        ac = row.get("actual_close", np.nan)
        row["firm_hit"] = hit(row["firm_low"], row["firm_high"], ac)
        row["iv_1s_hit"] = hit(row.get("iv_1s_low"), row.get("iv_1s_high"), ac)
        row["iv_2s_hit"] = hit(row.get("iv_2s_low"), row.get("iv_2s_high"), ac)
        row["rv_1s_hit"] = hit(row.get("rv_1s_low"), row.get("rv_1s_high"), ac)
        row["rv_2s_hit"] = hit(row.get("rv_2s_low"), row.get("rv_2s_high"), ac)
        row["blend_1s_hit"] = hit(row.get("blend_1s_low"), row.get("blend_1s_high"), ac)
        row["blend_2s_hit"] = hit(row.get("blend_2s_low"), row.get("blend_2s_high"), ac)
        rows.append(row)

    out = pd.DataFrame(rows)
    out.to_csv(save_csv, index=False, float_format="%.6f")
    print(f"Saved {len(out)} rows → {save_csv}")
    print_report(out, weight)
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--signals", default=str(ROOT / "risk_range_signals.csv"))
    p.add_argument("--iv", default=str(ROOT / "historical_iv.csv"))
    p.add_argument("--weight", type=float, default=0.7)
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--save-csv", default=str(ROOT / "backtest_results_hist_iv.csv"))
    args = p.parse_args(argv)
    run(Path(args.signals), Path(args.iv), args.weight, args.lookback, Path(args.save_csv))


if __name__ == "__main__":
    main()
