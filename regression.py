#!/usr/bin/env python3
"""
Regression analysis: next-day |return| on IV-RV spread and O/S ratio.
Uses the backtest results CSV as input.

Usage:
    python regression.py --csv backtest_results.csv
"""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd
from pathlib import Path

try:
    import statsmodels.api as sm
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to backtest CSV.")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    print(f"Loaded {len(df)} rows from {args.csv}\n")

    # Filter to valid rows
    valid = df.dropna(subset=["actual_close", "prev_close", "atm_iv", "sigma_daily"]).copy()
    valid = valid[valid["prev_close"] > 0]

    # Compute next-day |return|
    valid["next_day_return"] = (valid["actual_close"] - valid["prev_close"]) / valid["prev_close"]
    valid["abs_return"] = valid["next_day_return"].abs()

    # Compute IV-RV spread (annualized RV)
    valid["rv_annualized"] = valid["sigma_daily"] * np.sqrt(252)
    valid["iv_rv_spread"] = valid["atm_iv"] - valid["rv_annualized"]

    # O/S ratio (we didn't save it in backtest CSV, but we have option_volume_total
    # and stock_volume from the signals. Let me check what columns we have.)
    print("Available columns:", list(valid.columns))
    print()

    # Summary stats
    print("=" * 60)
    print("  DESCRIPTIVE STATISTICS")
    print("=" * 60)
    print(f"\n  N = {len(valid)} observations")
    print(f"\n  Next-day |return|:")
    print(f"    Mean:   {valid['abs_return'].mean():.4f} ({valid['abs_return'].mean()*100:.2f}%)")
    print(f"    Median: {valid['abs_return'].median():.4f} ({valid['abs_return'].median()*100:.2f}%)")
    print(f"    Std:    {valid['abs_return'].std():.4f}")
    print(f"    Min:    {valid['abs_return'].min():.4f}")
    print(f"    Max:    {valid['abs_return'].max():.4f}")

    print(f"\n  IV-RV Spread:")
    spread = valid["iv_rv_spread"].dropna()
    print(f"    Mean:   {spread.mean():.4f} ({spread.mean()*100:.2f}%)")
    print(f"    Median: {spread.median():.4f}")
    print(f"    Std:    {spread.std():.4f}")

    # Correlation matrix
    print(f"\n  Correlation Matrix:")
    cols = ["abs_return", "iv_rv_spread", "atm_iv", "sigma_daily"]
    corr = valid[cols].corr()
    print(corr.to_string(float_format=lambda x: f"{x:7.3f}"))

    # --- OLS Regression ---
    print(f"\n{'='*60}")
    print("  OLS REGRESSION: |next_day_return| ~ IV-RV spread + ATM_IV + RV")
    print("=" * 60)

    reg_data = valid[["abs_return", "iv_rv_spread", "atm_iv", "rv_annualized"]].dropna()
    print(f"\n  N (complete cases): {len(reg_data)}")

    if len(reg_data) < 10:
        print("  Insufficient data for regression.")
        return

    y = reg_data["abs_return"]
    X = reg_data[["iv_rv_spread", "atm_iv", "rv_annualized"]]
    X = sm.add_constant(X) if HAS_STATSMODELS else X.assign(const=1.0)

    if HAS_STATSMODELS:
        model = sm.OLS(y, X).fit()
        print(model.summary().tables[1].as_text())
        print(f"\n  R²: {model.rsquared:.4f}")
        print(f"  Adj R²: {model.rsquared_adj:.4f}")
        print(f"  F-statistic: {model.fvalue:.2f} (p={model.f_pvalue:.4f})")

        print(f"\n  Coefficient interpretation:")
        for name, coef, pval in zip(model.params.index, model.params.values, model.pvalues.values):
            sig = "***" if pval < 0.01 else "**" if pval < 0.05 else "*" if pval < 0.1 else ""
            print(f"    {name:20s}: {coef:+.6f}  (p={pval:.4f}) {sig}")
    else:
        # Manual OLS using numpy
        print("  (statsmodels not available, using numpy)")
        X_mat = X.values
        y_mat = y.values
        beta = np.linalg.lstsq(X_mat, y_mat, rcond=None)[0]
        y_hat = X_mat @ beta
        residuals = y_mat - y_hat
        ss_res = np.sum(residuals**2)
        ss_tot = np.sum((y_mat - y_mat.mean())**2)
        r2 = 1 - ss_res / ss_tot
        n = len(y_mat)
        k = X_mat.shape[1]
        adj_r2 = 1 - (1 - r2) * (n - 1) / (n - k - 1)

        print(f"\n  R²: {r2:.4f}")
        print(f"  Adj R²: {adj_r2:.4f}")
        for name, b in zip(X.columns, beta):
            print(f"    {name:20s}: {b:+.6f}")

    # --- Simple univariate regressions ---
    print(f"\n{'='*60}")
    print("  UNIVARIATE REGRESSIONS")
    print("=" * 60)

    for col in ["iv_rv_spread", "atm_iv", "rv_annualized"]:
        sub = valid[["abs_return", col]].dropna()
        if len(sub) < 5:
            continue
        x = sub[col].values
        y_sub = sub["abs_return"].values
        x_with_const = np.column_stack([np.ones(len(x)), x])
        beta = np.linalg.lstsq(x_with_const, y_sub, rcond=None)[0]
        y_hat = x_with_const @ beta
        ss_res = np.sum((y_sub - y_hat)**2)
        ss_tot = np.sum((y_sub - y_sub.mean())**2)
        r2 = 1 - ss_res / ss_tot
        print(f"\n  |return| ~ {col}:")
        print(f"    Intercept: {beta[0]:.6f}")
        print(f"    Slope:     {beta[1]:.6f}")
        print(f"    R²:        {r2:.4f}")


if __name__ == "__main__":
    main()
