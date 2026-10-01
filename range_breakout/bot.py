"""Live 15-minute range breakout bot.

Run once per trading day (systemd timer / cron at ~09:10 IST):

    python -m range_breakout.bot

It waits for the market, builds the opening range, watches each completed
15-minute candle for a breakout, manages stop/target with LTP polling, squares
off at SQUARE_OFF_TIME and exits after the close. Orders are only sent when
LIVE_TRADING=true; otherwise every action is a [PAPER] alert.
"""
import json
import logging
import os
import time as _time
from datetime import datetime, time, timedelta

from .config import load_config
from .strategy import IST, LONG, Params, RangeBreakout
from .telegram import Telegram
from .token_store import load_token
from .upstox_client import TokenExpired, Upstox

log = logging.getLogger("range_breakout")

POLL_SECONDS = 20
MARKET_CLOSE = time(15, 30)
STATE_DIR = os.environ.get("STATE_DIR", "state")


def now_ist():
    return datetime.now(IST)


def state_path(day):
    return os.path.join(STATE_DIR, f"{day}.json")


def save_state(strat):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(strat.s.day), "w") as fh:
        json.dump(strat.to_dict(), fh, indent=2)


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
            tg.send("Range breakout: Upstox token missing or expired. Run get_token to log in.")
            alerted = True
        _time.sleep(60)
    return None


class Executor:
    def __init__(self, cfg, client, tg):
        self.cfg, self.client, self.tg = cfg, client, tg

    def handle(self, ev):
        tag = "" if self.cfg.live_trading else "[PAPER] "
        self.tg.send(f"{tag}{self.cfg.signal_instrument}: {ev.message}")
        if ev.kind not in ("ENTRY", "EXIT") or not self.cfg.live_trading:
            return
        if ev.kind == "ENTRY":
            side = "BUY" if ev.side == LONG else "SELL"
        else:
            side = "SELL" if ev.side == LONG else "BUY"
        try:
            resp = self.client.place_market_order(self.cfg.trade_instrument, side, ev.qty)
            self.tg.send(f"Order {side} {ev.qty} {self.cfg.trade_instrument} placed: {resp}")
        except Exception as exc:  # never die silently with a position open
            log.exception("order failed")
            self.tg.send(f"ORDER FAILED {side} {ev.qty} {self.cfg.trade_instrument}: {exc}. Check the position manually!")


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    client = wait_for_token(cfg, tg)
    if not client:
        return
    strat = RangeBreakout(Params.from_config(cfg))
    exe = Executor(cfg, client, tg)

    today = now_ist().date().isoformat()
    resumed = os.path.exists(state_path(today))
    if resumed:
        with open(state_path(today)) as fh:
            strat.load_dict(json.load(fh))
        log.info("Resumed state for %s", today)
    mode = "LIVE" if cfg.live_trading else "PAPER"
    tg.send(f"Range breakout bot started ({mode}) on {cfg.signal_instrument}, {cfg.candle_minutes}m candles")

    first_fetch = not resumed
    step = timedelta(minutes=cfg.candle_minutes)
    while True:
        now = now_ist()
        if now.time() >= MARKET_CLOSE:
            break
        try:
            candles = [
                c for c in client.intraday_candles(cfg.signal_instrument, cfg.candle_minutes)
                if c.ts + step <= now and c.ts.date().isoformat() == today
            ]
            if first_fetch and len(candles) > 1:
                # Started late with no saved state: rebuild the range silently and
                # don't act on breakouts that already happened before we were running.
                for c in candles[:-1]:
                    for ev in strat.on_candle(c):
                        if ev.kind == "ENTRY":
                            strat.s.position = None
                            tg.send(f"Started late: missed {ev.message}")
                candles = candles[-1:]
            if candles:
                first_fetch = False
            for c in candles:
                for ev in strat.on_candle(c):
                    exe.handle(ev)
            if strat.s.position:
                for ev in strat.on_price(now, client.ltp(cfg.signal_instrument)):
                    exe.handle(ev)
            if strat.s.day:
                save_state(strat)
        except TokenExpired:
            tg.send("Upstox token expired mid-session. Run get_token; bot is waiting.")
            client = wait_for_token(cfg, tg)
            if not client:
                break
            exe.client = client
        except Exception as exc:
            log.exception("loop error")
            tg.send(f"Range breakout loop error: {exc}")
        _time.sleep(POLL_SECONDS)

    if not strat.s.day:
        tg.send("Range breakout: no candles today (holiday?)")
        return
    trades = strat.s.closed
    summary = ", ".join(f"{t['side']} {t['pnl']:+.0f} ({t['reason']})" for t in trades) or "no trades"
    tg.send(f"Range breakout {today} done. {summary}. Day P&L {strat.s.realized_pnl:+.2f}")


if __name__ == "__main__":
    run()
