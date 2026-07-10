#!/usr/bin/env python3
"""
Volume + Velocity stock swing strategy (long-only, no derivatives, no leverage).

Thesis: passive/momentum/algo flows dominate active management, so
  - a large DOWN move on THIN volume has no real flow behind it -> fade it (buy the dip)
  - a large UP move on HEAVY volume is real flow -> follow it (buy the breakout)
Large/small is measured against the same risk-range machinery as the rest of this
repo: a move of k sigma, where sigma is trailing realized vol (default 60d, the
lookback the extension analysis showed is best).

Signals are computed at the close of day t; entry is at the OPEN of day t+1;
exit is at the close k trading days after entry (fixed time stop, no intraday
stop assumptions — daily bars only). One position per ticker at a time.

Strategies compared (all long-only):
  fade_all   : buy every >=trigger-sigma DOWN day                  (baseline)
  fade_vv    : buy DOWN day only if volume is QUIET (< quiet_max)  (thesis)
  brk_all    : buy every >=trigger-sigma UP day                    (baseline)
  brk_vv     : buy UP day only if volume is HEAVY (>= heavy_min)   (thesis)
  combo_vv   : fade_vv + brk_vv together                           (the algo)
Benchmark: equal-weight buy & hold of the same 9 tickers.

Also runs the same rules over the firm risk-range CSV window using the firm's
low/high as the trigger levels instead of k-sigma bands (--firm-signals).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tabulate import tabulate

ROOT = Path(__file__).resolve().parent
TICKERS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "ORCL", "TSLA"]

TRADING_DAYS = 252


def download(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    print(f"Downloading OHLCV from {start} for {len(tickers)} tickers")
    raw = yf.download(tickers=tickers, start=start, group_by="ticker",
                      auto_adjust=True, threads=True, progress=False)
    out = {}
    for t in tickers:
        h = raw[t].dropna(how="all").copy()
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_localize(None)
        h.index = pd.to_datetime(h.index).normalize()
        out[t] = h
        print(f"  {t}: {len(h)} bars {h.index.min().date()} → {h.index.max().date()}")
    return out


def features(h: pd.DataFrame, sigma_lookback: int, vol_lookback: int) -> pd.DataFrame:
    df = h.copy()
    df["ret"] = df["Close"].pct_change()
    # sigma known as of the PRIOR close (no lookahead)
    df["sigma"] = df["ret"].rolling(sigma_lookback).std().shift(1)
    df["std_move"] = df["ret"] / df["sigma"]
    # volume vs its trailing average up to the prior day
    df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(vol_lookback).mean().shift(1)
    return df


def gen_signals(df: pd.DataFrame, trigger: float, quiet_max: float, heavy_min: float) -> pd.DataFrame:
    down = df["std_move"] <= -trigger
    up = df["std_move"] >= trigger
    out = pd.DataFrame(index=df.index)
    out["fade_all"] = down
    out["fade_vv"] = down & (df["vol_ratio"] < quiet_max)
    out["brk_all"] = up
    out["brk_vv"] = up & (df["vol_ratio"] >= heavy_min)
    out["combo_vv"] = out["fade_vv"] | out["brk_vv"]
    return out


def run_trades(h: pd.DataFrame, signal: pd.Series, hold: int, cost_bps: float) -> pd.DataFrame:
    """Signal at close t -> buy open t+1 -> sell close t+1+hold-1 (hold sessions in market)."""
    idx = h.index
    trades = []
    in_pos_until = -1
    sig_days = np.flatnonzero(signal.reindex(idx).fillna(False).to_numpy())
    for i in sig_days:
        if i + 1 >= len(idx) or i + hold >= len(idx):
            continue
        if i + 1 <= in_pos_until:
            continue  # one position per ticker
        entry_i, exit_i = i + 1, i + hold
        buy = float(h["Open"].iloc[entry_i])
        sell = float(h["Close"].iloc[exit_i])
        cost = 2 * cost_bps / 1e4
        trades.append({
            "signal_date": idx[i], "entry_date": idx[entry_i], "exit_date": idx[exit_i],
            "buy": buy, "sell": sell, "ret": sell / buy - 1 - cost,
            "days_held": exit_i - entry_i + 1,
        })
        in_pos_until = exit_i
    return pd.DataFrame(trades)


def sleeve_curve(h: pd.DataFrame, trades: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Daily return series for one ticker sleeve: in the stock while a trade is on, else cash."""
    ret = pd.Series(0.0, index=index)
    if trades.empty:
        return ret
    close_ret = h["Close"].pct_change().reindex(index).fillna(0.0)
    open_to_close = (h["Close"] / h["Open"] - 1).reindex(index).fillna(0.0)
    for _, tr in trades.iterrows():
        days = index[(index >= tr["entry_date"]) & (index <= tr["exit_date"])]
        if len(days) == 0:
            continue
        ret.loc[days[0]] = open_to_close.loc[days[0]]  # entry day: open -> close
        for d in days[1:]:
            ret.loc[d] = close_ret.loc[d]
    return ret


def perf(curve: pd.Series) -> dict:
    curve = curve.dropna()
    eq = (1 + curve).cumprod()
    n = len(curve)
    if n == 0 or eq.iloc[-1] <= 0:
        return {}
    years = n / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1
    vol = curve.std() * np.sqrt(TRADING_DAYS)
    sharpe = (curve.mean() * TRADING_DAYS) / vol if vol > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    return {"total": eq.iloc[-1] - 1, "cagr": cagr, "vol": vol, "sharpe": sharpe, "max_dd": dd}


def trade_stats(all_trades: pd.DataFrame) -> dict:
    if all_trades.empty:
        return {"n": 0}
    r = all_trades["ret"]
    return {"n": len(r), "win": (r > 0).mean(), "avg": r.mean(),
            "median": r.median(), "best": r.max(), "worst": r.min()}


def evaluate(hist: dict[str, pd.DataFrame], sigs: dict[str, pd.DataFrame], strategies: list[str],
             hold: int, cost_bps: float, index: pd.DatetimeIndex, label: str) -> pd.DataFrame:
    print(f"\n{'=' * 100}")
    print(f"  {label}  |  hold={hold} sessions, cost={cost_bps:.0f}bps/side, long-only, 1 position/ticker")
    print(f"{'=' * 100}\n")
    rows, all_trades_by_strat = [], {}
    for strat in strategies:
        curves, trades_list = [], []
        for t, h in hist.items():
            tr = run_trades(h, sigs[t][strat], hold, cost_bps)
            if not tr.empty:
                tr["ticker"] = t
                trades_list.append(tr)
            curves.append(sleeve_curve(h, tr, index))
        port = pd.concat(curves, axis=1).mean(axis=1)  # equal-weight sleeves
        trades = pd.concat(trades_list, ignore_index=True) if trades_list else pd.DataFrame()
        all_trades_by_strat[strat] = trades
        ts, pf = trade_stats(trades), perf(port)
        rows.append([strat, ts.get("n", 0),
                     f"{ts.get('win', np.nan):.1%}" if ts.get("n") else "—",
                     f"{ts.get('avg', np.nan):+.2%}" if ts.get("n") else "—",
                     f"{pf.get('total', np.nan):+.1%}", f"{pf.get('cagr', np.nan):+.1%}",
                     f"{pf.get('sharpe', np.nan):.2f}", f"{pf.get('max_dd', np.nan):.1%}"])
    # benchmark: equal-weight buy & hold
    bh = pd.concat([h["Close"].pct_change().reindex(index) for h in hist.values()], axis=1).mean(axis=1).fillna(0)
    pf = perf(bh)
    rows.append(["buy_hold_eqw", "—", "—", "—", f"{pf['total']:+.1%}", f"{pf['cagr']:+.1%}",
                 f"{pf['sharpe']:.2f}", f"{pf['max_dd']:.1%}"])
    print(tabulate(rows, headers=["Strategy", "Trades", "Win%", "Avg/trade", "Total ret",
                                  "CAGR", "Sharpe", "MaxDD"], tablefmt="simple"))
    out = []
    for strat, tr in all_trades_by_strat.items():
        if not tr.empty:
            tr = tr.copy()
            tr["strategy"] = strat
            out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def load_firm(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Date": "date", "Ticker": "ticker", "Low": "firm_low",
                            "High": "firm_high", "Prev. Close": "prev_close"})
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for c in ("firm_low", "firm_high", "prev_close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna()


def firm_signals(h: pd.DataFrame, firm: pd.DataFrame, ticker: str,
                 quiet_max: float, heavy_min: float) -> pd.DataFrame:
    """Trigger = close outside the FIRM's published band for that day (instead of k-sigma)."""
    f = firm[firm["ticker"] == ticker].set_index("date")
    df = h.copy()
    df["firm_low"] = f["firm_low"].reindex(df.index)
    df["firm_high"] = f["firm_high"].reindex(df.index)
    down = df["Close"] < df["firm_low"]
    up = df["Close"] > df["firm_high"]
    out = pd.DataFrame(index=df.index)
    out["fade_all"] = down
    out["fade_vv"] = down & (df["vol_ratio"] < quiet_max)
    out["brk_all"] = up
    out["brk_vv"] = up & (df["vol_ratio"] >= heavy_min)
    out["combo_vv"] = out["fade_vv"] | out["brk_vv"]
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Volume+velocity long-only swing strategy backtest")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--sigma-lookback", type=int, default=60)
    p.add_argument("--vol-lookback", type=int, default=20)
    p.add_argument("--trigger", type=float, default=2.0, help="k-sigma daily move that triggers a signal")
    p.add_argument("--quiet-max", type=float, default=1.1, help="vol_ratio below this = quiet volume")
    p.add_argument("--heavy-min", type=float, default=1.5, help="vol_ratio at/above this = heavy volume")
    p.add_argument("--hold", type=int, default=5, help="sessions in market per trade")
    p.add_argument("--cost-bps", type=float, default=2.0, help="one-way cost in basis points")
    p.add_argument("--firm-signals", default=str(ROOT / "risk_range_signals.csv"))
    p.add_argument("--save-trades", default=str(ROOT / "vv_trades.csv"))
    p.add_argument("--hold-grid", action="store_true", help="also print hold=1/3/5/10 grid for combo_vv")
    args = p.parse_args(argv)

    strategies = ["fade_all", "fade_vv", "brk_all", "brk_vv", "combo_vv"]
    hist = download(TICKERS, args.start)
    index = sorted(set().union(*[h.index for h in hist.values()]))
    index = pd.DatetimeIndex(index)

    feats = {t: features(h, args.sigma_lookback, args.vol_lookback) for t, h in hist.items()}
    sigs = {t: gen_signals(f, args.trigger, args.quiet_max, args.heavy_min) for t, f in feats.items()}

    print(f"\nParams: trigger={args.trigger}σ  quiet<{args.quiet_max}×  heavy≥{args.heavy_min}×  "
          f"sigma_lb={args.sigma_lookback}d  vol_lb={args.vol_lookback}d")

    trades = evaluate(hist, sigs, strategies, args.hold, args.cost_bps, index,
                      f"A. LONG HISTORY (statistical {args.trigger}σ bands) {args.start} → today")

    if args.hold_grid:
        print(f"\n{'-' * 100}\n  Hold-period grid (combo_vv)\n{'-' * 100}")
        for hd in (1, 3, 5, 10):
            evaluate(hist, sigs, ["combo_vv"], hd, args.cost_bps, index, f"combo_vv hold={hd}")

    # B. Firm-band window
    firm = load_firm(Path(args.firm_signals))
    fstart, fend = firm["date"].min(), firm["date"].max()
    fidx = index[(index >= fstart) & (index <= fend + pd.Timedelta(days=10))]
    fhist = {t: h[h.index.isin(fidx)] for t, h in hist.items()}
    fsigs_firm = {t: firm_signals(feats[t], firm, t, args.quiet_max, args.heavy_min).reindex(fidx).fillna(False)
                  for t in TICKERS}
    fsigs_stat = {t: sigs[t].reindex(fidx).fillna(False) for t in TICKERS}
    evaluate(fhist, fsigs_firm, strategies, args.hold, args.cost_bps, fidx,
             f"B. FIRM WINDOW {fstart.date()} → {fend.date()} — triggers from FIRM bands")
    evaluate(fhist, fsigs_stat, strategies, args.hold, args.cost_bps, fidx,
             f"C. FIRM WINDOW same dates — triggers from statistical {args.trigger}σ bands")

    if not trades.empty:
        trades.to_csv(args.save_trades, index=False, float_format="%.6f")
        print(f"\nSaved long-history trades: {args.save_trades} ({len(trades)} rows)")


if __name__ == "__main__":
    main()
