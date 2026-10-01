"""Settings loaded from environment variables (or a .env file)."""
import os
from dataclasses import dataclass
from datetime import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

DEFAULT_INDICES = "NSE_INDEX|Nifty 50,NSE_INDEX|Nifty Bank,BSE_INDEX|SENSEX"


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

    indices: tuple

    candle_minutes: int
    min_range_candles: int
    max_range_candles: int
    atr_period: int
    range_atr_mult: float
    max_range_pct: float
    breakout_buffer_pct: float
    breakout_on: str
    target_mult: float
    last_alert_time: time


def load_config():
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
        indices=tuple(k.strip() for k in _get("INDICES", DEFAULT_INDICES).split(",") if k.strip()),
        candle_minutes=int(_get("CANDLE_MINUTES", "15")),
        min_range_candles=int(_get("MIN_RANGE_CANDLES", "4")),
        max_range_candles=int(_get("MAX_RANGE_CANDLES", "12")),
        atr_period=int(_get("ATR_PERIOD", "14")),
        range_atr_mult=float(_get("RANGE_ATR_MULT", "1.5")),
        max_range_pct=float(_get("MAX_RANGE_PCT", "0")),
        breakout_buffer_pct=float(_get("BREAKOUT_BUFFER_PCT", "0.02")),
        breakout_on=_get("BREAKOUT_ON", "ltp").lower(),
        target_mult=float(_get("TARGET_MULT", "1.0")),
        last_alert_time=_time(_get("LAST_ALERT_TIME", "15:00")),
    )


def short_name(instrument_key):
    """'NSE_INDEX|Nifty 50' -> 'Nifty 50'."""
    return instrument_key.split("|", 1)[-1]
