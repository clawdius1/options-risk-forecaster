#!/usr/bin/env python3
"""
Extension analyses on top of the full firm + RV backtest (HANDOFF open items 2 & 3):

  A. Intraday pierce rates — score bands against next-day HIGH/LOW, not just close.
     A band "contains" the day only if next_low >= band_low AND next_high <= band_high.
     Also reports directional pierce rates (up = high > band_high, down = low < band_low).

  B. RV lookback grid — 10/20/30/60d realized-vol bands vs firm on the full sample.

Uses OHLCV only for firm/RV (N~1,350) and historical_iv.csv for the IV/blend
methods on the IV-covered subset (N~549). No live yfinance option chains.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

from run_iv_backtest import download_history, load_iv, load_signals, realized_vol

ROOT = Path(__file__).resolve().parent

LOOKBACKS = [10, 20, 30, 60]


def build_rows(signals: pd.DataFrame, iv: pd.DataFrame, hist: dict[str, pd.DataFrame]) -> pd.DataFrame:
    iv_cols = iv[["date", "ticker", "atm_iv"]] if "atm_iv" in iv.columns else iv
    merged = signals.merge(iv_cols, on=["date", "ticker"], how="left")

    rows = []
    for _, s in merged.iterrows():
        t, d = s["ticker"], s["date"]
        h = hist.get(t)
        if h is None or h.empty:
            continue
        row = {
            "date": d,
            "ticker": t,
            "prev_close": float(s["prev_close"]),
            "firm_low": float(s["firm_low"]),
            "firm_high": float(s["firm_high"]),
            "atm_iv": float(s["atm_iv"]) if pd.notna(s.get("atm_iv")) else np.nan,
        }
        available = h[h.index <= d]
        for lb in LOOKBACKS:
            row[f"sigma_{lb}"] = realized_vol(available["Close"], lookback=lb)
        future = h[h.index > d]
        if future.empty:
            row["actual_close"] = row["actual_high"] = row["actual_low"] = np.nan
        else:
            bar = future.iloc[0]
            row["actual_close"] = float(bar["Close"])
            row["actual_high"] = float(bar["High"])
            row["actual_low"] = float(bar["Low"])
        rows.append(row)
    df = pd.DataFrame(rows).dropna(subset=["actual_close"]).reset_index(drop=True)
    return df


def band_stats(df: pd.DataFrame, lo: pd.Series, hi: pd.Series) -> dict:
    valid = lo.notna() & hi.notna()
    n = int(valid.sum())
    if n == 0:
        return {"n": 0}
    lo, hi = lo[valid], hi[valid]
    close, high, low = df.loc[valid, "actual_close"], df.loc[valid, "actual_high"], df.loc[valid, "actual_low"]
    close_hit = (close >= lo) & (close <= hi)
    pierce_up = high > hi
    pierce_down = low < lo
    contained = ~pierce_up & ~pierce_down
    return {
        "n": n,
        "close_hit": close_hit.mean(),
        "contained": contained.mean(),
        "pierce_up": pierce_up.mean(),
        "pierce_down": pierce_down.mean(),
        "pierce_both": (pierce_up & pierce_down).mean(),
        "width": float((hi - lo).mean()),
    }


def method_bands(df: pd.DataFrame, blend_weight: float) -> dict[str, tuple[pd.Series, pd.Series]]:
    S = df["prev_close"]
    out = {"Research Firm": (df["firm_low"], df["firm_high"])}
    move_rv = S * df["sigma_20"]
    out["RV 1σ (20d)"] = (S - move_rv, S + move_rv)
    out["RV 2σ (20d)"] = (S - 2 * move_rv, S + 2 * move_rv)
    move_iv = S * df["atm_iv"] / np.sqrt(252)
    out["IV 2σ (hist)"] = (S - 2 * move_iv, S + 2 * move_iv)
    move_b = blend_weight * move_iv + (1 - blend_weight) * move_rv
    out[f"Blend 2σ w={blend_weight:.1f}"] = (S - 2 * move_b, S + 2 * move_b)
    return out


def report_pierce(df: pd.DataFrame, blend_weight: float) -> pd.DataFrame:
    bands = method_bands(df, blend_weight)
    records = []
    for name, (lo, hi) in bands.items():
        st = band_stats(df, lo, hi)
        if st["n"] == 0:
            continue
        records.append(
            {
                "method": name,
                "n": st["n"],
                "close_hit": st["close_hit"],
                "intraday_contained": st["contained"],
                "pierce_up": st["pierce_up"],
                "pierce_down": st["pierce_down"],
                "pierce_both": st["pierce_both"],
                "mean_width": st["width"],
            }
        )
    rep = pd.DataFrame(records)
    disp = rep.copy()
    for c in ("close_hit", "intraday_contained", "pierce_up", "pierce_down", "pierce_both"):
        disp[c] = disp[c].map(lambda v: f"{v:.1%}")
    disp["mean_width"] = disp["mean_width"].map(lambda v: f"${v:.2f}")
    print(
        tabulate(
            disp,
            headers=["Method", "N", "Close Hit", "Intraday Contained", "Pierce Up", "Pierce Down", "Both", "Mean Width"],
            tablefmt="simple",
            showindex=False,
        )
    )
    return rep


def report_lookback_grid(df: pd.DataFrame) -> pd.DataFrame:
    S = df["prev_close"]
    firm = band_stats(df, df["firm_low"], df["firm_high"])
    records = []
    for lb in LOOKBACKS:
        move = S * df[f"sigma_{lb}"]
        for k, label in ((1, "1σ"), (2, "2σ")):
            st = band_stats(df, S - k * move, S + k * move)
            records.append(
                {
                    "lookback": lb,
                    "band": label,
                    "n": st["n"],
                    "close_hit": st["close_hit"],
                    "intraday_contained": st["contained"],
                    "mean_width": st["width"],
                    "width_vs_firm": st["width"] / firm["width"],
                }
            )
    rep = pd.DataFrame(records)
    disp = rep.copy()
    disp["close_hit"] = disp["close_hit"].map(lambda v: f"{v:.1%}")
    disp["intraday_contained"] = disp["intraday_contained"].map(lambda v: f"{v:.1%}")
    disp["mean_width"] = disp["mean_width"].map(lambda v: f"${v:.2f}")
    disp["width_vs_firm"] = disp["width_vs_firm"].map(lambda v: f"{v:.2f}x")
    print(
        tabulate(
            disp,
            headers=["Lookback", "Band", "N", "Close Hit", "Intraday Contained", "Mean Width", "Width vs Firm"],
            tablefmt="simple",
            showindex=False,
        )
    )
    print(
        f"\n  Reference — firm: close hit {firm['close_hit']:.1%}, "
        f"intraday contained {firm['contained']:.1%}, mean width ${firm['width']:.2f}"
    )
    return rep


def run(signals_path: Path, iv_path: Path, blend_weight: float, save_prefix: Path) -> None:
    signals = load_signals(signals_path)
    iv = load_iv(iv_path)
    tickers = sorted(signals["ticker"].unique())
    hist = download_history(tickers, signals["date"].min(), signals["date"].max())
    df = build_rows(signals, iv, hist)
    print(f"\nRows with next-day actual: {len(df)} | with hist IV: {int(df['atm_iv'].notna().sum())}")

    print(f"\n{'=' * 90}")
    print("  A. INTRADAY PIERCE RATES — full sample (firm + RV; IV methods cover their subset)")
    print(f"{'=' * 90}\n")
    pierce_full = report_pierce(df, blend_weight)

    iv_sub = df[df["atm_iv"].notna()].reset_index(drop=True)
    print(f"\n{'-' * 90}")
    print(f"  A2. Same, restricted to IV-covered subset (N={len(iv_sub)}) for apples-to-apples")
    print(f"{'-' * 90}\n")
    pierce_iv = report_pierce(iv_sub, blend_weight)

    print(f"\n{'=' * 90}")
    print(f"  B. RV LOOKBACK GRID {LOOKBACKS} — full sample, close hit + intraday containment")
    print(f"{'=' * 90}\n")
    grid = report_lookback_grid(df)

    pierce_full.assign(sample="full").pipe(
        lambda a: pd.concat([a, pierce_iv.assign(sample="iv_covered")], ignore_index=True)
    ).to_csv(f"{save_prefix}_pierce.csv", index=False, float_format="%.6f")
    grid.to_csv(f"{save_prefix}_rv_lookback.csv", index=False, float_format="%.6f")
    print(f"\nSaved: {save_prefix}_pierce.csv, {save_prefix}_rv_lookback.csv")


def main(argv=None):
    p = argparse.ArgumentParser(description="Pierce-rate + RV-lookback extension analyses")
    p.add_argument("--signals", default=str(ROOT / "risk_range_signals.csv"))
    p.add_argument("--iv", default=str(ROOT / "historical_iv.csv"))
    p.add_argument("--blend-weight", type=float, default=0.5)
    p.add_argument("--save-prefix", default=str(ROOT / "backtest_results_extensions"))
    args = p.parse_args(argv)
    run(Path(args.signals), Path(args.iv), args.blend_weight, Path(args.save_prefix))


if __name__ == "__main__":
    main()
