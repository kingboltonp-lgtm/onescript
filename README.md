# onescript

Local trading-bot skeleton with Telegram alerts, an hourly heartbeat, and automatic ATM/ITM/OTM strike selection. Upstox is stubbed until you add a live session.

Run every command **inside the cloned `onescript` folder**. `bot.py` and `.env.example` live there, not in `C:\Users\DELL`.

## Windows (PowerShell)

```powershell
# 1. Clone the repo, then enter it
cd $HOME
git clone https://github.com/kingboltonp-lgtm/onescript.git
cd onescript
git checkout cursor/trading-bot-system-be63

# 2. Confirm you are in the project (must list bot.py and .env.example)
Get-ChildItem

# 3. Optional: isolated Python env
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 4. Install THIS project's packages (requests, python-dotenv, pytest — not Django)
python -m pip install -r requirements.txt

# 5. Create your local secrets file
Copy-Item .env.example .env
notepad .env

# 6. Start the bot
python bot.py
```

If PowerShell blocks the venv script, run this once as Administrator, then retry `Activate.ps1`:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Stop the bot with `Ctrl+C`.

## macOS / Linux

```bash
git clone https://github.com/kingboltonp-lgtm/onescript.git
cd onescript
git checkout cursor/trading-bot-system-be63
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Edit `.env` with your Telegram bot token, chat id, and Upstox credentials. Do not commit `.env`.

Placeholder text such as `YOUR_TELEGRAM_BOT_TOKEN` is **not** a token. Leave those strings in place and Telegram will return `404 Not Found`.

### Fill Telegram values

1. In Telegram, open [@BotFather](https://t.me/BotFather) → `/newbot` (or `/token` for an existing bot) and copy the token. It looks like `123456789:AAH...`, not `YOUR_TELEGRAM_BOT_TOKEN`.
2. Start a chat with **your** bot and send any message.
3. In a browser, open  
   `https://api.telegram.org/bot<YOUR_REAL_TOKEN>/getUpdates`  
   and find `"chat":{"id": ...}`. That number is `TELEGRAM_CHAT_ID`.
4. Save `.env` like this (no quotes, no spaces around `=`):

```
TELEGRAM_TOKEN=123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

5. Stop the bot (`Ctrl+C`) and run `python bot.py` again.

To pick up README/code fixes from this branch:

```powershell
cd $HOME\onescript
git pull
git checkout cursor/trading-bot-system-be63
```

## What you should see

On start the bot sends a boot message to Telegram (if tokens are set), starts a background heartbeat (default every 3600 seconds), and loops with a mock Nifty spot while it auto-selects a 1-strike ITM call.

If Telegram env vars are empty, it logs a warning and keeps running.

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
python -m pytest
```

## Notes

- Heartbeat and alerts are skipped (with a warning) if Telegram env vars are empty.
- Missing Upstox credentials logs a warning and continues in mock mode so you can develop offline.
- This is a starter, not a production order-management system. Wire live quotes and order placement only after you have tested with paper/mock data.
