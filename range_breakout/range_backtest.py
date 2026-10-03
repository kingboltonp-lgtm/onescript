"""Backtest the tight-range scanner on Upstox 15m history (same code as the live monitor).

Examples:
    python range_backtest.py --days 60
    python range_backtest.py --symbols "NIFTY 50" BANKNIFTY --from 2026-06-01 --to 2026-08-31 -v
    python range_backtest.py --csv nifty_15m.csv --name "NIFTY 50"
    python range_backtest.py --days 180 --compare     # rule variants side by side

CSV columns: timestamp,open,high,low,close[,volume] with ISO timestamps.
Breakouts fill at the trigger price when a candle's high/low crosses it (ltp mode)
or at the close (close mode). If one candle touches both SL and target, it counts as SL.
"""
import argparse
import csv
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from range_scan.strategy import IST, INDEX_NAMES, PARAMS as P, Candle, TightRangeScanner

WARMUP_DAYS = 35


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


PRESETS = {
    "current": {},
    "close-confirm": dict(breakout_on="close"),
    "skip-0915": dict(no_entry_before=time(9, 30)),
    "trend-ema200": dict(trend_ema=200),
    "close+0930+ema200": dict(breakout_on="close", no_entry_before=time(9, 30), trend_ema=200),
    "sl1.5atr-1.5R": dict(sl_atr=1.5, target_r=1.5),
    "all-filters+1.5atr": dict(breakout_on="close", no_entry_before=time(9, 30), trend_ema=200,
                               sl_atr=1.5, target_r=1.5),
    "tightest-10%": dict(tight_percentile=10),
    "fade-2R": dict(mode="fade"),
    "fade-1R": dict(mode="fade", target_r=1.0),
    "fade-1R+skip0915": dict(mode="fade", target_r=1.0, no_entry_before=time(9, 30)),
}


def r_multiple(e):
    risk = abs(e.scalp.entry - e.scalp.stop)
    return e.points / risk if risk else 0.0


def compare(datasets):
    names = [n for n, _, _ in datasets]
    print(f"{'variant':<20}" + "".join(f"{n:>22}" for n in names) + f"{'ALL':>22}")
    print(f"{'':<20}" + "".join(f"{'trades win%  total R':>22}" for _ in names + ['ALL']))
    for label, over in PRESETS.items():
        p = replace(P, **over)
        cells, all_r = [], []
        for name, candles, start in datasets:
            rs = [r_multiple(e) for _, e in simulate(name, candles, p, start)
                  if e.kind in ("TARGET", "STOP", "EXPIRED")]
            all_r += rs
            cells.append(rs)
        cells.append(all_r)
        row = ""
        for rs in cells:
            win = sum(r > 0 for r in rs) / len(rs) * 100 if rs else 0
            row += f"{len(rs):>9} {win:5.0f}% {sum(rs):+6.1f}"
        print(f"{label:<20}{row}")


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
            "total_R": round(sum(r_multiple(e) for e in done), 2),
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
    ap.add_argument("--compare", action="store_true", help="run the rule variants in PRESETS side by side")
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

    if a.compare:
        compare(datasets)
        return
    for name, candles, start in datasets:
        events = simulate(name, candles, P, start)
        if a.verbose:
            for ts, e in events:
                print(f"  {ts:%Y-%m-%d %H:%M}  {e.message}")
        print(f"  {name:<10} {stats(events)}")


if __name__ == "__main__":
    main()
