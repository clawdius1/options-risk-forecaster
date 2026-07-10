#!/usr/bin/env python3
"""
Fetch historical equity IV from AlphaQuery (free chart API) and cache to CSV.

Endpoint (discovered from site JS):
  GET https://www.alphaquery.com/data/option-statistic-chart
      ?ticker=AAPL&perType=30-Day&identifier=iv-mean

Notes
- Free window ≈ last ~62 trading days (observed).
- perType values: 10-Day, 20-Day, 30-Day, 60-Day, 90-Day, 120-Day, 150-Day, 180-Day
  (these are IV tenor, NOT history length — history length is ~same for all).
- identifier values used: iv-mean, iv-call, iv-put, iv-mean-skew, historical-volatility
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "historical_iv.csv"
TICKERS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "ORCL", "TSLA"]
IDENTIFIERS = ["iv-mean", "iv-call", "iv-put", "iv-mean-skew", "historical-volatility"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def fetch_series(ticker: str, per_type: str, identifier: str, retries: int = 3) -> list[dict]:
    q = urllib.parse.urlencode(
        {"ticker": ticker, "perType": per_type, "identifier": identifier}
    )
    url = f"https://www.alphaquery.com/data/option-statistic-chart?{q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/javascript,*/*;q=0.01",
            "Referer": f"https://www.alphaquery.com/stock/{ticker}/volatility-option-statistics/30-day/iv-mean",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(0.8 * attempt)
    raise RuntimeError(f"{ticker} {per_type} {identifier}: {last_err}")


def collect(tickers: list[str], per_type: str, sleep_s: float) -> pd.DataFrame:
    frames = []
    for t in tickers:
        print(f"  {t}…", flush=True)
        series = {}
        for ident in IDENTIFIERS:
            try:
                data = fetch_series(t, per_type, ident)
                pairs = []
                for row in data:
                    if row.get("value") is None:
                        continue
                    pairs.append((pd.Timestamp(row["x"]).tz_localize(None).normalize(), float(row["value"])))
                s = pd.Series({d: v for d, v in pairs})
                series[ident.replace("-", "_")] = s
                print(f"    {ident}: n={len(s)} {s.index.min().date()} → {s.index.max().date()}")
            except Exception as e:
                print(f"    {ident}: FAIL {e}")
            time.sleep(sleep_s)
        if not series:
            continue
        df = pd.DataFrame(series)
        df["ticker"] = t
        df["date"] = df.index
        df = df.reset_index(drop=True)
        frames.append(df)
    if not frames:
        raise SystemExit("No IV data pulled.")
    out = pd.concat(frames, ignore_index=True)
    # friendly cols
    out = out.rename(
        columns={
            "iv_mean": "atm_iv",
            "iv_call": "call_iv",
            "iv_put": "put_iv",
            "iv_mean_skew": "iv_skew",
            "historical_volatility": "aq_hist_vol",
        }
    )
    cols = ["date", "ticker", "atm_iv", "call_iv", "put_iv", "iv_skew", "aq_hist_vol"]
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[cols].sort_values(["date", "ticker"]).reset_index(drop=True)
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", default=TICKERS)
    p.add_argument("--per-type", default="30-Day", help="IV tenor: 10/20/30/60/90/120/150/180-Day")
    p.add_argument("--sleep", type=float, default=0.35)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    args = p.parse_args(argv)

    print(f"Fetching AlphaQuery historical IV  perType={args.per_type}")
    print(f"Tickers: {args.tickers}")
    df = collect(args.tickers, args.per_type, args.sleep)
    out = Path(args.out)
    df.to_csv(out, index=False, float_format="%.8f")
    print(f"\nSaved {len(df)} rows → {out}")
    print(f"Date range: {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"Tickers: {sorted(df['ticker'].unique())}")
    print(df.groupby("ticker").size().to_string())


if __name__ == "__main__":
    main()
