# onescript

Local trading-bot skeleton with Telegram alerts, an hourly heartbeat, and automatic ATM/ITM/OTM strike selection. Upstox is stubbed until you add a live session.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your Telegram bot token, chat id, and Upstox credentials. Do not commit `.env`.

## Run

```bash
python bot.py
```

On start the bot sends a boot message to Telegram, starts a background heartbeat (default every 3600 seconds), and loops with a mock Nifty spot while it auto-selects a 1-strike ITM call.

Stop with `Ctrl+C` (sends a shutdown alert).

## Strike helper

`auto_select_strike(spot, option_type, step=50, depth=0)`

| Argument | Meaning |
| --- | --- |
| `spot` | Live index price |
| `option_type` | `CE` or `PE` |
| `step` | 50 for Nifty, 100 for BankNifty |
| `depth` | `0` ATM, positive ITM, negative OTM |

Example: Nifty 22432.45, `CE`, step 50, depth 1 → strike **22400**.

## Tests

```bash
pytest
```

## Notes

- Heartbeat and alerts are skipped (with a warning) if Telegram env vars are empty.
- Missing Upstox credentials logs a warning and continues in mock mode so you can develop offline.
- This is a starter, not a production order-management system. Wire live quotes and order placement only after you have tested with paper/mock data.
