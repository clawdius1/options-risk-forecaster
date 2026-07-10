#!/usr/bin/env python3
"""
On-demand risk ranges for ANY ticker (the "punch in ZS / BE / DDOG" tool).

  python rr.py ZS DDOG BE TSLA

For each ticker: refreshes OHLCV into DuckDB, computes next-day risk ranges from
trailing realized vol (60d default — best lookback per RESULTS.md §D), reports
today's volume/velocity state, and says what the volume+velocity algo would do.
Every run is logged to the forecasts table (paper trail for live validation).

This is the statistical engine only — no options data needed, so it works for
any listed symbol immediately.
"""

from __future__ import annotations

import argparse
from datetime import date

import numpy as np
import pandas as pd
from tabulate import tabulate

import db


def analyze(con, ticker: str, sigma_lb: int, vol_lb: int, k: float) -> dict | None:
    t = ticker.upper()
    last = con.execute("SELECT MAX(date) FROM prices WHERE ticker = ?", [t]).fetchone()[0]
    if last is None or (date.today() - last).days > 0:
        db.load_prices(con, [t], start="2019-01-01")
    h = con.execute(
        "SELECT date, open, high, low, close, volume FROM prices WHERE ticker = ? ORDER BY date", [t]
    ).fetch_df()
    if len(h) < sigma_lb + 5:
        print(f"  {t}: insufficient history ({len(h)} bars)")
        return None
    h["ret"] = h["close"].pct_change()
    sigma = float(h["ret"].tail(sigma_lb).std())
    S = float(h["close"].iloc[-1])
    vol_avg = float(h["volume"].tail(vol_lb + 1).head(vol_lb).mean())
    vol_today = float(h["volume"].iloc[-1])
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else np.nan
    std_move = float(h["ret"].iloc[-1]) / sigma if sigma > 0 else np.nan
    sma200 = float(h["close"].tail(200).mean())
    trend_state = "UPTREND" if S > sma200 else "DOWNTREND"
    asof = pd.Timestamp(h["date"].iloc[-1]).date()

    move = S * sigma
    lo1, hi1 = S - move, S + move
    lok, hik = S - k * move, S + k * move

    # what would the vv algo say about TODAY's bar (params from v2 walk-forward)
    signal = "no signal"
    if std_move <= -1.5 and vol_ratio < 1.5 and trend_state == "UPTREND":
        signal = "FADE candidate: big down day on unremarkable volume in an uptrend → buy-the-dip setup"
    elif std_move <= -1.5:
        signal = "big down day but " + ("heavy volume (real flow)" if vol_ratio >= 1.5 else "downtrend") + " → stand aside"
    elif std_move >= 1.5 and vol_ratio >= 2.0:
        signal = "BREAKOUT candidate: big up day on heavy volume → follow-the-flow setup"
    elif std_move >= 1.5:
        signal = "big up day but volume not heavy → no confirmation, skip"

    return {
        "ticker": t, "asof": asof, "close": S, "sigma": sigma,
        "lo1": lo1, "hi1": hi1, "lok": lok, "hik": hik, "k": k,
        "vol_ratio": vol_ratio, "std_move": std_move,
        "trend": trend_state, "signal": signal,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="On-demand next-day risk ranges for any ticker")
    p.add_argument("tickers", nargs="+")
    p.add_argument("--sigma-lookback", type=int, default=60)
    p.add_argument("--vol-lookback", type=int, default=20)
    p.add_argument("--k", type=float, default=2.0, help="band multiplier (empirical ~84%% coverage)")
    p.add_argument("--no-log", action="store_true", help="skip writing to forecasts table")
    args = p.parse_args(argv)

    con = db.connect()
    results = []
    for t in args.tickers:
        r = analyze(con, t, args.sigma_lookback, args.vol_lookback, args.k)
        if r:
            results.append(r)

    if not results:
        return
    rows = [[r["ticker"], r["asof"], f"${r['close']:.2f}",
             f"${r['lok']:.2f} — ${r['hik']:.2f}", f"${r['lo1']:.2f} — ${r['hi1']:.2f}",
             f"{r['sigma']:.2%}", f"{r['std_move']:+.1f}σ", f"{r['vol_ratio']:.2f}×",
             r["trend"]] for r in results]
    print()
    print(tabulate(rows, headers=["Ticker", "As of", "Close", f"{args.k:.0f}σ range (next day)",
                                  "1σ range", "Daily vol", "Today move", "Volume", "Trend"],
                   tablefmt="simple"))
    print()
    for r in results:
        print(f"  {r['ticker']}: {r['signal']}")

    if not args.no_log:
        db.save_forecast(con, [{
            "for_date": r["asof"] + pd.Timedelta(days=1), "ticker": r["ticker"],
            "method": f"rv{args.sigma_lookback}_{args.k:g}s", "low": r["lok"], "high": r["hik"],
            "center": r["close"], "sigma_daily": r["sigma"], "vol_ratio": r["vol_ratio"],
            "trend_state": r["trend"],
            "params": f"sigma_lb={args.sigma_lookback},vol_lb={args.vol_lookback},k={args.k}",
        } for r in results])
        print(f"\n  Logged {len(results)} forecast(s) to {db.DB_PATH.name}")
    con.close()


if __name__ == "__main__":
    main()
