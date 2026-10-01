"""Telegram alerts via the Bot API. Failures are logged, never raised."""
import logging

import requests

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, text):
        log.info("ALERT: %s", text)
        if not (self.bot_token and self.chat_id):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            ).raise_for_status()
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", exc)
