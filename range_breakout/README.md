# Range Scanner: tight 15m ranges on NIFTY 50 / BANKNIFTY / SENSEX (Upstox → Telegram)

An add-on for the **orb_scalper** project. It reuses that project's `.env` token, Upstox client,
Telegram sender and AWS setup, and only adds new files. It sends alerts and never places orders.

> **Default: alert-only.** Backtests over Oct 2025 to Sep 2026 (about 190 trades per variant, see
> `python range_backtest.py --compare`) found no SL/target rule that made money in both 6-month periods.
> So the live monitor only tells you when a tight range forms and when it breaks. Set
> `alert_only=False` in `RangeParams` to get the SL/target levels below as well.

## 1. The idea

Indices often sit in a tight band on the 15m chart before a sharp move. The scanner finds those bands
and alerts you the moment price breaks out either way, so you can take the scalp.

| Step | Rule (defaults in `range_scan/strategy.py` → `RangeParams`) |
|---|---|
| **Tight range** | at least **30** (up to 60) consecutive closed 15m candles whose combined high-low is among the **narrowest 20%** of all 30-candle ranges of that index over the **last 20 sessions**. Each index is compared with its own recent behaviour, so there are no fixed point values to maintain, and BANKNIFTY and SENSEX get their own scale. 30 candles is more than one session (25), so the range reaches back into the previous day(s). On startup the scanner loads ~35 days of 15m data, so the whole comparison is done before any alert. The range keeps growing while candles stay inside it. |
| **Breakout** | LTP (polled every 10s) moves **0.02%** past the range high (UP) or low (DOWN). Set `breakout_on="close"` to wait for a 15m close instead. |
| **No chasing** | if price is already more than **0.5 ATR** past the edge when seen (including an opening gap), you get a "missed" note instead of an entry |
| **SL** | **1 ATR back inside** the broken edge, and never deeper than the range **midpoint**. A real breakout shouldn't fall back into the box; if it does, the trade is wrong. Using the far side of a 30-candle range would make the stop several times too wide for a scalp. |
| **Target** | **2R** (2 × the distance from entry to SL) |
| **Timing** | no new ranges or breakouts after **15:00**; one range or scalp per index at a time; open scalps close at 15:30 |

### Alerts you'll get on Telegram
Alert-only (default): `🟡 TIGHT RANGE` (high, low, width, how tight vs recent ranges) → `🟢/🔴 BREAKOUT UP/DOWN`
(price, range) → `📊 Day summary`.
With `alert_only=False`, breakouts also carry entry/SL/target, followed by `✅ TARGET HIT` / `❌ SL HIT` / `⏹️ EOD CLOSE`,
logged to `logs/range_trades_YYYYMMDD.csv`.

## 2. Install into orb_scalper

Copy these into your `orb_scalper` folder (next to `config.py`):

```
range_scan/            run_range_live.py      range_backtest.py      public_compare.py
tests/test_range_scanner.py                   aws/crontab_range.txt
```

No new packages are needed, and none of the orb_scalper files are changed.

## 3. Run
```bash
python tests/test_range_scanner.py                 # offline checks
python range_backtest.py --days 60 -v              # backtest all three indices
python run_range_live.py                           # live; waits for the token, stops itself at 15:30
```
The token is the same one `scripts/get_access_token.py` writes into `.env`. If it is missing, the
scanner waits and reminds you on Telegram at 09:10 and 09:40, then gives up at 10:30.

### Compare with public strategies
```bash
python public_compare.py --orb-csv logs\backtest_v2_2026-04-03_2026-09-30.csv
```
Runs buy-and-hold, 200-day trend, golden cross, daily Supertrend, Donchian 20/10 and a classic 15m ORB on the
same Upstox data and costs as your range breakout and ORB scalper, as % of index value per index.

```bash
python momentum_backtest.py --years 5
```
Monthly momentum on Nifty 500 stocks (top 20 by 12-1 month return, equal weight) vs holding Nifty 50 / Nifty 500.

## 4. AWS
The scanner runs on the same EC2 server, in the same `~/orb_scalper` folder, so `push_token.ps1` already
delivers its token.
1. Add the line from `aws/crontab_range.txt` to `aws/crontab.txt`.
2. Run `aws\upload_project.ps1`, then `bash aws/setup_server.sh` on the server (it reinstalls the crontab).
   You can also run `crontab -e` on the server and paste the line with `__PROJECT__` replaced by `/home/ubuntu/orb_scalper`.
3. Log: `ssh ... "tail -n 30 orb_scalper/logs/cron_range.log"`.

## 5. Tuning
- `tight_percentile` lower (e.g. 10) → only the very tightest ranges and fewer alerts; `min_candles` higher → longer squeezes only
- `sl_atr` 1.0 → 0.5 gives tighter stops (more stop-outs); `target_r` 2.0 → 1.5 gives quicker scalps
- `span_days=False` keeps ranges inside one session (then use a smaller `min_candles`, e.g. 4-8)
- `max_range_pct` adds a hard cap on width as a % of price
- Change one parameter at a time and re-run the backtest.
