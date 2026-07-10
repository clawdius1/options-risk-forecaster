#!/usr/bin/env python3
"""
DuckDB storage layer for the risk-range project.

One file (market.duckdb, gitignored) holds everything:
  hedgeye_signals : every scraped Hedgeye row (all ~35 instruments, trend flag)
  prices          : OHLCV cache per ticker (yfinance, auto-adjusted) — ANY ticker
  earnings        : earnings dates per ticker
  forecasts       : ranges WE generate (live tracking / paper trail)

CLI:
  python db.py init
  python db.py load-hedgeye [--csv hedgeye_rr_full.csv]
  python db.py load-prices TICKER [TICKER ...] [--start 2013-01-01]
  python db.py load-earnings TICKER [TICKER ...]
  python db.py status
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "market.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS hedgeye_signals (
    date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    trend VARCHAR,            -- BULLISH / BEARISH / NEUTRAL / '' (older rows)
    buy_trade DOUBLE,
    sell_trade DOUBLE,
    prev_close DOUBLE,
    low DOUBLE,               -- min(buy, sell): yields list buy above sell
    high DOUBLE,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS prices (
    date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS earnings (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS forecasts (
    run_ts TIMESTAMP NOT NULL,
    for_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    method VARCHAR NOT NULL,  -- e.g. rv60_2s, blend_w05
    low DOUBLE, high DOUBLE, center DOUBLE,
    sigma_daily DOUBLE, vol_ratio DOUBLE, trend_state VARCHAR,
    params VARCHAR
);
"""


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH))
    con.execute(SCHEMA)
    return con


def load_hedgeye(con, csv_path: Path) -> None:
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["trend"] = df.get("trend", "").fillna("")
    con.execute("DELETE FROM hedgeye_signals")
    con.execute("""
        INSERT INTO hedgeye_signals
        SELECT date, ticker, trend, buy_trade, sell_trade, prev_close, low, high
        FROM df
    """)
    n, dmin, dmax, nt = con.execute(
        "SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM hedgeye_signals").fetchone()
    print(f"hedgeye_signals: {n} rows, {dmin} → {dmax}, {nt} tickers")


def load_prices(con, tickers: list[str], start: str) -> None:
    import yfinance as yf
    for t in [t.upper() for t in tickers]:
        h = yf.download(t, start=start, auto_adjust=True, progress=False)
        if h.empty:
            print(f"  {t}: NO DATA")
            continue
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        h = h.reset_index()
        h["ticker"] = t
        h = h.rename(columns={"Date": "date", "Open": "open", "High": "high",
                              "Low": "low", "Close": "close", "Volume": "volume"})
        h["date"] = pd.to_datetime(h["date"]).dt.tz_localize(None).dt.normalize()
        con.execute("DELETE FROM prices WHERE ticker = ?", [t])
        con.execute("INSERT INTO prices SELECT date, ticker, open, high, low, close, volume FROM h")
        print(f"  {t}: {len(h)} bars {h['date'].min().date()} → {h['date'].max().date()}")


def load_earnings(con, tickers: list[str]) -> None:
    import yfinance as yf
    for t in [t.upper() for t in tickers]:
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=28)
            if ed is None or ed.empty:
                print(f"  {t}: none")
                continue
            dates = pd.to_datetime(ed.index).tz_localize(None).normalize().unique()
            df = pd.DataFrame({"ticker": t, "date": dates})
            con.execute("DELETE FROM earnings WHERE ticker = ?", [t])
            con.execute("INSERT INTO earnings SELECT ticker, date FROM df")
            print(f"  {t}: {len(df)} earnings dates")
        except Exception as e:
            print(f"  {t}: FAILED ({e})")


def save_forecast(con, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    df["run_ts"] = datetime.now()
    con.execute("""
        INSERT INTO forecasts
        SELECT run_ts, for_date, ticker, method, low, high, center,
               sigma_daily, vol_ratio, trend_state, params
        FROM df
    """)


def status(con) -> None:
    for table in ("hedgeye_signals", "prices", "earnings", "forecasts"):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            extra = ""
            if n and table in ("hedgeye_signals", "prices"):
                dmin, dmax, nt = con.execute(
                    f"SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM {table}").fetchone()
                extra = f"  {dmin} → {dmax}  ({nt} tickers)"
            print(f"  {table:18s} {n:>9,} rows{extra}")
        except Exception as e:
            print(f"  {table}: ERROR {e}")


def main(argv=None):
    p = argparse.ArgumentParser(description="DuckDB storage for risk-range project")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    lh = sub.add_parser("load-hedgeye")
    lh.add_argument("--csv", default=str(ROOT / "hedgeye_rr_full.csv"))
    lp = sub.add_parser("load-prices")
    lp.add_argument("tickers", nargs="+")
    lp.add_argument("--start", default="2013-01-01")
    le = sub.add_parser("load-earnings")
    le.add_argument("tickers", nargs="+")
    sub.add_parser("status")
    args = p.parse_args(argv)

    con = connect()
    if args.cmd == "init":
        print(f"Initialized {DB_PATH}")
    elif args.cmd == "load-hedgeye":
        load_hedgeye(con, Path(args.csv))
    elif args.cmd == "load-prices":
        load_prices(con, args.tickers, args.start)
    elif args.cmd == "load-earnings":
        load_earnings(con, args.tickers)
    elif args.cmd == "status":
        status(con)
    con.close()


if __name__ == "__main__":
    main()
