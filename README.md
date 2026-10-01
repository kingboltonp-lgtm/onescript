# onescript

## Tight range breakout scanner (`range_breakout/`)

Watches **Nifty 50, Bank Nifty and Sensex** on 15-minute candles for tight
ranges and sends a Telegram alert the moment price breaks out up or down,
with a scalp stop and target. Uses Upstox for data, AWS SSM for the daily
access token, and a systemd timer on EC2. **Alerts only: it never places orders.**

### What it alerts

```
🟡 Nifty 50 tight range 24,950.00 - 25,010.50 (60.5 pts, 0.24%) over 5x15m 10:15-11:30 | ATR 45.2
🚀 Nifty 50 BREAKOUT UP @ 25,015.60 | range 24,950.00 - 25,010.50 | SL 24,950.00 | TGT 25,076.10
✅ Nifty 50 UP scalp target hit @ 25,076.40 (+60.8 pts)
```

### Rules (defaults, all in `.env`)

| | |
|---|---|
| Tight range | at least 4 (up to 12) consecutive 15m candles, today only, whose total high-low fits inside 1.5 × ATR(14) of the candles before it. It keeps growing while candles stay inside. |
| Breakout | LTP moves 0.02% beyond the range high (up) or low (down). Set `BREAKOUT_ON=close` to wait for a 15m close instead. |
| Scalp plan | stop = other side of the range, target = 1 × range width from entry |
| Timing | no new ranges or breakouts after 15:00; one active range or scalp per index at a time |

Tighten or loosen detection with `RANGE_ATR_MULT` (lower = tighter) and
`MIN_RANGE_CANDLES`; `MAX_RANGE_PCT` adds a hard width cap.

### Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # Upstox app keys, Telegram bot token + chat id
```

Each morning (Upstox tokens expire ~03:30 IST):

```bash
python -m range_breakout.get_token    # log in, paste redirect URL -> token saved to SSM
```

The scanner alerts on Telegram and waits if the token is missing or expired.

### Run

```bash
python -m range_breakout.bot                                       # one trading day
python -m range_breakout.backtest --from 2026-07-01 --to 2026-09-30 -v
python -m range_breakout.backtest --csv nifty_15m.csv --name "Nifty 50"
python -m unittest discover -s range_breakout/tests -t .
```

On start it loads the last week of 15m candles for ATR. If restarted mid-day
it replays today's candles silently, so it doesn't re-alert old breakouts.

### AWS (EC2)

1. Attach an instance role with `deploy/iam-policy.json` (SSM token read/write).
2. Copy the repo to `/opt/onescript`, then run `deploy/setup_ec2.sh`.
3. The timer starts the scanner at 09:05 IST Mon–Fri; it exits after 15:30.
   Logs: `journalctl -u range-breakout -f`.
