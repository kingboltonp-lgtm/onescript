"""Backtest the tight-range scanner on Upstox 15m history (same code as the live monitor).

Examples:
    python range_backtest.py --days 60
    python range_backtest.py --symbols "NIFTY 50" BANKNIFTY --from 2026-06-01 --to 2026-08-31 -v
    python range_backtest.py --csv nifty_15m.csv --name "NIFTY 50"

CSV columns: timestamp,open,high,low,close[,volume] with ISO timestamps.
Breakouts fill at the trigger price when a candle's high/low crosses it (ltp mode)
or at the close (close mode). If one candle touches both SL and target, it counts as SL.
"""
import argparse
import csv
from collections import Counter
from datetime import date, datetime, timedelta

from range_scan.strategy import IST, INDEX_NAMES, PARAMS as P, Candle, TightRangeScanner

WARMUP_DAYS = 10


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


def simulate(name, candles, p, start=None):
    """Returns [(candle_ts, Event)]. Candles before `start` only warm up the ATR."""
    sc = TightRangeScanner(name, p)
    if start:
        sc.warmup([c for c in candles if c.ts.date() < start])
        candles = [c for c in candles if c.ts.date() >= start]
    events = []
    for c in candles:
        events += [(c.ts, e) for e in sc.on_candle(c)]
    if sc.scalp and candles:
        events.append((candles[-1].ts, sc._close_scalp("EXPIRED", candles[-1].close)))
    return events


def stats(events) -> dict:
    events = [e for _, e in events]
    kinds = Counter(e.kind for e in events)
    done = [e for e in events if e.kind in ("TARGET", "STOP", "EXPIRED")]
    out = {"ranges": kinds["RANGE"], "breakouts": kinds["BREAKOUT"], "target": kinds["TARGET"],
           "sl": kinds["STOP"], "eod": kinds["EXPIRED"]}
    if done:
        pts = [e.points for e in done]
        gross_win, gross_loss = sum(x for x in pts if x > 0), -sum(x for x in pts if x < 0)
        out.update({
            "win_rate_%": round(sum(x > 0 for x in pts) / len(pts) * 100, 1),
            "total_pts": round(sum(pts), 2), "avg_pts": round(sum(pts) / len(pts), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", help=f"default: {' '.join(INDEX_NAMES)}")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--csv", help="offline: one index's candles from a CSV")
    ap.add_argument("--name", default="CSV")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every alert")
    a = ap.parse_args()

    if a.csv:
        datasets = [(a.name, load_csv(a.csv), None)]
    else:
        from config import load_env
        from scalper.universe import INDICES
        from scalper.upstox_client import UpstoxClient
        from run_range_live import df_to_candles

        to = date.fromisoformat(a.to) if a.to else date.today() - timedelta(days=1)
        frm = date.fromisoformat(a.frm) if a.frm else to - timedelta(days=a.days)
        api = UpstoxClient(load_env()["access_token"])
        datasets = []
        for name in a.symbols or INDEX_NAMES:
            df = api.historical_candles(INDICES[name], P.candle_minutes, frm - timedelta(days=WARMUP_DAYS), to)
            datasets.append((name, df_to_candles(df), frm))
        print(f"Range backtest {frm} -> {to} | {P.candle_minutes}m candles\n")

    for name, candles, start in datasets:
        events = simulate(name, candles, P, start)
        if a.verbose:
            for ts, e in events:
                print(f"  {ts:%Y-%m-%d %H:%M}  {e.message}")
        print(f"  {name:<10} {stats(events)}")


if __name__ == "__main__":
    main()
