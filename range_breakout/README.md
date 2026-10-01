# Range Scanner: tight 15m ranges on NIFTY 50 / BANKNIFTY / SENSEX (Upstox → Telegram)

An add-on for the **orb_scalper** project. It reuses that project's `.env` token, Upstox client,
Telegram sender and AWS setup, and only adds new files. It sends alerts and never places orders.

## 1. The idea

Indices often sit in a tight band on the 15m chart before a sharp move. The scanner finds those bands
and alerts you the moment price breaks out either way, so you can take the scalp.

| Step | Rule (defaults in `range_scan/strategy.py` → `RangeParams`) |
|---|---|
| **Tight range** | at least **30** (up to 60) consecutive closed 15m candles whose combined high-low fits inside **4 × ATR(14)** of the candles before them. 30 candles is more than one session (25), so the range reaches back into the previous day(s); on startup the scanner loads the last 10 days so the full window is checked before any alert. The range keeps growing while candles stay inside it. |
| **Breakout** | LTP (polled every 10s) moves **0.02%** past the range high (UP) or low (DOWN). Set `breakout_on="close"` to wait for a 15m close instead. |
| **No chasing** | if price is already more than **0.5 ATR** past the edge when seen (including an opening gap), you get a "missed" note instead of an entry |
| **Scalp levels** | SL = other side of the range, target = **1 × range width** from entry |
| **Timing** | no new ranges or breakouts after **15:00**; one range or scalp per index at a time; open scalps close at 15:30 |

### Alerts you'll get on Telegram
`🟡 TIGHT RANGE` (high, low, width, ATR) → `🟢/🔴 BREAKOUT UP/DOWN` (entry, SL, target) →
`✅ TARGET HIT` / `❌ SL HIT` / `⏹️ EOD CLOSE` → `📊 Day summary`. Results go to `logs/range_trades_YYYYMMDD.csv`.

## 2. Install into orb_scalper

Copy these into your `orb_scalper` folder (next to `config.py`):

```
range_scan/            run_range_live.py      range_backtest.py
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

## 4. AWS
The scanner runs on the same EC2 server, in the same `~/orb_scalper` folder, so `push_token.ps1` already
delivers its token.
1. Add the line from `aws/crontab_range.txt` to `aws/crontab.txt`.
2. Run `aws\upload_project.ps1`, then `bash aws/setup_server.sh` on the server (it reinstalls the crontab).
   You can also run `crontab -e` on the server and paste the line with `__PROJECT__` replaced by `/home/ubuntu/orb_scalper`.
3. Log: `ssh ... "tail -n 30 orb_scalper/logs/cron_range.log"`.

## 5. Tuning
- `range_atr_mult` lower → tighter ranges and fewer alerts; `min_candles` higher → longer squeezes only
- `span_days=False` keeps ranges inside one session (then use a smaller `min_candles`, e.g. 4-8)
- `max_range_pct` adds a hard cap on width as a % of price
- `target_mult` 1.0 → 1.5 lets winners run further
- Change one parameter at a time and re-run the backtest.
