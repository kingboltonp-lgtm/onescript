"""Daily Upstox login: prints the login URL, takes the redirect, stores the token.

    python -m range_breakout.get_token

Open the printed URL, log in, then paste the full URL you were redirected to
(or just the `code` value). The token is saved to SSM / file per TOKEN_SOURCE.
"""
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .telegram import Telegram
from .token_store import save_token
from .upstox_client import Upstox, exchange_code, login_url


def main():
    cfg = load_config()
    print("Open this URL and log in:\n")
    print(login_url(cfg.api_key, cfg.redirect_uri), "\n")
    raw = input("Paste the redirect URL or code: ").strip()
    code = parse_qs(urlparse(raw).query).get("code", [raw])[0]
    token = exchange_code(cfg.api_key, cfg.api_secret, cfg.redirect_uri, code)
    save_token(cfg, token)
    name = Upstox(token).profile().get("user_name", "")
    msg = f"Upstox token refreshed for {name} ({cfg.token_source})"
    print(msg)
    Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id).send(msg)


if __name__ == "__main__":
    main()
