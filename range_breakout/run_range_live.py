"""Live tight-range breakout monitor for NIFTY 50, BANKNIFTY and SENSEX (15m).
Alerts only: it never places orders.

Lives in the orb_scalper folder and reuses its .env token, Upstox client and
Telegram sender. Started by cron at 08:50 on weekdays (aws/crontab_range.txt):
it waits for today's token (pushed by aws\\push_token.ps1), runs until 15:30,
then sends a day summary.

    python run_range_live.py
"""
import csv
import logging
import sys
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from config import LOG_DIR, load_env
from range_scan.alerts import fmt_event
from range_scan.strategy import INDEX_NAMES, PARAMS as P, Candle, Event, TightRangeScanner
from scalper.telegram_alert import Telegram
from scalper.universe import INDICES
from scalper.upstox_client import TokenExpired, UpstoxClient

IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = dtime(9, 15), dtime(15, 30)
STOP_AT = dtime(15, 31)            # lets the 15:15 candle land so open scalps get closed
TOKEN_REMIND_AT = [dtime(9, 10), dtime(9, 40)]
TOKEN_GIVE_UP = dtime(10, 30)
WARMUP_DAYS = 35                   # ~20 sessions to rank range widths, plus the range window and ATR
LTP_POLL_SECONDS = 10
BAR_CLOSE_DELAY_SECONDS = 4
log = logging.getLogger("range_live")


def now_ist() -> datetime:
    return datetime.now(IST)


def df_to_candles(df) -> list[Candle]:
    return [Candle(ts.to_pydatetime(), float(r.open), float(r.high), float(r.low), float(r.close),
                   float(r.volume)) for ts, r in df.iterrows()]


def wait_for_token(tg: Telegram):
    """Return a working client once .env holds a valid token; None if we give up."""
    reminded = set()
    while True:
        token = load_env()["access_token"]
        if token:
            api = UpstoxClient(token)
            try:
                api.profile()
                return api
            except Exception:
                pass
        now = now_ist().time()
        for t in TOKEN_REMIND_AT:
            if now >= t and t not in reminded:
                reminded.add(t)
                tg.send("🔑 <b>Range scanner waiting for today's Upstox token.</b>\n"
                        "On your laptop run:  <code>powershell -File aws\\push_token.ps1</code>")
        if now >= TOKEN_GIVE_UP:
            tg.send("⏹️ Range scanner: no working Upstox token by 10:30, not starting today.")
            return None
        time.sleep(30)


class RangeMonitor:
    def __init__(self):
        env = load_env()
        self.tg = Telegram(env["tg_token"], env["tg_chat"])
        self.api = None
        self.keys = {name: INDICES[name] for name in INDEX_NAMES}
        self.scanners = {name: TightRangeScanner(name, P) for name in INDEX_NAMES}
        self.caught_up = set()
        self.done = []  # closed scalp events
        LOG_DIR.mkdir(exist_ok=True)
        self.trade_file = LOG_DIR / f"range_trades_{now_ist():%Y%m%d}.csv"

    def startup(self) -> bool:
        self.api = wait_for_token(self.tg)
        if not self.api:
            return False
        today = now_ist().date()
        for name, sc in self.scanners.items():
            try:
                hist = self.api.historical_candles(self.keys[name], P.candle_minutes,
                                                   today - timedelta(days=WARMUP_DAYS), today - timedelta(days=1))
                sc.warmup(df_to_candles(hist))
            except Exception as e:
                log.warning("warmup %s failed: %s", name, e)
        self.tg.send(f"📐 <b>Range scanner started</b>: {', '.join(INDEX_NAMES)} on {P.candle_minutes}m\n"
                     f"Tight = {P.min_candles}+ candles in the narrowest {P.tight_percentile:g}% of the last "
                     f"{P.lookback_days} sessions | SL {P.sl_atr:g} ATR inside (max midpoint) | "
                     f"target {P.target_r:g}R | last alert {P.last_alert_time:%H:%M}")
        return True

    def process_bar(self, bar_end: datetime):
        for name, sc in self.scanners.items():
            try:
                df = self.api.intraday_candles(self.keys[name], P.candle_minutes)
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("intraday %s failed: %s", name, e)
                continue
            candles = [c for c in df_to_candles(df)
                       if c.ts + timedelta(minutes=P.candle_minutes) <= bar_end and c.ts.date() == bar_end.date()]
            if name not in self.caught_up and candles:
                # first fetch, or a restart mid-day: rebuild today's state without re-alerting
                for c in candles[:-1]:
                    sc.on_candle(c)
                candles = candles[-1:]
                self.caught_up.add(name)
                if sc.rng:  # a range found during the replay still deserves its alert
                    self._handle([Event("RANGE", name, sc._range_msg(sc.rng), rng=sc.rng)])
            for c in candles:
                self._handle(sc.on_candle(c))

    def poll_ltp(self):
        names = [n for n, sc in self.scanners.items() if sc.scalp or (sc.rng and P.breakout_on == "ltp")]
        if not names:
            return
        prices = self.api.ltp([self.keys[n] for n in names])
        now = now_ist()
        for name in names:
            ltp = prices.get(self.keys[name])
            if ltp is not None:
                self._handle(self.scanners[name].on_price(now, ltp))

    def _handle(self, events):
        for ev in events:
            log.info(ev.message)
            self.tg.send(fmt_event(ev, P))
            if ev.kind in ("TARGET", "STOP", "EXPIRED"):
                self.done.append(ev)
                self._log_trade(ev)

    def _log_trade(self, ev):
        s = ev.scalp
        new = not self.trade_file.exists()
        with open(self.trade_file, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["index", "side", "range_low", "range_high", "range_candles", "entry_time", "entry",
                            "sl", "target", "exit_time", "exit", "result", "points"])
            w.writerow([ev.name, s.side, s.rng.low, s.rng.high, s.rng.candles, s.ts, s.entry, s.stop, s.target,
                        now_ist(), ev.price, ev.kind, ev.points])

    def summary(self):
        if not self.done:
            self.tg.send("📊 <b>Range scanner day summary</b>: no breakouts today.")
            return
        wins = sum(e.points > 0 for e in self.done)
        lines = [f"{e.name} {e.side} {e.points:+.2f} pts ({e.kind.lower()})" for e in self.done]
        self.tg.send(f"📊 <b>Range scanner day summary</b>: {len(self.done)} breakouts | {wins} wins\n"
                     + "\n".join(lines))

    def run(self):
        if not self.startup():
            return
        step = timedelta(minutes=P.candle_minutes)
        processed, last_poll = None, 0.0
        while True:
            now = now_ist()
            if now.time() >= STOP_AT:
                break
            if now.time() < OPEN:
                time.sleep(15)
                continue
            session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
            bar_end = session_open + ((now - session_open) // step) * step
            ready = now >= bar_end + timedelta(seconds=BAR_CLOSE_DELAY_SECONDS)
            try:
                if bar_end > session_open and bar_end != processed and ready:
                    self.process_bar(bar_end)
                    processed = bar_end
                elif now.time() < CLOSE and time.time() - last_poll >= LTP_POLL_SECONDS:
                    last_poll = time.time()
                    self.poll_ltp()
            except TokenExpired as e:
                self.tg.send(f"⚠️ <b>Range scanner stopped</b>: {e}")
                return
            except Exception as e:
                log.exception("loop error: %s", e)
            time.sleep(1)
        self.summary()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if now_ist().weekday() >= 5:
        sys.exit("Weekend, market closed.")
    RangeMonitor().run()
