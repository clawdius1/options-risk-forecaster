#!/usr/bin/env python3
"""
Offline grid search: finds optimal blend weight using already-fetched data.
Reads the detailed CSV output from backtest.py and searches across weights.

Usage:
    python grid_search_offline.py --csv backtest_results.csv
"""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to backtest CSV with per-row data.")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    print(f"Loaded {len(df)} rows from {args.csv}")

    # Filter to valid rows
    valid = df.dropna(subset=["actual_close", "our_iv_low", "our_rv_low"]).copy()
    print(f"Valid rows with both IV and RV: {len(valid)}")

    weights = [round(x * 0.05, 2) for x in range(0, 21)]  # 0.00 to 1.00 in 0.05 steps

    best_w = 0.5
    best_rate = 0.0
    results = []

    for w in weights:
        blend_low = w * valid["our_iv_low"] + (1 - w) * valid["our_rv_low"]
        blend_high = w * valid["our_iv_high"] + (1 - w) * valid["our_rv_high"]
        hits = ((valid["actual_close"] >= blend_low) & (valid["actual_close"] <= blend_high)).sum()
        rate = hits / len(valid)
        mean_width = (blend_high - blend_low).mean()
        results.append((w, hits, len(valid), rate, mean_width))
        if rate > best_rate:
            best_rate = rate
            best_w = w

    print(f"\n  {'Weight':>6s}  {'Hits':>5s}  {'N':>5s}  {'Hit Rate':>10s}  {'Mean Width':>12s}")
    print(f"  {'-'*45}")
    for w, h, n, rate, mw in results:
        marker = " ← BEST" if w == best_w else ""
        print(f"  {w:6.2f}  {h:5d}  {n:5d}  {rate:9.1%}  ${mw:10.2f}{marker}")

    print(f"\n  Optimal weight: {best_w:.2f}  (hit rate: {best_rate:.1%})")

    # Also show firm's hit rate for comparison
    firm_hits = ((valid["actual_close"] >= valid["firm_low"]) & (valid["actual_close"] <= valid["firm_high"])).sum()
    firm_rate = firm_hits / len(valid)
    firm_width = (valid["firm_high"] - valid["firm_low"]).mean()
    print(f"\n  Research firm:   {firm_hits}/{len(valid)} = {firm_rate:.1%}  (mean width: ${firm_width:.2f})")

    # Show IV-only and RV-only
    iv_hits = ((valid["actual_close"] >= valid["our_iv_low"]) & (valid["actual_close"] <= valid["our_iv_high"])).sum()
    rv_hits = ((valid["actual_close"] >= valid["our_rv_low"]) & (valid["actual_close"] <= valid["our_rv_high"])).sum()
    print(f"  IV-only:         {iv_hits}/{len(valid)} = {iv_hits/len(valid):.1%}")
    print(f"  RV-only:         {rv_hits}/{len(valid)} = {rv_hits/len(valid):.1%}")


if __name__ == "__main__":
    main()
