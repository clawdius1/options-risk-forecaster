#!/usr/bin/env python3
"""
Options-Implied Next-Day Risk Range Forecaster

Blends options-market implied volatility with realized (historical) volatility
to forecast the next 24-hour price range for one or more stocks.

Usage:
    python forecast.py --tickers TSLA NVDA META --weight 0.5
    python forecast.py --tickers AAPL --weight 0.7 --save-csv results.csv
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.interpolate import interp1d
from tabulate import tabulate

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TickerData:
    """All raw + derived inputs for one ticker."""
    ticker: str
    S: float                          # last close price
    stock_volume: float               # prior-day share volume
    # Options chain (nearest expiry)
    option_chain: pd.DataFrame = field(repr=False)
    # Derived
    atm_iv: float = np.nan            # interpolated ATM implied vol (annualized)
    sigma_daily: float = np.nan       # 20-day realized daily vol
    total_option_volume: float = 0.0
    put_volume: float = 0.0
    call_volume: float = 0.0
    total_open_interest: float = 0.0
    put_oi: float = 0.0
    call_oi: float = 0.0
    # Diagnostics
    iv_rv_spread: float = np.nan
    os_ratio: float = np.nan
    put_call_volume_ratio: float = np.nan
    iv_skew: float = np.nan           # ATM put IV − ATM call IV
    # Warnings collected during processing
    warnings: list[str] = field(default_factory=list)


@dataclass
class RangeEstimate:
    """Range estimates for one method (IV, RV, or blended)."""
    label: str
    move: float                       # 1-sigma dollar move
    band_1s: tuple[float, float]      # (low, high)  ~68%
    band_2s: tuple[float, float]      # (low, high)  ~95%


@dataclass
class ForecastResult:
    """Complete forecast for one ticker."""
    ticker: str
    data: TickerData
    range_iv: Optional[RangeEstimate] = None
    range_rv: Optional[RangeEstimate] = None
    range_blend: Optional[RangeEstimate] = None


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _safe_mean(series: pd.Series) -> float:
    """Return mean of a Series, ignoring NaN. Returns NaN if all NaN."""
    cleaned = series.dropna()
    if cleaned.empty:
        return np.nan
    return float(cleaned.mean())


def _safe_sum(series: pd.Series) -> float:
    """Return sum of a Series, treating NaN as 0."""
    return float(series.fillna(0).sum())


def get_spot_and_realized_vol(ticker: str, lookback: int = 20) -> tuple[float, float, float, list[str]]:
    """
    Fetch last close price, prior-day volume, and realized daily vol.

    Returns:
        (S, stock_volume, sigma_daily, warnings)
    """
    warnings_list: list[str] = []
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
    except Exception as e:
        warnings_list.append(f"Failed to fetch history for {ticker}: {e}")
        return np.nan, np.nan, np.nan, warnings_list

    if hist.empty:
        warnings_list.append(f"No price history returned for {ticker}.")
        return np.nan, np.nan, np.nan, warnings_list

    if "Close" not in hist.columns:
        warnings_list.append(f"No 'Close' column in history for {ticker}.")
        return np.nan, np.nan, np.nan, warnings_list

    S = float(hist["Close"].iloc[-1])
    stock_volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else np.nan

    # Realized vol: std of last `lookback` daily simple returns
    closes = hist["Close"].dropna()
    if len(closes) < lookback + 1:
        warnings_list.append(
            f"{ticker}: only {len(closes)} data points, need {lookback + 1} for realized vol."
        )
        # Use whatever we have
        returns = closes.pct_change().dropna()
    else:
        returns = closes.pct_change().tail(lookback).dropna()

    sigma_daily = float(returns.std()) if not returns.empty else np.nan

    if np.isnan(sigma_daily):
        warnings_list.append(f"{ticker}: could not compute realized volatility.")

    return S, stock_volume, sigma_daily, warnings_list


def get_atm_iv_and_volume(
    ticker: str, S: float
) -> tuple[pd.DataFrame, float, float, float, float, float, float, float, float, list[str]]:
    """
    Fetch the nearest-expiry option chain and compute ATM IV + volume stats.

    ATM IV is interpolated from the *average* of call and put IV at each strike,
    then evaluated at the strike closest to S.

    Returns:
        (chain_df, atm_iv, total_opt_vol, put_vol, call_vol,
         total_oi, put_oi, call_oi, iv_skew, warnings)
    """
    warnings_list: list[str] = []
    empty_chain = pd.DataFrame()

    try:
        t = yf.Ticker(ticker)
        expirations = t.options
    except Exception as e:
        warnings_list.append(f"Failed to fetch options for {ticker}: {e}")
        return empty_chain, np.nan, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, np.nan, warnings_list

    if not expirations:
        warnings_list.append(f"{ticker}: no options expirations found.")
        return empty_chain, np.nan, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, np.nan, warnings_list

    nearest_expiry = expirations[0]

    try:
        chain = t.option_chain(nearest_expiry)
    except Exception as e:
        warnings_list.append(f"Failed to fetch option chain for {ticker} {nearest_expiry}: {e}")
        return empty_chain, np.nan, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, np.nan, warnings_list

    calls: pd.DataFrame = chain.calls.copy()
    puts: pd.DataFrame = chain.puts.copy()

    # --- Volume stats ---
    call_vol = _safe_sum(calls["volume"]) if "volume" in calls.columns else 0.0
    put_vol = _safe_sum(puts["volume"]) if "volume" in puts.columns else 0.0
    total_opt_vol = call_vol + put_vol

    call_oi = _safe_sum(calls["openInterest"]) if "openInterest" in calls.columns else 0.0
    put_oi = _safe_sum(puts["openInterest"]) if "openInterest" in puts.columns else 0.0
    total_oi = call_oi + put_oi

    # --- ATM IV interpolation ---
    # Build a combined view: for each strike, average call & put IV
    atm_iv = np.nan
    iv_skew = np.nan

    if "impliedVolatility" not in calls.columns or "impliedVolatility" not in puts.columns:
        warnings_list.append(f"{ticker}: impliedVolatility column missing from chain.")
        return empty_chain, np.nan, total_opt_vol, put_vol, call_vol, total_oi, put_oi, call_oi, np.nan, warnings_list

    # Drop rows where IV is NaN
    calls_iv = calls[["strike", "impliedVolatility"]].dropna().sort_values("strike")
    puts_iv = puts[["strike", "impliedVolatility"]].dropna().sort_values("strike")

    if calls_iv.empty and puts_iv.empty:
        warnings_list.append(f"{ticker}: no valid IV data in option chain.")
        return empty_chain, np.nan, total_opt_vol, put_vol, call_vol, total_oi, put_oi, call_oi, np.nan, warnings_list

    # Merge on strike to average
    merged = pd.merge(calls_iv, puts_iv, on="strike", suffixes=("_call", "_put"), how="outer").sort_values("strike")
    merged["avg_iv"] = merged[["impliedVolatility_call", "impliedVolatility_put"]].mean(axis=1, skipna=True)

    valid = merged.dropna(subset=["avg_iv"])
    if valid.empty:
        warnings_list.append(f"{ticker}: no valid averaged IV after merge.")
        return empty_chain, np.nan, total_opt_vol, put_vol, call_vol, total_oi, put_oi, call_oi, np.nan, warnings_list

    strikes = valid["strike"].values
    avg_ivs = valid["avg_iv"].values

    if len(strikes) >= 2:
        # Interpolate (linear) — clamp to range
        f = interp1d(strikes, avg_ivs, kind="linear", fill_value="extrapolate")
        atm_iv = float(np.clip(f(S), avg_ivs.min(), avg_ivs.max()))
    elif len(strikes) == 1:
        atm_iv = float(avg_ivs[0])
        warnings_list.append(f"{ticker}: only one valid strike for IV interpolation; using that value.")

    # IV skew: ATM put IV − ATM call IV
    # Find nearest strike to S in each side
    if not calls_iv.empty and not puts_iv.empty:
        nearest_call_idx = (calls_iv["strike"] - S).abs().idxmin()
        nearest_put_idx = (puts_iv["strike"] - S).abs().idxmin()
        atm_call_iv = float(calls_iv.loc[nearest_call_idx, "impliedVolatility"])
        atm_put_iv = float(puts_iv.loc[nearest_put_idx, "impliedVolatility"])
        iv_skew = atm_put_iv - atm_call_iv

    # Combine chain for return
    calls["side"] = "call"
    puts["side"] = "put"
    full_chain = pd.concat([calls, puts], ignore_index=True)

    return full_chain, atm_iv, total_opt_vol, put_vol, call_vol, total_oi, put_oi, call_oi, iv_skew, warnings_list


def build_ticker_data(ticker: str) -> TickerData:
    """Orchestrate data fetch and derived feature computation for one ticker."""
    # 1. Price + realized vol
    S, stock_volume, sigma_daily, w1 = get_spot_and_realized_vol(ticker)

    # 2. Options chain + ATM IV + volume stats
    chain, atm_iv, total_opt_vol, put_vol, call_vol, total_oi, put_oi, call_oi, iv_skew, w2 = (
        get_atm_iv_and_volume(ticker, S)
    )

    all_warnings = w1 + w2

    # 3. Derived diagnostics
    annualized_rv = sigma_daily * np.sqrt(252) if not np.isnan(sigma_daily) else np.nan
    iv_rv_spread = (atm_iv - annualized_rv) if (not np.isnan(atm_iv) and not np.isnan(annualized_rv)) else np.nan

    os_ratio = (total_opt_vol / stock_volume) if (stock_volume and stock_volume > 0) else np.nan

    put_call_vol_ratio = (put_vol / call_vol) if call_vol > 0 else np.nan

    return TickerData(
        ticker=ticker.upper(),
        S=S,
        stock_volume=stock_volume,
        option_chain=chain,
        atm_iv=atm_iv,
        sigma_daily=sigma_daily,
        total_option_volume=total_opt_vol,
        put_volume=put_vol,
        call_volume=call_vol,
        total_open_interest=total_oi,
        put_oi=put_oi,
        call_oi=call_oi,
        iv_rv_spread=iv_rv_spread,
        os_ratio=os_ratio,
        put_call_volume_ratio=put_call_vol_ratio,
        iv_skew=iv_skew,
        warnings=all_warnings,
    )


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def compute_ranges(data: TickerData, weight: float) -> ForecastResult:
    """
    Compute IV-only, RV-only, and blended range estimates.

    Args:
        data:   Populated TickerData.
        weight: Blend weight on IV (0 = pure RV, 1 = pure IV).
    """
    result = ForecastResult(ticker=data.ticker, data=data)
    S = data.S

    if np.isnan(S):
        data.warnings.append("Spot price is NaN — cannot compute ranges.")
        return result

    sqrt252 = np.sqrt(252)

    # IV-implied move
    if not np.isnan(data.atm_iv):
        move_iv = S * data.atm_iv / sqrt252
        result.range_iv = RangeEstimate(
            label="IV-implied",
            move=move_iv,
            band_1s=(S - move_iv, S + move_iv),
            band_2s=(S - 2 * move_iv, S + 2 * move_iv),
        )

    # RV-implied move
    if not np.isnan(data.sigma_daily):
        move_rv = S * data.sigma_daily
        result.range_rv = RangeEstimate(
            label="Realized",
            move=move_rv,
            band_1s=(S - move_rv, S + move_rv),
            band_2s=(S - 2 * move_rv, S + 2 * move_rv),
        )

    # Blended move
    has_iv = result.range_iv is not None
    has_rv = result.range_rv is not None

    if has_iv and has_rv:
        move_blend = weight * result.range_iv.move + (1 - weight) * result.range_rv.move
    elif has_iv:
        move_blend = result.range_iv.move
        data.warnings.append("Blending: RV unavailable, using IV-only.")
    elif has_rv:
        move_blend = result.range_rv.move
        data.warnings.append("Blending: IV unavailable, using RV-only.")
    else:
        data.warnings.append("Neither IV nor RV available — no range estimate possible.")
        return result

    result.range_blend = RangeEstimate(
        label=f"Blended (w={weight:.2f})",
        move=move_blend,
        band_1s=(S - move_blend, S + move_blend),
        band_2s=(S - 2 * move_blend, S + 2 * move_blend),
    )

    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _fmt(x: float, prefix: str = "$", decimals: int = 2) -> str:
    if np.isnan(x):
        return "N/A"
    return f"{prefix}{x:,.{decimals}f}"


def _fmt_pct(x: float, decimals: int = 2) -> str:
    if np.isnan(x):
        return "N/A"
    return f"{x * 100:.{decimals}f}%"


def print_report(results: list[ForecastResult], weight: float) -> None:
    """Pretty-print a console report for all tickers."""
    SEP = "=" * 78
    SUBSEP = "-" * 78

    for r in results:
        d = r.data
        print(f"\n{SEP}")
        print(f"  {r.ticker}  —  Next-Day Risk Range Forecast")
        print(f"{SEP}")

        # --- Spot & inputs ---
        print(f"\n  Spot Price (S):        {_fmt(d.S)}")
        print(f"  Stock Volume:          {d.stock_volume:,.0f}" if not np.isnan(d.stock_volume) else "  Stock Volume:          N/A")
        print(f"  ATM IV (annualized):   {_fmt_pct(d.atm_iv)}")
        print(f"  Realized Vol (daily):  {_fmt_pct(d.sigma_daily)}")
        print(f"  Realized Vol (ann.):   {_fmt_pct(d.sigma_daily * np.sqrt(252)) if not np.isnan(d.sigma_daily) else np.nan}")

        # --- Range comparison table ---
        print(f"\n  {'Range Estimates':}")
        print(f"  {SUBSEP}")

        rows = []
        for est in [r.range_iv, r.range_rv, r.range_blend]:
            if est is None:
                continue
            rows.append([
                est.label,
                _fmt(est.move),
                f"{_fmt(est.band_1s[0])} — {_fmt(est.band_1s[1])}",
                f"{_fmt(est.band_2s[0])} — {_fmt(est.band_2s[1])}",
            ])

        if rows:
            headers = ["Method", "1σ Move", "1σ Band (~68%)", "2σ Band (~95%)"]
            print(tabulate(rows, headers=headers, tablefmt="simple",
                           colalign=("left", "right", "center", "center")))
        else:
            print("  No range estimates available.")

        # --- Diagnostics ---
        print(f"\n  {'Diagnostics':}")
        print(f"  {SUBSEP}")
        diag_rows = [
            ["IV − RV Spread", _fmt_pct(d.iv_rv_spread)],
            ["O/S Ratio", f"{d.os_ratio:.4f}" if not np.isnan(d.os_ratio) else "N/A"],
            ["Put/Call Volume Ratio", f"{d.put_call_volume_ratio:.4f}" if not np.isnan(d.put_call_volume_ratio) else "N/A"],
            ["IV Skew (Put IV − Call IV)", _fmt_pct(d.iv_skew)],
            ["Option Volume (total)", f"{d.total_option_volume:,.0f}"],
            ["Call Volume", f"{d.call_volume:,.0f}"],
            ["Put Volume", f"{d.put_volume:,.0f}"],
            ["Open Interest (total)", f"{d.total_open_interest:,.0f}"],
        ]
        print(tabulate(diag_rows, headers=["Feature", "Value"], tablefmt="simple"))

        # --- Warnings ---
        if d.warnings:
            print(f"\n  ⚠  Warnings:")
            for w in d.warnings:
                print(f"     • {w}")

    print(f"\n{SEP}")
    print(f"  Blend weight on IV: {weight:.2f}  |  "
          f"IV-only=1.0  |  RV-only=0.0")
    print(f"{SEP}\n")


def results_to_dataframe(results: list[ForecastResult]) -> pd.DataFrame:
    """Flatten results into a DataFrame suitable for CSV export."""
    rows = []
    for r in results:
        d = r.data
        row: dict = {
            "ticker": r.ticker,
            "spot_price": d.S,
            "stock_volume": d.stock_volume,
            "atm_iv": d.atm_iv,
            "realized_vol_daily": d.sigma_daily,
            "realized_vol_annualized": d.sigma_daily * np.sqrt(252) if not np.isnan(d.sigma_daily) else np.nan,
            "iv_rv_spread": d.iv_rv_spread,
            "os_ratio": d.os_ratio,
            "put_call_volume_ratio": d.put_call_volume_ratio,
            "iv_skew": d.iv_skew,
            "option_volume_total": d.total_option_volume,
            "call_volume": d.call_volume,
            "put_volume": d.put_volume,
            "open_interest_total": d.total_open_interest,
        }
        for est in [r.range_iv, r.range_rv, r.range_blend]:
            if est is None:
                continue
            prefix = est.label.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("=", "")
            row[f"{prefix}_move"] = est.move
            row[f"{prefix}_1s_low"] = est.band_1s[0]
            row[f"{prefix}_1s_high"] = est.band_1s[1]
            row[f"{prefix}_2s_low"] = est.band_2s[0]
            row[f"{prefix}_2s_high"] = est.band_2s[1]
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Options-Implied Next-Day Risk Range Forecaster",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python forecast.py --tickers TSLA NVDA META --weight 0.5",
    )
    parser.add_argument(
        "--tickers", nargs="+", required=True,
        help="One or more ticker symbols (space-separated).",
    )
    parser.add_argument(
        "--weight", type=float, default=0.5,
        help="Blend weight on IV (0 = pure RV, 1 = pure IV). Default: 0.5.",
    )
    parser.add_argument(
        "--save-csv", type=str, default=None,
        help="Optional path to save results as CSV.",
    )
    args = parser.parse_args(argv)

    if not 0.0 <= args.weight <= 1.0:
        parser.error("--weight must be between 0 and 1.")

    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Suppress yfinance progress bars
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    print(f"\nFetching data for: {', '.join(t.upper() for t in args.tickers)}")
    print(f"Blend weight (IV): {args.weight:.2f}\n")

    results: list[ForecastResult] = []
    for ticker in args.tickers:
        data = build_ticker_data(ticker)
        result = compute_ranges(data, args.weight)
        results.append(result)

    # Print report
    print_report(results, args.weight)

    # Optional CSV export
    if args.save_csv:
        df = results_to_dataframe(results)
        out = Path(args.save_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, float_format="%.6f")
        print(f"Results saved to: {out.resolve()}")


if __name__ == "__main__":
    main()
