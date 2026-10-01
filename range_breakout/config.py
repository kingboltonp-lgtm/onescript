"""Settings loaded from environment variables (or a .env file)."""
import os
from dataclasses import dataclass
from datetime import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _get(name, default=""):
    return os.environ.get(name, default).strip()


def _time(value):
    h, m = value.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    redirect_uri: str

    token_source: str
    token_ssm_param: str
    token_file: str
    aws_region: str

    telegram_bot_token: str
    telegram_chat_id: str

    signal_instrument: str
    trade_instrument: str
    lot_size: int

    candle_minutes: int
    reward_risk: float
    max_trades_per_day: int
    last_entry_time: time
    square_off_time: time
    max_range_pct: float

    risk_per_trade: float
    max_lots: int

    live_trading: bool


def load_config():
    signal = _get("SIGNAL_INSTRUMENT", "NSE_INDEX|Nifty 50")
    return Config(
        api_key=_get("UPSTOX_API_KEY"),
        api_secret=_get("UPSTOX_API_SECRET"),
        redirect_uri=_get("UPSTOX_REDIRECT_URI"),
        token_source=_get("TOKEN_SOURCE", "ssm").lower(),
        token_ssm_param=_get("TOKEN_SSM_PARAM", "/range-breakout/upstox_access_token"),
        token_file=_get("TOKEN_FILE", ".upstox_token"),
        aws_region=_get("AWS_REGION", "ap-south-1"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
        signal_instrument=signal,
        trade_instrument=_get("TRADE_INSTRUMENT") or signal,
        lot_size=int(_get("LOT_SIZE", "1")),
        candle_minutes=int(_get("CANDLE_MINUTES", "15")),
        reward_risk=float(_get("REWARD_RISK", "2.0")),
        max_trades_per_day=int(_get("MAX_TRADES_PER_DAY", "1")),
        last_entry_time=_time(_get("LAST_ENTRY_TIME", "14:30")),
        square_off_time=_time(_get("SQUARE_OFF_TIME", "15:15")),
        max_range_pct=float(_get("MAX_RANGE_PCT", "0")),
        risk_per_trade=float(_get("RISK_PER_TRADE", "7500")),
        max_lots=int(_get("MAX_LOTS", "1")),
        live_trading=_get("LIVE_TRADING", "false").lower() == "true",
    )
