"""Backtest monthly momentum on Nifty 500 stocks against simply holding the index.

    python momentum_backtest.py                       # 5 years, all variants
    python momentum_backtest.py --years 8 --cost-pct 0.15

Each month-end: rank stocks by past return, buy the top N in equal weights, hold one month,
repeat. Costs (--cost-pct per side, default 0.15%: delivery STT + charges + slippage) are charged
on the part of the portfolio that changes. Daily candles are cached in .cache/daily/ so a re-run
is quick.

Caveat: it uses TODAY's Nifty 500 list, so stocks that dropped out of the index over the years
are missing (survivorship bias). That flatters momentum; treat results as an upper bound.
"""
import argparse
import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests

from config import CACHE_DIR, load_env
from public_compare import fetch_daily
from scalper.universe import load_nse_equity_keys
from scalper.upstox_client import UpstoxClient

NIFTY500_CSV = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
BENCHMARKS = {"NIFTY 500": "NSE_INDEX|Nifty 500", "NIFTY 50": "NSE_INDEX|Nifty 50"}
VARIANTS = {
    "12-1m top20": dict(lookback=12, skip=1, top=20, filter=False),
    "6m top20": dict(lookback=6, skip=0, top=20, filter=False),
    "12-1m top10": dict(lookback=12, skip=1, top=10, filter=False),
    "12-1m top20 + trend": dict(lookback=12, skip=1, top=20, filter=True),
}


def nifty500_symbols() -> list:
    r = requests.get(NIFTY500_CSV, timeout=15, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
    r.raise_for_status()
    return [row["Symbol"].strip() for row in csv.DictReader(io.StringIO(r.text)) if row.get("Symbol")]


def cached_daily(api, name, key, start, end) -> pd.Series:
    folder = CACHE_DIR / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name.replace(' ', '_').replace('&', 'and')}_{start}_{end}.csv"
    if path.exists():
        s = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
        return s
    for attempt in range(3):
        try:
            df = fetch_daily(api, key, start, end)
            break
        except Exception:
            if attempt == 2:
                return pd.Series(dtype=float)
            time.sleep(2)
    s = df["close"]
    s.index = s.index.tz_localize(None).normalize()
    s.to_frame().to_csv(path)
    return s


def month_ends(index: pd.DatetimeIndex) -> list:
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


def run_variant(px: pd.DataFrame, bench: pd.Series, v: dict, cost: float, start) -> dict:
    ends = [d for d in month_ends(px.index) if d >= pd.Timestamp(start)]
    trend = bench > bench.rolling(200).mean()
    held, rets, picks_log, turnovers = set(), [], [], []
    for t, t_next in zip(ends[:-1], ends[1:]):
        i = px.index.get_loc(t)
        a, b = i - 21 * v["lookback"], i - 21 * v["skip"]
        if a < 0:
            continue
        score = (px.iloc[b] / px.iloc[a] - 1).dropna()
        score = score[px.iloc[i].notna()]
        picks = set(score.nlargest(v["top"]).index)
        if v["filter"] and not trend.get(t, True):
            picks = set()  # index below its 200-day average: sit in cash
        n_old, n_new = max(len(held), 1), max(len(picks), 1)
        changed = len(held - picks) / n_old + len(picks - held) / n_new if (held or picks) else 0
        turnovers.append(changed / 2)
        r = (px.loc[t_next, list(picks)] / px.loc[t, list(picks)] - 1).fillna(0).mean() if picks else 0.0
        rets.append((t_next, r - changed * cost))
        held = picks
        picks_log.append((t, sorted(picks)))
    return {"rets": pd.Series(dict(rets)), "turnover": sum(turnovers) / max(len(turnovers), 1), "last": picks_log[-1:]}


def summarize(monthly: pd.Series) -> dict:
    eq = (1 + monthly).cumprod()
    years = len(monthly) / 12
    return {
        "net%": (eq.iloc[-1] - 1) * 100,
        "per_year%": (eq.iloc[-1] ** (1 / years) - 1) * 100 if years else 0,
        "max_dd%": (eq / eq.cummax() - 1).min() * 100,
        "win_months%": (monthly > 0).mean() * 100,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--cost-pct", type=float, default=0.15, help="cost per side, %% of trade value")
    ap.add_argument("--list-csv", help="Nifty 500 list saved from niftyindices.com, if NSE blocks the download")
    a = ap.parse_args()

    api = UpstoxClient(load_env()["access_token"])
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=365 * a.years)
    data_start = start - timedelta(days=400)  # 12-month lookback + 200-day average warm-up

    keys = load_nse_equity_keys()
    if a.list_csv:
        with open(a.list_csv) as fh:
            names = [row["Symbol"].strip() for row in csv.DictReader(fh) if row.get("Symbol")]
    else:
        try:
            names = nifty500_symbols()
        except Exception as e:
            raise SystemExit(f"Couldn't download the Nifty 500 list from NSE ({e}). Save "
                             f"ind_nifty500list.csv from niftyindices.com and pass --list-csv <file>.")
    syms = [s for s in names if s in keys]
    print(f"Downloading daily candles for {len(syms)} stocks (cached after the first run)...")
    with ThreadPoolExecutor(max_workers=4) as ex:
        series = dict(zip(syms, ex.map(lambda s: cached_daily(api, s, keys[s], data_start, end), syms)))
    px = pd.DataFrame({s: v for s, v in series.items() if len(v)}).sort_index()
    bench = {n: cached_daily(api, n, k, data_start, end) for n, k in BENCHMARKS.items()}
    px = px.reindex(bench["NIFTY 500"].index).ffill(limit=5)

    print(f"\nMonthly momentum {start} -> {end} | {px.shape[1]} stocks | cost {a.cost_pct}% per side\n")
    print(f"  {'strategy':<22}{'net':>10}{'per year':>11}{'max DD':>10}{'up months':>11}{'turnover/m':>12}")
    ends = [d for d in month_ends(px.index) if d >= pd.Timestamp(start)]
    for name, s in bench.items():
        m = s.reindex(ends).pct_change().dropna()
        r = summarize(m)
        print(f"  {name + ' hold':<22}{r['net%']:>+9.1f}%{r['per_year%']:>+10.1f}%{r['max_dd%']:>+9.1f}%"
              f"{r['win_months%']:>10.0f}%{'-':>12}")
    last = None
    for label, v in VARIANTS.items():
        out = run_variant(px, bench["NIFTY 500"], v, a.cost_pct / 100, start)
        r = summarize(out["rets"])
        print(f"  {label:<22}{r['net%']:>+9.1f}%{r['per_year%']:>+10.1f}%{r['max_dd%']:>+9.1f}%"
              f"{r['win_months%']:>10.0f}%{out['turnover'] * 100:>11.0f}%")
        if label == "12-1m top20":
            last = out["last"]
    if last:
        t, picks = last[0]
        print(f"\nPicks held into the latest month ({t:%d %b %Y}, 12-1m top20): {', '.join(picks)}")
    print("\nNote: today's Nifty 500 list is used for every year (survivorship bias), so momentum looks "
          "somewhat better here than it would have in real time.")


if __name__ == "__main__":
    main()
