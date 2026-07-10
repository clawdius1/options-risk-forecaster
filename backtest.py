#!/usr/bin/env python3
"""
Backtest harness: compare research firm's risk ranges against our IV/RV/blended
model and actual next-day price outcomes.

Usage:
    python backtest.py --signals path/to/risk_range_signals.csv
    python backtest.py --signals signals.csv --weight 0.5
    python backtest.py --signals signals.csv --grid-search
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
try:
    from tabulate import tabulate
except ImportError:
    tabulate = None

# ---------------------------------------------------------------------------
# Reuse forecast.py functions (import if available, else inline)
# ---------------------------------------------------------------------------

def _safe_sum(series: pd.Series) -> float:
    return float(series.fillna(0).sum())


def get_spot_and_realized_vol(ticker: str, target_date: str, lookback: int = 20):
    """
    Fetch realized vol using data UP TO target_date (exclusive of future).
    target_date can be 'M/D/YYYY' or 'YYYY-MM-DD'.
    Returns (S_prev_close, stock_volume, sigma_daily, warnings).
    """
    w = []
    try:
        iso_date = _parse_date(target_date)
        t = yf.Ticker(ticker)
        target_ts = pd.Timestamp(iso_date)
        end_ts = target_ts + pd.Timedelta(days=5)
        start_ts = target_ts - pd.Timedelta(days=90)
        hist = t.history(start=start_ts.strftime("%Y-%m-%d"),
                         end=end_ts.strftime("%Y-%m-%d"))
    except Exception as e:
        w.append(f"History fetch failed for {ticker}@{target_date}: {e}")
        return np.nan, np.nan, np.nan, w

    if hist.empty:
        w.append(f"No history for {ticker}@{target_date}")
        return np.nan, np.nan, np.nan, w

    # Normalize index
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    # Get data up to and including target_date
    mask = hist.index <= target_ts
    available = hist[mask]
    if available.empty:
        w.append(f"No history up to {target_date} for {ticker}")
        return np.nan, np.nan, np.nan, w

    S = float(available["Close"].iloc[-1])
    stock_volume = float(available["Volume"].iloc[-1]) if "Volume" in available.columns else np.nan

    # Realized vol from last `lookback` daily returns up to target_date
    closes = available["Close"].dropna()
    if len(closes) < 3:
        w.append(f"Insufficient data for {ticker}@{target_date}")
        return S, stock_volume, np.nan, w

    returns = closes.pct_change().dropna()
    if len(returns) > lookback:
        returns = returns.tail(lookback)
    sigma_daily = float(returns.std()) if not returns.empty else np.nan

    return S, stock_volume, sigma_daily, w


def get_atm_iv(ticker: str, S: float, target_date: str):
    """
    Fetch ATM IV from nearest-expiry chain as of target_date.
    target_date can be 'M/D/YYYY' or 'YYYY-MM-DD'.
    Returns (atm_iv, total_opt_vol, put_vol, call_vol, iv_skew, warnings).
    """
    w = []
    try:
        iso_date = _parse_date(target_date)
        t = yf.Ticker(ticker)
        expirations = t.options
    except Exception as e:
        w.append(f"Options fetch failed for {ticker}@{target_date}: {e}")
        return np.nan, 0.0, 0.0, 0.0, np.nan, w

    if not expirations:
        w.append(f"No options for {ticker}@{target_date}")
        return np.nan, 0.0, 0.0, 0.0, np.nan, w

    # Find nearest expiry AFTER target_date
    target_ts = pd.Timestamp(iso_date)
    valid_expiries = []
    for exp in expirations:
        try:
            exp_dt = pd.Timestamp(exp)
            if exp_dt > target_ts:
                valid_expiries.append((exp_dt, exp))
        except:
            pass

    if not valid_expiries:
        w.append(f"No future expiry for {ticker}@{target_date}")
        return np.nan, 0.0, 0.0, 0.0, np.nan, w

    valid_expiries.sort()
    nearest_expiry = valid_expiries[0][1]

    try:
        chain = t.option_chain(nearest_expiry)
    except Exception as e:
        w.append(f"Chain fetch failed for {ticker}@{target_date}: {e}")
        return np.nan, 0.0, 0.0, 0.0, np.nan, w

    calls = chain.calls.copy()
    puts = chain.puts.copy()

    call_vol = _safe_sum(calls["volume"]) if "volume" in calls.columns else 0.0
    put_vol = _safe_sum(puts["volume"]) if "volume" in puts.columns else 0.0
    total_opt_vol = call_vol + put_vol

    # ATM IV interpolation
    atm_iv = np.nan
    iv_skew = np.nan

    if "impliedVolatility" not in calls.columns or "impliedVolatility" not in puts.columns:
        return np.nan, total_opt_vol, put_vol, call_vol, np.nan, w

    calls_iv = calls[["strike", "impliedVolatility"]].dropna().sort_values("strike")
    puts_iv = puts[["strike", "impliedVolatility"]].dropna().sort_values("strike")

    if calls_iv.empty and puts_iv.empty:
        return np.nan, total_opt_vol, put_vol, call_vol, np.nan, w

    merged = pd.merge(calls_iv, puts_iv, on="strike", suffixes=("_call", "_put"), how="outer").sort_values("strike")
    merged["avg_iv"] = merged[["impliedVolatility_call", "impliedVolatility_put"]].mean(axis=1, skipna=True)
    valid = merged.dropna(subset=["avg_iv"])

    if valid.empty:
        return np.nan, total_opt_vol, put_vol, call_vol, np.nan, w

    strikes = valid["strike"].values
    avg_ivs = valid["avg_iv"].values

    if len(strikes) >= 2:
        from scipy.interpolate import interp1d
        f = interp1d(strikes, avg_ivs, kind="linear", fill_value="extrapolate")
        atm_iv = float(np.clip(f(S), avg_ivs.min(), avg_ivs.max()))
    elif len(strikes) == 1:
        atm_iv = float(avg_ivs[0])

    # IV skew
    if not calls_iv.empty and not puts_iv.empty:
        nearest_call_idx = (calls_iv["strike"] - S).abs().idxmin()
        nearest_put_idx = (puts_iv["strike"] - S).abs().idxmin()
        atm_call_iv = float(calls_iv.loc[nearest_call_idx, "impliedVolatility"])
        atm_put_iv = float(puts_iv.loc[nearest_put_idx, "impliedVolatility"])
        iv_skew = atm_put_iv - atm_call_iv

    return atm_iv, total_opt_vol, put_vol, call_vol, iv_skew, w


def _parse_date(date_str: str) -> str:
    """Convert M/D/YYYY or YYYY-MM-DD to YYYY-MM-DD."""
    date_str = date_str.strip()
    if "/" in date_str:
        dt = pd.Timestamp(date_str)
        return dt.strftime("%Y-%m-%d")
    return date_str


def get_next_day_actual(ticker: str, target_date: str):
    """
    Get the next trading day's OHLC after target_date.
    target_date can be 'M/D/YYYY' or 'YYYY-MM-DD'.
    Returns (next_date, open, high, low, close, volume).
    """
    try:
        iso_date = _parse_date(target_date)
        t = yf.Ticker(ticker)
        target_ts = pd.Timestamp(iso_date)
        end_ts = target_ts + pd.Timedelta(days=10)
        # Fetch from a few days before target to ensure we have the target day
        start_ts = target_ts - pd.Timedelta(days=3)
        hist = t.history(start=start_ts.strftime("%Y-%m-%d"),
                         end=end_ts.strftime("%Y-%m-%d"))
    except Exception:
        return None, np.nan, np.nan, np.nan, np.nan, np.nan

    if hist.empty:
        return None, np.nan, np.nan, np.nan, np.nan, np.nan

    # Normalize index to naive timestamps
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    # Find rows strictly after the target date
    future = hist[hist.index > target_ts]

    if future.empty:
        return None, np.nan, np.nan, np.nan, np.nan, np.nan

    row = future.iloc[0]
    next_date = future.index[0].strftime("%Y-%m-%d")
    return next_date, float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]), float(row["Volume"])


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SignalRow:
    date: str
    ticker: str
    firm_low: float
    firm_high: float
    prev_close: float


@dataclass
class BacktestRow:
    date: str
    ticker: str
    # Firm's forecast
    firm_low: float
    firm_high: float
    prev_close: float
    # Next day actual
    next_date: str = ""
    actual_close: float = np.nan
    actual_high: float = np.nan
    actual_low: float = np.nan
    # Our model inputs
    atm_iv: float = np.nan
    sigma_daily: float = np.nan
    # Our model ranges (1σ)
    our_iv_low: float = np.nan
    our_iv_high: float = np.nan
    our_rv_low: float = np.nan
    our_rv_high: float = np.nan
    our_blend_low: float = np.nan
    our_blend_high: float = np.nan
    # Hit flags
    firm_hit: bool = False
    our_iv_hit: bool = False
    our_rv_hit: bool = False
    our_blend_hit: bool = False
    # Warnings
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main backtest logic
# ---------------------------------------------------------------------------

def parse_signals_csv(path: str) -> list[SignalRow]:
    """Parse the research firm's CSV."""
    rows = []
    with open(path, "r") as f:
        content = f.read()

    # Handle the pipe-delimited format from the document cache
    if "|" in content.split("\n")[0]:
        lines = content.strip().split("\n")
        parsed = []
        for line in lines:
            if "|" in line:
                parts = line.split("|", 1)[1]  # remove line number
                parsed.append(parts)
        reader = csv.DictReader(io.StringIO("\n".join(parsed)))
    else:
        reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        try:
            rows.append(SignalRow(
                date=_parse_date(row["Date"]),
                ticker=row["Ticker"].strip().upper(),
                firm_low=float(row["Low"]),
                firm_high=float(row["High"]),
                prev_close=float(row["Prev. Close"]),
            ))
        except (KeyError, ValueError) as e:
            print(f"  [WARN] Skipping row: {row} ({e})")
    return rows


def run_backtest(signals: list[SignalRow], weight: float = 0.5,
                 exclude: Optional[set] = None) -> list[BacktestRow]:
    """Run the full backtest."""
    exclude = exclude or set()
    results = []

    for i, sig in enumerate(signals):
        key = f"{sig.date}_{sig.ticker}"
        if key in exclude:
            print(f"  [{i+1}/{len(signals)}] SKIP {sig.ticker}@{sig.date} (excluded)")
            continue

        print(f"  [{i+1}/{len(signals)}] {sig.ticker}@{sig.date}...", end=" ", flush=True)
        bt = BacktestRow(
            date=sig.date, ticker=sig.ticker,
            firm_low=sig.firm_low, firm_high=sig.firm_high,
            prev_close=sig.prev_close,
        )

        # 1. Get next day's actual price
        next_date, actual_open, actual_high, actual_low, actual_close, actual_vol = \
            get_next_day_actual(sig.ticker, sig.date)
        bt.next_date = next_date or ""
        bt.actual_close = actual_close
        bt.actual_high = actual_high
        bt.actual_low = actual_low

        if np.isnan(actual_close):
            bt.warnings.append("No next-day actual price available")
            print("NO ACTUAL")
            results.append(bt)
            continue

        # 2. Get our model inputs (as of sig.date)
        S, stock_vol, sigma_daily, w1 = get_spot_and_realized_vol(sig.ticker, sig.date)
        bt.sigma_daily = sigma_daily
        bt.warnings.extend(w1)

        atm_iv, opt_vol, put_vol, call_vol, iv_skew, w2 = \
            get_atm_iv(sig.ticker, sig.prev_close, sig.date)
        bt.atm_iv = atm_iv
        bt.warnings.extend(w2)

        # 3. Compute our ranges
        sqrt252 = np.sqrt(252)

        if not np.isnan(atm_iv):
            move_iv = sig.prev_close * atm_iv / sqrt252
            bt.our_iv_low = sig.prev_close - move_iv
            bt.our_iv_high = sig.prev_close + move_iv

        if not np.isnan(sigma_daily):
            move_rv = sig.prev_close * sigma_daily
            bt.our_rv_low = sig.prev_close - move_rv
            bt.our_rv_high = sig.prev_close + move_rv

        # Blend
        has_iv = not np.isnan(bt.our_iv_low)
        has_rv = not np.isnan(bt.our_rv_low)
        if has_iv and has_rv:
            bt.our_blend_low = weight * bt.our_iv_low + (1 - weight) * bt.our_rv_low
            bt.our_blend_high = weight * bt.our_iv_high + (1 - weight) * bt.our_rv_high
        elif has_iv:
            bt.our_blend_low = bt.our_iv_low
            bt.our_blend_high = bt.our_iv_high
        elif has_rv:
            bt.our_blend_low = bt.our_rv_low
            bt.our_blend_high = bt.our_rv_high

        # 4. Hit tests (using actual close)
        bt.firm_hit = sig.firm_low <= actual_close <= sig.firm_high
        if has_iv:
            bt.our_iv_hit = bt.our_iv_low <= actual_close <= bt.our_iv_high
        if has_rv:
            bt.our_rv_hit = bt.our_rv_low <= actual_close <= bt.our_rv_high
        if not np.isnan(bt.our_blend_low):
            bt.our_blend_hit = bt.our_blend_low <= actual_close <= bt.our_blend_high

        firm_mid = (sig.firm_low + sig.firm_high) / 2
        print(f"actual={actual_close:.0f} firm_hit={bt.firm_hit} iv_hit={bt.our_iv_hit} rv_hit={bt.our_rv_hit} blend_hit={bt.our_blend_hit}")
        results.append(bt)

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(n, d):
    return f"{n/d*100:.1f}%" if d > 0 else "N/A"


def print_backtest_report(results: list[BacktestRow], weight: float) -> None:
    """Print comprehensive backtest report."""
    SEP = "=" * 80
    SUB = "-" * 80

    # Filter to rows with actuals
    valid = [r for r in results if not np.isnan(r.actual_close)]
    n = len(valid)

    print(f"\n{SEP}")
    print(f"  BACKTEST REPORT  |  Blend weight (IV): {weight:.2f}  |  N={n} forecasts")
    print(f"{SEP}")

    if n == 0:
        print("  No valid results.")
        return

    # --- Overall hit rates ---
    firm_hits = sum(1 for r in valid if r.firm_hit)
    iv_hits = sum(1 for r in valid if r.our_iv_hit)
    rv_hits = sum(1 for r in valid if r.our_rv_hit)
    blend_hits = sum(1 for r in valid if r.our_blend_hit)
    n_iv = sum(1 for r in valid if not np.isnan(r.our_iv_low))
    n_rv = sum(1 for r in valid if not np.isnan(r.our_rv_low))
    n_blend = sum(1 for r in valid if not np.isnan(r.our_blend_low))

    print(f"\n  {'Hit Rates (actual close inside band)':}")
    print(f"  {SUB}")
    rows = [
        ["Research Firm", firm_hits, n, _pct(firm_hits, n)],
        ["Our IV-only", iv_hits, n_iv, _pct(iv_hits, n_iv)],
        ["Our RV-only", rv_hits, n_rv, _pct(rv_hits, n_rv)],
        ["Our Blended", blend_hits, n_blend, _pct(blend_hits, n_blend)],
    ]
    headers = ["Method", "Hits", "N", "Hit Rate"]
    if tabulate:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        for r in rows:
            print(f"  {r[0]:20s}  {r[1]}/{r[2]}  {r[3]}")

    # --- Band width comparison ---
    print(f"\n  {'Band Width Comparison ($)':}")
    print(f"  {SUB}")
    firm_widths = [(r.firm_high - r.firm_low) for r in valid]
    iv_widths = [(r.our_iv_high - r.our_iv_low) for r in valid if not np.isnan(r.our_iv_low)]
    rv_widths = [(r.our_rv_high - r.our_rv_low) for r in valid if not np.isnan(r.our_rv_low)]
    blend_widths = [(r.our_blend_high - r.our_blend_low) for r in valid if not np.isnan(r.our_blend_low)]

    width_rows = [
        ["Research Firm", np.mean(firm_widths), np.median(firm_widths), np.std(firm_widths)],
        ["Our IV-only", np.mean(iv_widths), np.median(iv_widths), np.std(iv_widths)] if iv_widths else ["Our IV-only", "N/A", "N/A", "N/A"],
        ["Our RV-only", np.mean(rv_widths), np.median(rv_widths), np.std(rv_widths)] if rv_widths else ["Our RV-only", "N/A", "N/A", "N/A"],
        ["Our Blended", np.mean(blend_widths), np.median(blend_widths), np.std(blend_widths)] if blend_widths else ["Our Blended", "N/A", "N/A", "N/A"],
    ]
    wh = ["Method", "Mean Width", "Median Width", "Std Width"]
    if tabulate:
        print(tabulate(width_rows, headers=wh, tablefmt="simple", floatfmt=".2f"))
    else:
        for r in width_rows:
            print(f"  {r}")

    # --- By ticker ---
    print(f"\n  {'Hit Rates by Ticker':}")
    print(f"  {SUB}")
    tickers = sorted(set(r.ticker for r in valid))
    ticker_rows = []
    for t in tickers:
        tv = [r for r in valid if r.ticker == t]
        fh = sum(1 for r in tv if r.firm_hit)
        ih = sum(1 for r in tv if r.our_iv_hit)
        rh = sum(1 for r in tv if r.our_rv_hit)
        bh = sum(1 for r in tv if r.our_blend_hit)
        ticker_rows.append([
            t, len(tv),
            _pct(fh, len(tv)),
            _pct(ih, len(tv)),
            _pct(rh, len(tv)),
            _pct(bh, len(tv)),
        ])

    th = ["Ticker", "N", "Firm", "IV", "RV", "Blend"]
    if tabulate:
        print(tabulate(ticker_rows, headers=th, tablefmt="simple"))
    else:
        for r in ticker_rows:
            print(f"  {r}")

    # --- Directional bias analysis ---
    print(f"\n  {'Firm Directional Bias (midpoint vs prev close)':}")
    print(f"  {SUB}")
    biases = []
    for r in valid:
        mid = (r.firm_low + r.firm_high) / 2
        bias_pct = (mid - r.prev_close) / r.prev_close * 100
        biases.append(bias_pct)

    print(f"  Mean bias:   {np.mean(biases):+.2f}%  (positive = bullish bias)")
    print(f"  Median bias: {np.median(biases):+.2f}%")
    print(f"  Std bias:    {np.std(biases):.2f}%")
    print(f"  Min/Max:     {np.min(biases):+.2f}% / {np.max(biases):+.2f}%")

    # --- Detailed row-by-row ---
    print(f"\n  {'Detailed Results':}")
    print(f"  {SUB}")
    detail_rows = []
    for r in valid:
        actual_ret = (r.actual_close - r.prev_close) / r.prev_close * 100
        detail_rows.append([
            r.date, r.ticker,
            f"{r.firm_low:.0f}-{r.firm_high:.0f}",
            f"{r.our_iv_low:.0f}-{r.our_iv_high:.0f}" if not np.isnan(r.our_iv_low) else "N/A",
            f"{r.our_rv_low:.0f}-{r.our_rv_high:.0f}" if not np.isnan(r.our_rv_low) else "N/A",
            f"{r.our_blend_low:.0f}-{r.our_blend_high:.0f}" if not np.isnan(r.our_blend_low) else "N/A",
            f"{r.actual_close:.0f}",
            f"{actual_ret:+.1f}%",
            "✓" if r.firm_hit else "✗",
            "✓" if r.our_iv_hit else "✗",
            "✓" if r.our_rv_hit else "✗",
            "✓" if r.our_blend_hit else "✗",
        ])

    dh = ["Date", "Tkr", "Firm", "IV", "RV", "Blend", "Close", "Ret", "F", "I", "R", "B"]
    if tabulate:
        print(tabulate(detail_rows, headers=dh, tablefmt="simple"))
    else:
        for r in detail_rows:
            print(f"  {r}")

    # --- Warnings ---
    warned = [r for r in results if r.warnings]
    if warned:
        print(f"\n  ⚠  Warnings ({len(warned)} rows):")
        for r in warned[:10]:
            for w in r.warnings[:2]:
                print(f"     • {r.ticker}@{r.date}: {w}")
        if len(warned) > 10:
            print(f"     ... and {len(warned) - 10} more")

    print(f"\n{SEP}\n")


def grid_search_weight(signals: list[SignalRow], exclude: Optional[set] = None,
                       weights: Optional[list] = None) -> None:
    """Grid-search blend weight to maximize blended hit rate."""
    if weights is None:
        weights = [round(x * 0.1, 1) for x in range(0, 11)]

    print(f"\n{'='*60}")
    print(f"  GRID SEARCH: Blend weight optimization")
    print(f"{'='*60}")
    print(f"  Testing weights: {weights}")
    print(f"  (This will re-run the backtest for each weight — may take a while)\n")

    best_w = 0.5
    best_rate = 0.0
    grid_results = []

    for w in weights:
        print(f"  --- weight={w:.1f} ---")
        results = run_backtest(signals, weight=w, exclude=exclude)
        valid = [r for r in results if not np.isnan(r.actual_close) and not np.isnan(r.our_blend_low)]
        n = len(valid)
        if n == 0:
            print(f"  No valid results for w={w:.1f}")
            grid_results.append((w, 0, 0))
            continue
        hits = sum(1 for r in valid if r.our_blend_hit)
        rate = hits / n
        grid_results.append((w, hits, n))
        print(f"  Hit rate: {hits}/{n} = {rate:.1%}")
        if rate > best_rate:
            best_rate = rate
            best_w = w

    print(f"\n  {'Weight':>8s}  {'Hits':>5s}  {'N':>5s}  {'Hit Rate':>10s}")
    print(f"  {'-'*35}")
    for w, h, n in grid_results:
        marker = " ← BEST" if w == best_w else ""
        rate = f"{h/n:.1%}" if n > 0 else "N/A"
        print(f"  {w:8.1f}  {h:5d}  {n:5d}  {rate:>10s}{marker}")

    print(f"\n  Optimal weight: {best_w:.1f}  (hit rate: {best_rate:.1%})")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backtest research firm risk ranges vs our IV/RV model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python backtest.py --signals risk_range_signals.csv --weight 0.5",
    )
    parser.add_argument("--signals", required=True, help="Path to the research firm's CSV.")
    parser.add_argument("--weight", type=float, default=0.5,
                        help="Blend weight on IV. Default: 0.5")
    parser.add_argument("--grid-search", action="store_true",
                        help="Grid-search optimal blend weight.")
    parser.add_argument("--save-csv", type=str, default=None,
                        help="Save detailed results to CSV.")
    args = parser.parse_args(argv)
    return args


def main(argv=None):
    args = parse_args(argv)

    warnings.filterwarnings("ignore")

    print(f"\nLoading signals from: {args.signals}")
    signals = parse_signals_csv(args.signals)
    print(f"Loaded {len(signals)} signal rows")

    # Known bad data points to exclude
    exclude = {
        "2026-06-17_NFLX",  # Low=7 in raw data was a typo (fixed in our CSV, but kept as safety)
    }

    if args.grid_search:
        grid_search_weight(signals, exclude=exclude)
    else:
        print(f"\nRunning backtest (weight={args.weight})...\n")
        results = run_backtest(signals, weight=args.weight, exclude=exclude)
        print_backtest_report(results, args.weight)

        if args.save_csv:
            rows = []
            for r in results:
                rows.append({
                    "date": r.date, "ticker": r.ticker,
                    "firm_low": r.firm_low, "firm_high": r.firm_high,
                    "prev_close": r.prev_close,
                    "next_date": r.next_date,
                    "actual_close": r.actual_close,
                    "atm_iv": r.atm_iv,
                    "sigma_daily": r.sigma_daily,
                    "our_iv_low": r.our_iv_low, "our_iv_high": r.our_iv_high,
                    "our_rv_low": r.our_rv_low, "our_rv_high": r.our_rv_high,
                    "our_blend_low": r.our_blend_low, "our_blend_high": r.our_blend_high,
                    "firm_hit": r.firm_hit,
                    "our_iv_hit": r.our_iv_hit,
                    "our_rv_hit": r.our_rv_hit,
                    "our_blend_hit": r.our_blend_hit,
                })
            df = pd.DataFrame(rows)
            out = Path(args.save_csv)
            df.to_csv(out, index=False, float_format="%.4f")
            print(f"Results saved to: {out.resolve()}")


if __name__ == "__main__":
    main()
