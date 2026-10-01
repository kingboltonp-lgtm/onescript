"""Backtest the tight-range breakout scanner per index.

    python -m range_breakout.backtest --from 2026-07-01 --to 2026-09-30
    python -m range_breakout.backtest --csv nifty_15m.csv --name "Nifty 50"

CSV columns: timestamp,open,high,low,close[,volume] with ISO timestamps.
Breakouts are taken at the trigger price when a candle's high/low crosses it
(ltp mode) or at the close (close mode). If one candle touches both stop and
target, the stop is assumed to have hit first.
"""
import argparse
import csv
from collections import Counter
from datetime import date, datetime, timedelta

from .config import load_config, short_name
from .strategy import IST, Candle, Params, TightRangeScanner


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


def fetch(client, key, minutes, start, end):
    candles, cur = [], start
    while cur <= end:  # Upstox caps minute-candle requests at about a month
        chunk_end = min(cur + timedelta(days=27), end)
        candles += client.historical_candles(key, minutes, cur.isoformat(), chunk_end.isoformat())
        cur = chunk_end + timedelta(days=1)
    return sorted({c.ts: c for c in candles}.values(), key=lambda c: c.ts)


def run(name, candles, params):
    sc = TightRangeScanner(name, params)
    events = []
    for c in candles:
        events += [(c.ts, e) for e in sc.on_candle(c)]
    if sc.scalp:
        events.append((candles[-1].ts, sc._close_scalp("EXPIRED", candles[-1].close)))
    return events


def summarize(name, events):
    events = [e for _, e in events]
    kinds = Counter(e.kind for e in events)
    done = [e for e in events if e.kind in ("TARGET", "STOP", "EXPIRED")]
    if not done:
        return f"{name}: {kinds['RANGE']} ranges, no breakouts"
    wins = sum(e.points > 0 for e in done)
    pts = sum(e.points for e in done)
    return (
        f"{name}: {kinds['RANGE']} ranges, {kinds['BREAKOUT']} breakouts | "
        f"target {kinds['TARGET']}, stop {kinds['STOP']}, EOD {kinds['EXPIRED']} | "
        f"win {wins / len(done):.0%} | net {pts:+.1f} pts, avg {pts / len(done):+.1f}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--name", default="CSV")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every event")
    args = ap.parse_args()
    cfg = load_config()
    params = Params.from_config(cfg)

    if args.csv:
        datasets = [(args.name, load_csv(args.csv))]
    else:
        from .token_store import load_token
        from .upstox_client import Upstox

        client = Upstox(load_token(cfg))
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=90)
        datasets = [(short_name(k), fetch(client, k, cfg.candle_minutes, start, end)) for k in cfg.indices]

    for name, candles in datasets:
        events = run(name, candles, params)
        if args.verbose:
            for ts, e in events:
                print(f"{ts:%Y-%m-%d %H:%M}  {e.message}")
        print(summarize(name, events))


if __name__ == "__main__":
    main()
