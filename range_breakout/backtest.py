"""Backtest the range breakout on Upstox historical candles or a CSV.

    python -m range_breakout.backtest --from 2026-07-01 --to 2026-09-30
    python -m range_breakout.backtest --csv nifty_15m.csv

CSV columns: timestamp,open,high,low,close[,volume] with ISO timestamps.
Exits are judged on candle high/low; if one candle touches both stop and
target, the stop is assumed to have hit first.
"""
import argparse
import csv
from datetime import date, datetime, timedelta

from .config import load_config
from .strategy import IST, Candle, Params, RangeBreakout


def load_csv(path):
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        out.append(Candle(ts, float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r.get("volume") or 0)))
    return sorted(out, key=lambda c: c.ts)


def fetch(cfg, start, end):
    from .token_store import load_token
    from .upstox_client import Upstox

    client = Upstox(load_token(cfg))
    candles, cur = [], start
    while cur <= end:  # Upstox caps minute-candle requests at about a month
        chunk_end = min(cur + timedelta(days=27), end)
        candles += client.historical_candles(cfg.signal_instrument, cfg.candle_minutes,
                                             cur.isoformat(), chunk_end.isoformat())
        cur = chunk_end + timedelta(days=1)
    return sorted({c.ts: c for c in candles}.values(), key=lambda c: c.ts)


def run(candles, params):
    strat = RangeBreakout(params)
    trades = []
    for c in candles:
        prev = strat.s
        strat.on_candle(c)
        if strat.s is not prev:  # new day
            if prev.position:
                raise RuntimeError(f"{prev.day}: position left open, square-off candle missing?")
            trades += prev.closed
    return trades + strat.s.closed


def summarize(trades):
    if not trades:
        return "No trades."
    wins = [t for t in trades if t["pnl"] > 0]
    total = sum(t["pnl"] for t in trades)
    equity = peak = dd = 0.0
    for t in trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    return (
        f"Trades {len(trades)} | win rate {len(wins) / len(trades):.0%} | "
        f"net P&L {total:+.2f} | avg {total / len(trades):+.2f} | max DD {dd:.2f} | exits {by_reason}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    args = ap.parse_args()
    cfg = load_config()
    if args.csv:
        candles = load_csv(args.csv)
    else:
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=90)
        candles = fetch(cfg, start, end)
    trades = run(candles, Params.from_config(cfg))
    for t in trades:
        print(f"{t['entry_ts'][:16]} {t['side']:5} x{t['qty']} {t['entry']:.2f} -> "
              f"{t['exit']:.2f} {t['reason']:10} {t['pnl']:+.2f}")
    print(summarize(trades))


if __name__ == "__main__":
    main()
