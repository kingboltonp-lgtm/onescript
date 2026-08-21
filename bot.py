"""Trading bot: Telegram alerts, hourly heartbeat, and ATM/ITM/OTM strike selection.

Secrets come from environment variables (see .env.example). Replace the
Upstox initializer with a live session when you are ready to trade.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))
LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)

_shutdown = threading.Event()


def send_telegram_alert(message: str) -> bool:
    """Send a Markdown message to the configured Telegram chat."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram is not configured; skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        logging.error("Failed to send Telegram alert: %s", exc)
        return False


def heartbeat_monitor(interval_seconds: int = 3600) -> None:
    """Background thread: send a life-signal on a fixed interval until shutdown."""
    while not _shutdown.is_set():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"🟢 *Bot Heartbeat* \nStatus: Alive & Monitoring\nTime: {now}"
        send_telegram_alert(msg)
        logging.info("Heartbeat sent to Telegram.")
        if _shutdown.wait(interval_seconds):
            break


def auto_select_strike(
    spot_price: float,
    option_type: str,
    step: int = 50,
    depth: int = 0,
) -> int:
    """Pick an option strike from live spot.

    - spot_price: live index/spot (e.g. Nifty)
    - option_type: "CE" or "PE"
    - step: strike interval (50 for Nifty, 100 for BankNifty)
    - depth: 0 = ATM, positive = that many strikes ITM, negative = OTM
    """
    if step <= 0:
        raise ValueError("step must be a positive integer.")

    atm_strike = int(round(spot_price / step)) * step

    option_type = option_type.upper()
    if option_type == "CE":
        target_strike = atm_strike - (depth * step)
    elif option_type == "PE":
        target_strike = atm_strike + (depth * step)
    else:
        raise ValueError("Invalid option_type. Use 'CE' or 'PE'.")

    return int(target_strike)


def initialize_upstox() -> bool:
    """Validate credentials and log a connection placeholder.

    Plug in upstox_client (or REST login) here when you have a live access token.
    """
    logging.info("Initializing Upstox connection...")
    if not UPSTOX_API_KEY or not UPSTOX_ACCESS_TOKEN:
        logging.warning(
            "UPSTOX_API_KEY / UPSTOX_ACCESS_TOKEN are not set. "
            "Running in paper/mock mode."
        )
        return True

    logging.info("Upstox credentials present. Ready for API wiring.")
    return True


def main() -> None:
    logging.info("Starting Trading Bot System...")
    send_telegram_alert(
        "🚀 *System Boot* \nTrading Bot has successfully started on the local PC."
    )

    heartbeat_thread = threading.Thread(
        target=heartbeat_monitor,
        args=(HEARTBEAT_INTERVAL_SECONDS,),
        daemon=True,
        name="heartbeat",
    )
    heartbeat_thread.start()

    is_connected = initialize_upstox()
    if not is_connected:
        send_telegram_alert("🔴 *Error*: Failed to connect to Upstox API.")
        return

    try:
        while not _shutdown.is_set():
            # Replace this mock with a live Nifty spot quote from Upstox.
            mock_live_nifty_spot = 22432.45
            ce_strike = auto_select_strike(
                mock_live_nifty_spot, "CE", step=50, depth=1
            )
            logging.debug(
                "Spot: %s | Auto-selected CE strike: %s",
                mock_live_nifty_spot,
                ce_strike,
            )
            time.sleep(LOOP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Manual shutdown initiated.")
        send_telegram_alert("🛑 *System Shutdown* \nBot stopped manually by user.")
    except Exception as exc:
        error_msg = f"⚠️ *Fatal Error* \nScript crashed: {exc}"
        logging.error(error_msg)
        send_telegram_alert(error_msg)
    finally:
        _shutdown.set()


if __name__ == "__main__":
    main()
