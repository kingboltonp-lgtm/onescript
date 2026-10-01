# onescript

## 15-minute range breakout bot (`range_breakout/`)

Opening range breakout for Upstox, with the access token kept in AWS SSM,
Telegram alerts, and a systemd timer for EC2. **Paper mode by default**:
nothing is sent to the exchange until `LIVE_TRADING=true`.

### Rules (defaults)

| | |
|---|---|
| Opening range | high/low of the first 15m candle (09:15–09:30) |
| Entry | a 15m candle **closes** above the range high (long) or below the low (short); fill at that close |
| Stop | opposite side of the range |
| Target | `REWARD_RISK` × risk (default 2R) |
| Exits | stop/target checked on every LTP poll (~20s) and every candle; square-off at 15:15 |
| Limits | 1 trade/day, no new entries after 14:30 |
| Size | whole lots whose loss at the stop fits `RISK_PER_TRADE`, capped at `MAX_LOTS`; skipped if 1 lot doesn't fit |

Signals come from `SIGNAL_INSTRUMENT` candles (default Nifty 50 index) and
orders go to `TRADE_INSTRUMENT` (e.g. the current Nifty future). Sizing uses
signal-instrument points, so it matches a future, not an option premium.

### Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in Upstox app keys, Telegram, instruments
```

Each morning (Upstox tokens expire ~03:30 IST):

```bash
python -m range_breakout.get_token    # log in, paste redirect URL -> token saved to SSM
```

The bot alerts on Telegram and waits if the token is missing or expired.

### Run

```bash
python -m range_breakout.bot                                   # one trading day
python -m range_breakout.backtest --from 2026-07-01 --to 2026-09-30
python -m range_breakout.backtest --csv candles.csv            # offline
python -m unittest discover -s range_breakout/tests -t .
```

The bot saves its state to `state/<date>.json`, so a restart mid-day resumes
the open position. If it starts late with no saved state, breakouts that
already happened are reported as missed, not chased.

### AWS (EC2)

1. Attach an instance role with `deploy/iam-policy.json` (SSM token read/write).
2. Copy the repo to `/opt/onescript`, then run `deploy/setup_ec2.sh`.
3. The timer starts the bot at 09:05 IST Mon–Fri; it exits after 15:30.
   Logs: `journalctl -u range-breakout -f`.
