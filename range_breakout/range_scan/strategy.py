"""Tight-range breakout scanner for 15-minute index candles.

Pure logic with no I/O, shared by run_range_live.py and range_backtest.py, so
what you backtest is what runs live. One scanner instance watches one index.

A *tight range* is a run of at least min_candles completed candles (30 by
default, so it reaches back into earlier sessions; set span_days=False to keep
ranges inside one day) whose total high-low span is at most range_atr_mult x the ATR of
the candles before it (and, optionally, at most max_range_pct of price). The
longest such run ending at the latest candle is taken, and it keeps growing
while new candles stay inside it.

A *breakout* is price moving breakout_buffer_pct beyond the range high (up) or
low (down): on live LTP when breakout_on="ltp", or on a 15m close when
breakout_on="close". Each breakout gets a scalp plan (stop = other side of the
range, target = target_mult x range width from entry) and its outcome is
reported. After a breakout the scanner looks for the next range.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_END = time(15, 30)

UP = "UP"
DOWN = "DOWN"


@dataclass
class Candle:
    ts: datetime  # candle start, IST
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class RangeParams:
    """Tune the scanner here (the ORB scalper's StrategyParams is left untouched)."""
    candle_minutes: int = 15
    min_candles: int = 30           # 30 x 15m = 7.5 trading hours, so ranges span sessions
    max_candles: int = 60
    span_days: bool = True          # let a range continue across the overnight gap
    atr_period: int = 14
    range_atr_mult: float = 4.0     # 30-candle span vs one-candle ATR; calibrate with the backtest
    max_range_pct: float = 0.0
    breakout_buffer_pct: float = 0.02
    breakout_on: str = "ltp"  # ltp | close
    target_mult: float = 1.0
    max_extension_atr: float = 0.5  # don't chase: skip if entry is this many ATR past the range edge
    last_alert_time: time = time(15, 0)


PARAMS = RangeParams()
INDEX_NAMES = ["NIFTY 50", "BANKNIFTY", "SENSEX"]  # keys of scalper.universe.INDICES


@dataclass
class Range:
    high: float
    low: float
    start: datetime
    end: datetime  # start of the last candle inside the range
    candles: int
    atr: float

    @property
    def width(self):
        return self.high - self.low


@dataclass
class Scalp:
    side: str
    entry: float
    stop: float
    target: float
    ts: datetime
    rng: Range


@dataclass
class Event:
    kind: str  # RANGE, BREAKOUT, SKIP, TARGET, STOP, EXPIRED
    name: str
    message: str  # plain-text summary (logs, backtest)
    side: str = ""
    price: float = 0.0
    points: float = 0.0
    rng: Range = None
    scalp: Scalp = None


TICK = 0.05


def rnd(x):
    return round(round(x / TICK) * TICK, 2)


def span_label(start, end):
    """'11:15-12:15' within a day, '30 Sep 13:15 - 01 Oct 12:15' across days."""
    if start.date() == end.date():
        return f"{start:%H:%M}-{end:%H:%M}"
    return f"{start:%d %b %H:%M} - {end:%d %b %H:%M}"


def _fmt(x):
    return f"{x:,.2f}"


class TightRangeScanner:
    def __init__(self, name, params):
        self.name = name
        self.p = params
        self.candles = []  # history + today, oldest first
        self.day = None
        self.day_start = 0  # index of today's first candle
        self.search_from = 0  # ranges may not start before this index
        self.rng = None
        self.scalp = None

    # --- helpers -----------------------------------------------------------
    def _end_time(self, c):
        return (c.ts + timedelta(minutes=self.p.candle_minutes)).time()

    def _atr(self, upto):
        """ATR of the atr_period candles before index `upto` (may span days)."""
        lo = upto - self.p.atr_period
        if lo < 1:
            return 0.0
        trs = []
        for i in range(lo, upto):
            c, prev = self.candles[i], self.candles[i - 1]
            trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
        return sum(trs) / len(trs)

    def _triggers(self):
        buf = self.rng.high * self.p.breakout_buffer_pct / 100
        return rnd(self.rng.high + buf), rnd(self.rng.low - buf)

    def _find_range(self):
        last = len(self.candles) - 1
        first_allowed = self.search_from if self.p.span_days else max(self.day_start, self.search_from)
        best = None
        hi = lo = None
        for n in range(1, self.p.max_candles + 1):
            i = last - n + 1
            if i < first_allowed:
                break
            c = self.candles[i]
            hi = c.high if hi is None else max(hi, c.high)
            lo = c.low if lo is None else min(lo, c.low)
            if n < self.p.min_candles:
                continue
            atr = self._atr(i)
            if not atr:
                continue
            span = hi - lo
            if span > self.p.range_atr_mult * atr:
                continue
            if self.p.max_range_pct and span / self.candles[last].close * 100 > self.p.max_range_pct:
                continue
            best = Range(hi, lo, self.candles[i].ts, self.candles[last].ts, n, atr)
        return best

    def _range_msg(self, r):
        pct = r.width / r.high * 100
        end = r.end + timedelta(minutes=self.p.candle_minutes)
        return (
            f"{self.name} tight range {_fmt(r.low)} - {_fmt(r.high)} "
            f"({r.width:.1f} pts, {pct:.2f}%) over {r.candles}x{self.p.candle_minutes}m "
            f"{span_label(r.start, end)} | ATR {r.atr:.1f}"
        )

    def _breakout(self, side, price, ts):
        r = self.rng
        sign = 1 if side == UP else -1
        edge = r.high if side == UP else r.low
        if (price - edge) * sign > self.p.max_extension_atr * r.atr:
            self.rng = None
            self.search_from = len(self.candles)
            return Event("SKIP", self.name,
                         f"{self.name} broke {side} but price {_fmt(price)} is too far past "
                         f"{_fmt(edge)} to chase", side=side, price=price, rng=r)
        stop = r.low if side == UP else r.high
        target = rnd(price + sign * self.p.target_mult * r.width)
        self.scalp = Scalp(side, price, stop, target, ts, r)
        self.rng = None
        self.search_from = len(self.candles)  # next range starts after this candle
        return Event(
            "BREAKOUT", self.name,
            f"{self.name} BREAKOUT {side} @ {_fmt(price)} | range {_fmt(r.low)} - {_fmt(r.high)} "
            f"| SL {_fmt(stop)} | TGT {_fmt(target)}",
            side=side, price=price, rng=r, scalp=self.scalp,
        )

    def _close_scalp(self, kind, price):
        s = self.scalp
        pts = (price - s.entry) * (1 if s.side == UP else -1)
        self.scalp = None
        label = {"TARGET": "target hit", "STOP": "stop hit", "EXPIRED": "closed at end of day"}[kind]
        return Event(kind, self.name, f"{self.name} {s.side} scalp {label} @ {_fmt(price)} ({pts:+.1f} pts)",
                     side=s.side, price=price, points=round(pts, 2), rng=s.rng, scalp=s)

    def _check_scalp_candle(self, c):
        s = self.scalp
        if s.side == UP:
            if c.low <= s.stop:  # stop first when both are touched: conservative
                return self._close_scalp("STOP", s.stop)
            if c.high >= s.target:
                return self._close_scalp("TARGET", s.target)
        else:
            if c.high >= s.stop:
                return self._close_scalp("STOP", s.stop)
            if c.low <= s.target:
                return self._close_scalp("TARGET", s.target)
        return None

    # --- inputs ------------------------------------------------------------
    def warmup(self, candles):
        """Load earlier days' candles for ATR. No events."""
        self.candles.extend(candles)

    def on_candle(self, c):
        """Feed one completed candle. Returns a list of Events."""
        if self.candles and c.ts <= self.candles[-1].ts:
            return []
        events = []
        if c.ts.date() != self.day:
            if self.scalp:
                last = self.candles[-1]
                events.append(self._close_scalp("EXPIRED", last.close))
            self.day = c.ts.date()
            self.day_start = len(self.candles)
            if not self.p.span_days:
                self.search_from = self.day_start
                self.rng = None
        self.candles.append(c)

        # 1. an open scalp from an earlier candle
        if self.scalp and self.scalp.ts < c.ts:
            ev = self._check_scalp_candle(c)
            if ev:
                events.append(ev)
            elif self._end_time(c) >= SESSION_END:
                events.append(self._close_scalp("EXPIRED", c.close))

        late = self._end_time(c) > self.p.last_alert_time

        # 2. breakout of the active range
        if self.rng:
            up, down = self._triggers()
            if self.p.breakout_on == "close":
                side = UP if c.close > up else DOWN if c.close < down else None
                price = c.close
            else:  # ltp mode: catch anything the price polls missed
                hit_up, hit_down = c.high >= up, c.low <= down
                if hit_up and hit_down:  # whipsaw through both sides
                    side = UP if c.close > self.rng.high else DOWN if c.close < self.rng.low else None
                    if not side:
                        self.rng = None
                        self.search_from = len(self.candles)
                else:
                    side = UP if hit_up else DOWN if hit_down else None
                price = up if side == UP else down
                if side == UP and c.open > up or side == DOWN and c.open < down:
                    price = c.open  # gapped through the level: the open is the first price seen
            if side and not late and not self.scalp:
                events.append(self._breakout(side, price, c.ts))
                ev = self._check_scalp_candle(c) if self.scalp and self.p.breakout_on == "ltp" else None
                if ev:
                    events.append(ev)
                return events
            if side:
                self.rng = None  # broke out after LAST_ALERT_TIME or while a scalp is open
                self.search_from = len(self.candles)
            elif self.rng:  # still inside: the range keeps growing
                self.rng.end = c.ts
                self.rng.candles += 1
                return events

        # 3. look for a new tight range
        if not late and not self.scalp:
            r = self._find_range()
            if r:
                self.rng = r
                events.append(Event("RANGE", self.name, self._range_msg(r), rng=r))
        return events

    def on_price(self, ts, price):
        """Live LTP between candles: breakouts (ltp mode) and scalp exits."""
        if self.scalp:
            s = self.scalp
            if (s.side == UP and price <= s.stop) or (s.side == DOWN and price >= s.stop):
                return [self._close_scalp("STOP", price)]
            if (s.side == UP and price >= s.target) or (s.side == DOWN and price <= s.target):
                return [self._close_scalp("TARGET", price)]
            return []
        if self.rng and self.p.breakout_on == "ltp" and ts.time() <= self.p.last_alert_time:
            up, down = self._triggers()
            if price >= up:
                return [self._breakout(UP, price, ts)]
            if price <= down:
                return [self._breakout(DOWN, price, ts)]
        return []
