"""Compare well-known public strategies with yours, on the same Upstox data and the same costs.

    python public_compare.py                                    # 5 years daily + last 180 days intraday
    python public_compare.py --orb-csv logs\\backtest_v2_2026-04-03_2026-09-30.csv
    python public_compare.py --years 8 --days 180 --cost-pct 0.03

Everything is measured the same way: % return on the index value (no leverage), after
--cost-pct per side (default 0.03%, roughly futures brokerage + taxes + slippage).
Leverage scales gains and losses alike, so it doesn't change which strategy is better.

Public strategies (daily candles, long/short where noted):
  buy-and-hold            the benchmark everything else has to beat
  sma200-trend            long while the close is above its 200-day average, otherwise in cash
  golden-cross 50/200     long while the 50-day average is above the 200-day average
  supertrend-daily 10,3   long when Supertrend is green, short when red
  donchian 20/10          turtle-style: long a new 20-day high, short a new 20-day low,
                          exit on the opposite 10-day extreme
Intraday (15m candles):
  classic-orb-15m         first 15m candle's range; long/short on the first close beyond it,
                          SL at the other side, exit 15:15. One trade a day.
  your-range-breakout     range_scan rules with SL/target (alert_only ignored)
  your-orb-scalper        read from your backtest CSV if you pass --orb-csv
"""
import argparse
import csv
from dataclasses import replace
from datetime import date, time, timedelta
from urllib.parse import quote

import numpy as np
import pandas as pd

from config import load_env
from range_backtest import simulate
from range_scan.strategy import INDEX_NAMES, PARAMS as P
from run_range_live import df_to_candles
from scalper.indicators import supertrend
from scalper.universe import INDICES
from scalper.upstox_client import UpstoxClient, candles_to_df

WARMUP_DAYS = 35


# ----------------------------------------------------------------------------- data
def fetch_daily(api, key, start, end) -> pd.DataFrame:
    frames, cur_end = [], end
    while cur_end >= start:
        cur_start = max(start, cur_end - timedelta(days=364))
        path = f"/v3/historical-candle/{quote(key, safe='')}/days/1/{cur_end.isoformat()}/{cur_start.isoformat()}"
        frames.append(candles_to_df(api._get(path)))
        cur_end = cur_start - timedelta(days=1)
    frames = [f for f in frames if not f.empty]
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


# ----------------------------------------------------------------------------- metrics
def position_stats(close: pd.Series, pos: pd.Series, cost: float) -> dict:
    """pos[t] is decided at close t and held over the next day. cost is a fraction per side."""
    ret = close.pct_change().fillna(0)
    held = pos.shift(1).fillna(0)
    turnover = held.diff().abs().fillna(held.abs())
    daily = held * ret - turnover * cost
    equity = (1 + daily).cumprod()
    entries = ((held != 0) & (held != held.shift(1).fillna(0))).sum()
    # win rate per position spell
    spell = (held != held.shift(1)).cumsum()
    spells = daily[held != 0].groupby(spell[held != 0]).apply(lambda x: (1 + x).prod() - 1)
    return _summary(equity, int(entries), float((spells > 0).mean() * 100) if len(spells) else 0.0,
                    years=len(close) / 248)


def trades_stats(pcts: list, years: float) -> dict:
    """pcts: net % return of each trade, in time order."""
    if not pcts:
        return {"trades": 0}
    equity = pd.Series(np.cumprod([1 + p / 100 for p in pcts]))
    return _summary(equity, len(pcts), sum(p > 0 for p in pcts) / len(pcts) * 100, years)


def _summary(equity: pd.Series, trades: int, win: float, years: float) -> dict:
    total = equity.iloc[-1] - 1
    dd = (equity / equity.cummax() - 1).min()
    per_year = (equity.iloc[-1] ** (1 / years) - 1) if years > 0 and equity.iloc[-1] > 0 else float("nan")
    return {"trades": trades, "win%": win, "net%": total * 100, "per_year%": per_year * 100, "max_dd%": dd * 100}


# ----------------------------------------------------------------------------- daily strategies
def daily_strategies(df: pd.DataFrame, cost: float) -> dict:
    c = df["close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    _, st_dir = supertrend(df, 10, 3.0)
    hi20, lo20 = df["high"].rolling(20).max().shift(1), df["low"].rolling(20).min().shift(1)
    hi10, lo10 = df["high"].rolling(10).max().shift(1), df["low"].rolling(10).min().shift(1)
    don, cur = [], 0
    for i in range(len(df)):
        h, l = df["high"].iloc[i], df["low"].iloc[i]
        if cur == 0:
            cur = 1 if h > hi20.iloc[i] else -1 if l < lo20.iloc[i] else 0
        elif cur == 1 and l < lo10.iloc[i]:
            cur = 0
        elif cur == -1 and h > hi10.iloc[i]:
            cur = 0
        don.append(cur)
    return {
        "buy-and-hold": pd.Series(1.0, index=c.index),
        "sma200-trend": (c > sma200).astype(float).where(sma200.notna(), 0.0),
        "golden-cross 50/200": (sma50 > sma200).astype(float).where(sma200.notna(), 0.0),
        "supertrend-daily 10,3": st_dir.fillna(0.0),
        "donchian 20/10": pd.Series(don, index=c.index, dtype=float),
    }


# ----------------------------------------------------------------------------- intraday strategies
def classic_orb(df15: pd.DataFrame, cost_pct: float) -> list:
    pcts = []
    for _, day in df15.groupby(df15.index.date):
        if len(day) < 3 or day.index[0].time() != time(9, 15):
            continue
        orh, orl = day["high"].iloc[0], day["low"].iloc[0]
        pos = None
        for ts, r in day.iloc[1:].iterrows():
            end = (ts + pd.Timedelta(minutes=15)).time()
            if pos is None:
                if end > time(14, 30):
                    break
                if r.close > orh:
                    pos = (1, r.close, orl)
                elif r.close < orl:
                    pos = (-1, r.close, orh)
                continue
            sign, entry, sl = pos
            if (sign == 1 and r.low <= sl) or (sign == -1 and r.high >= sl):
                exit_ = sl
            elif end >= time(15, 30) or ts == day.index[-1]:
                exit_ = r.close
            else:
                continue
            pcts.append((exit_ - entry) * sign / entry * 100 - 2 * cost_pct)
            break
    return pcts


def your_range(name, candles, start, cost_pct) -> list:
    p = replace(P, alert_only=False)
    return [e.points / e.scalp.entry * 100 - 2 * cost_pct
            for _, e in simulate(name, candles, p, start) if e.kind in ("TARGET", "STOP", "EXPIRED")]


def your_orb(path, cost_pct) -> dict:
    out = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            sign = 1 if r["side"] == "LONG" else -1
            entry, exit_ = float(r["entry"]), float(r["exit"])
            out.setdefault(r["symbol"], []).append((exit_ - entry) * sign / entry * 100 - 2 * cost_pct)
    return out


# ----------------------------------------------------------------------------- report
def row(label, period, s):
    if not s.get("trades"):
        return f"  {label:<24}{period:<12}{'no trades':>8}"
    return (f"  {label:<24}{period:<12}{s['trades']:>7}{s['win%']:>7.0f}%{s['net%']:>+9.1f}%"
            f"{s['per_year%']:>+10.1f}%{s['max_dd%']:>+10.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5, help="history for the daily strategies")
    ap.add_argument("--days", type=int, default=180, help="history for the intraday strategies")
    ap.add_argument("--cost-pct", type=float, default=0.03, help="cost per side, %% of price")
    ap.add_argument("--orb-csv", help="your ORB scalper backtest trade list (logs/backtest_*.csv)")
    ap.add_argument("--symbols", nargs="*", default=INDEX_NAMES)
    a = ap.parse_args()

    api = UpstoxClient(load_env()["access_token"])
    to = date.today() - timedelta(days=1)
    d_from, i_from = to - timedelta(days=365 * a.years), to - timedelta(days=a.days)
    cost = a.cost_pct / 100
    orb = your_orb(a.orb_csv, a.cost_pct) if a.orb_csv else {}

    print(f"Daily {d_from} -> {to} | intraday {i_from} -> {to} | cost {a.cost_pct}% per side | "
          f"returns on index value, no leverage\n")
    head = f"  {'strategy':<24}{'period':<12}{'trades':>7}{'win':>8}{'net':>10}{'per year':>11}{'max DD':>11}"
    for name in a.symbols:
        key = INDICES[name]
        print(f"{name}\n{head}")
        daily = fetch_daily(api, key, d_from - timedelta(days=400), to)  # 200-day averages need a warm-up
        recent_cut = pd.Timestamp(i_from, tz=daily.index.tz)
        for label, pos in daily_strategies(daily, cost).items():
            full = daily.index >= pd.Timestamp(d_from, tz=daily.index.tz)
            print(row(label, f"{a.years}y", position_stats(daily["close"][full], pos[full], cost)))
            rec = daily.index >= recent_cut
            print(row("", f"last {a.days}d", position_stats(daily["close"][rec], pos[rec], cost)))
        df15 = api.historical_candles(key, 15, i_from - timedelta(days=WARMUP_DAYS), to)
        yrs = a.days / 365
        print(row("classic-orb-15m", f"last {a.days}d",
                  trades_stats(classic_orb(df15[df15.index >= recent_cut], a.cost_pct), yrs)))
        print(row("your-range-breakout", f"last {a.days}d",
                  trades_stats(your_range(name, df_to_candles(df15), i_from, a.cost_pct), yrs)))
        if name in orb:
            print(row("your-orb-scalper", "csv", trades_stats(orb[name], yrs)))
        print()


if __name__ == "__main__":
    main()
