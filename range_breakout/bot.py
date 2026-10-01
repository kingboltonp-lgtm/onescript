"""Live tight-range breakout scanner for Nifty 50, Bank Nifty and Sensex.

Run once per trading day (systemd timer at ~09:05 IST):

    python -m range_breakout.bot

Every completed 15m candle it looks for a tight range on each index and
alerts on Telegram when one forms; between candles it polls LTP and alerts the
moment price breaks out up or down, with a scalp stop and target, then reports
whether the target or stop was hit. Alerts only: it never places orders.
If restarted mid-day it rebuilds today's state silently from the candles.
"""
import logging
import time as _time
from datetime import date, datetime, time, timedelta

from .config import load_config, short_name
from .strategy import IST, Params, TightRangeScanner
from .telegram import Telegram
from .token_store import load_token
from .upstox_client import TokenExpired, Upstox

log = logging.getLogger("range_breakout")

POLL_SECONDS = 15
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
STOP_AT = time(15, 32)  # lets the 15:15 candle land so open scalps get closed
WARMUP_DAYS = 7

ICONS = {"RANGE": "🟡", "BREAKOUT": "🚀", "TARGET": "✅", "STOP": "❌", "EXPIRED": "⏹"}


def now_ist():
    return datetime.now(IST)


def wait_for_token(cfg, tg):
    """Return a working Upstox client, alerting until a valid token appears."""
    alerted = False
    while now_ist().time() < MARKET_CLOSE:
        token = load_token(cfg)
        if token:
            client = Upstox(token)
            try:
                client.profile()
                return client
            except TokenExpired:
                pass
        if not alerted:
            tg.send("Range scanner: Upstox token missing or expired. Run get_token to log in.")
            alerted = True
        _time.sleep(60)
    return None


def build_scanners(cfg, client, tg):
    params = Params.from_config(cfg)
    today = date.today()
    scanners = {}
    for key in cfg.indices:
        sc = TightRangeScanner(short_name(key), params)
        try:
            hist = client.historical_candles(key, cfg.candle_minutes,
                                             (today - timedelta(days=WARMUP_DAYS)).isoformat(),
                                             (today - timedelta(days=1)).isoformat())
            sc.warmup(hist)
        except Exception as exc:  # ATR then builds up from today's candles only
            log.warning("warmup failed for %s: %s", key, exc)
            tg.send(f"Range scanner: no history for {sc.name} ({exc}); ATR will start from today's candles")
        scanners[key] = sc
    return scanners


def alert(tg, ev):
    tg.send(f"{ICONS.get(ev.kind, '')} {ev.message}")


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    client = wait_for_token(cfg, tg)
    if not client:
        return
    scanners = build_scanners(cfg, client, tg)
    names = ", ".join(sc.name for sc in scanners.values())
    tg.send(f"Range scanner started on {names} ({cfg.candle_minutes}m, breakout on {cfg.breakout_on})")

    step = timedelta(minutes=cfg.candle_minutes)
    caught_up = set()
    seen_candles = 0
    while now_ist().time() < STOP_AT:
        now = now_ist()
        if now.time() < MARKET_OPEN:
            _time.sleep(POLL_SECONDS)
            continue
        try:
            for key, sc in scanners.items():
                candles = [c for c in client.intraday_candles(key, cfg.candle_minutes)
                           if c.ts + step <= now and c.ts.date() == now.date()]
                seen_candles += len(candles)
                if key not in caught_up and candles:
                    # first fetch (also after a restart): replay all but the latest silently
                    for c in candles[:-1]:
                        sc.on_candle(c)
                    candles = candles[-1:]
                    caught_up.add(key)
                for c in candles:
                    for ev in sc.on_candle(c):
                        alert(tg, ev)
            prices = client.ltp(list(scanners))
            for key, sc in scanners.items():
                if key in prices:
                    for ev in sc.on_price(now, prices[key]):
                        alert(tg, ev)
        except TokenExpired:
            tg.send("Upstox token expired mid-session. Run get_token; scanner is waiting.")
            client = wait_for_token(cfg, tg)
            if not client:
                break
        except Exception as exc:
            log.exception("loop error")
            tg.send(f"Range scanner error: {exc}")
        _time.sleep(POLL_SECONDS)

    if not seen_candles:
        tg.send("Range scanner: no candles today (holiday?)")
        return
    tg.send("Range scanner stopped for the day.")


if __name__ == "__main__":
    run()
