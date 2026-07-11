#!/usr/bin/env python3
"""
Merge the Hedgeye risk-range scrape (checkpoint/final CSVs downloaded by the
browser session) into one clean dataset: hedgeye_rr_full.csv.

Input files (pipe-delimited, header date_slug|ticker|trend|buy_trade|sell_trade|prev_close):
  ~/Downloads/hedgeye_rr_checkpoint_*.csv and hedgeye_rr_final.csv

Cleaning:
  - slug like 'november-28-2025' -> date 2025-11-28
  - numbers may contain thousands commas -> stripped
  - Hedgeye lists BUY TRADE / SELL TRADE; for yield instruments buy>sell, so we
    also emit low/high = min/max for band work, keeping the raw columns
  - dedupe on (date, ticker), keeping the first occurrence
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DOWNLOADS = Path.home() / "Downloads"

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def slug_to_date(slug: str):
    s = re.sub(r"^(correction|update[d]?)-", "", slug.strip().lower())
    m = re.match(r"([a-z]+)-(\d{1,2})(?:st|nd|rd|th)?-(\d{4})", s)
    if not m or m.group(1) not in MONTHS:
        return None
    return pd.Timestamp(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))


def to_num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return float("nan")


def main():
    files = sorted(DOWNLOADS.glob("hedgeye_rr_*.csv"))
    if not files:
        raise SystemExit("No hedgeye_rr_*.csv files found in Downloads")
    frames = []
    for f in files:
        df = pd.read_csv(f, sep="|")
        df["source_file"] = f.name
        frames.append(df)
        print(f"  {f.name}: {len(df)} rows")
    df = pd.concat(frames, ignore_index=True)

    df["date"] = df["date_slug"].map(slug_to_date)
    bad_slugs = df[df["date"].isna()]["date_slug"].unique()
    if len(bad_slugs):
        print(f"WARN: {len(bad_slugs)} unparseable slugs, e.g. {bad_slugs[:5]}")
    df = df.dropna(subset=["date"])

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["trend"] = df["trend"].fillna("").astype(str).str.strip().str.upper()
    for c in ("buy_trade", "sell_trade", "prev_close"):
        df[c] = df[c].map(to_num)
    df = df.dropna(subset=["buy_trade", "sell_trade"])
    df["low"] = df[["buy_trade", "sell_trade"]].min(axis=1)
    df["high"] = df[["buy_trade", "sell_trade"]].max(axis=1)

    before = len(df)
    # corrections outrank the original post for the same (date, ticker)
    df["is_correction"] = df["date_slug"].str.lower().str.startswith("correction")
    df = (df.sort_values(["date", "ticker", "is_correction"], ascending=[True, True, False])
            .drop_duplicates(subset=["date", "ticker"], keep="first"))
    print(f"Dedupe: {before} -> {len(df)}")

    out = df[["date", "ticker", "trend", "buy_trade", "sell_trade", "prev_close", "low", "high"]]
    dest = ROOT / "hedgeye_rr_full.csv"
    out.to_csv(dest, index=False, float_format="%.4f")
    print(f"\nSaved {len(out)} rows -> {dest}")
    print(f"Date range: {out['date'].min().date()} -> {out['date'].max().date()}")
    print(f"Tickers: {out['ticker'].nunique()}")
    print(f"Trend flag coverage: {(out['trend'] != '').mean():.1%}")
    per_year = out.groupby(out["date"].dt.year).size()
    print("\nRows per year:")
    print(per_year.to_string())


if __name__ == "__main__":
    main()
