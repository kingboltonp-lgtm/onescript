"""Tight-range breakout scanner for 15-minute index candles.

Pure logic with no I/O, shared by run_range_live.py and range_backtest.py, so
what you backtest is what runs live. One scanner instance watches one index.

A *tight range* is a run of at least min_candles completed candles (30 by
default, so it reaches back into earlier sessions) whose total high-low span is
among the narrowest tight_percentile % of all 30-candle spans of that index
over the last lookback_days sessions. Each index is judged against its own
recent behaviour, so no fixed point values are needed. Optional extra caps:
range_atr_mult x ATR and max_range_pct of price. The longest such run ending at
the latest candle is taken, and it keeps growing while candles stay inside it.

A *breakout* is price moving breakout_buffer_pct beyond the range high (up) or
low (down): on live LTP when breakout_on="ltp", or on a 15m close when
breakout_on="close". Breakouts more than max_extension_atr past the edge
(e.g. a gap open) are reported as missed, not chased. After a breakout the
scanner looks for the next range.

Levels: SL = sl_atr x ATR back inside the broken edge (never deeper than the
range midpoint); target = target_r x risk.
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
    # Tight = narrower than this percentile of all 30-candle ranges of the same index over the
    # last `lookback_days` sessions, so each index is judged against its own recent behaviour.
    tight_percentile: float = 20.0
    lookback_days: int = 20
    range_atr_mult: float = 0.0     # optional extra cap: span <= this x ATR (0 = off; used alone if tight_percentile=0)
    max_range_pct: float = 0.0
    breakout_buffer_pct: float = 0.02
    breakout_on: str = "ltp"  # ltp | close
    sl_atr: float = 1.0             # SL: 1 ATR back inside the range, never deeper than its midpoint
    target_r: float = 2.0           # target = 2 x risk
    max_extension_atr: float = 0.5  # don't chase: skip if entry is this many ATR past the range edge
    no_entry_before: time = time(9, 15)  # e.g. 09:30 ignores breakouts in the opening candle
    mode: str = "breakout"          # "fade": trade the failure instead (break out, then close back inside)
    fade_window: int = 3            # fade: candles after the break in which price must close back inside
    trend_ema: int = 0              # e.g. 200 (~8 sessions): only trade the side the range sits on vs this 15m EMA (0 = off)
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
    pctile: float = 0.0  # % of recent same-length ranges that were narrower (lower = tighter)

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
    reason: str = ""  # why a SKIP was skipped


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
        self._widths = {}  # end index -> span of the min_candles window ending there
        self._ref = {}  # window start index -> sorted reference widths
        self.pending = None  # fade mode: a breakout waiting to fail
        self.ema = None

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

    def _width(self, end):
        """Span of the min_candles window ending at index `end` (cached)."""
        w = self._widths.get(end)
        if w is None:
            win = self.candles[end - self.p.min_candles + 1:end + 1]
            w = self._widths[end] = max(c.high for c in win) - min(c.low for c in win)
        return w

    def _recent_widths(self, before):
        """Spans of every min_candles window that ended before index `before`, over lookback_days."""
        if before in self._ref:
            return self._ref[before]
        per_day = 375 // self.p.candle_minutes  # 09:15-15:30
        lo = max(self.p.min_candles - 1, before - self.p.lookback_days * per_day)
        widths = sorted(self._width(e) for e in range(lo, before))
        widths = widths if len(widths) >= 5 * per_day else []  # need ~5 sessions to judge
        self._ref[before] = widths
        return widths

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
            pctile = 0.0
            if self.p.tight_percentile:
                widths = self._recent_widths(i)
                if not widths:
                    continue
                k = min(len(widths) - 1, int(len(widths) * self.p.tight_percentile / 100))
                if span > widths[k]:
                    continue
                pctile = sum(w < span for w in widths) / len(widths) * 100
            if self.p.range_atr_mult and span > self.p.range_atr_mult * atr:
                continue
            if not self.p.tight_percentile and not self.p.range_atr_mult:
                continue
            if self.p.max_range_pct and span / self.candles[last].close * 100 > self.p.max_range_pct:
                continue
            best = Range(hi, lo, self.candles[i].ts, self.candles[last].ts, n, atr, round(pctile, 1))
        return best

    def _range_msg(self, r):
        pct = r.width / r.high * 100
        end = r.end + timedelta(minutes=self.p.candle_minutes)
        return (
            f"{self.name} tight range {_fmt(r.low)} - {_fmt(r.high)} "
            f"({r.width:.1f} pts, {pct:.2f}%) over {r.candles}x{self.p.candle_minutes}m "
            f"{span_label(r.start, end)} | ATR {r.atr:.1f}"
            + (f" | narrower than {100 - r.pctile:.0f}% of recent ranges" if self.p.tight_percentile else "")
        )

    def _breakout(self, side, price, ts):
        r = self.rng
        sign = 1 if side == UP else -1
        edge = r.high if side == UP else r.low
        reason = ""
        if (price - edge) * sign > self.p.max_extension_atr * r.atr:
            reason = f"price {_fmt(price)} is too far past {_fmt(edge)} to chase"
        elif self.p.trend_ema and self.ema is not None and ((r.high + r.low) / 2 - self.ema) * sign < 0:
            reason = f"against the trend (EMA{self.p.trend_ema} {_fmt(self.ema)})"
        if reason:
            self.rng = None
            self.search_from = len(self.candles)
            return Event("SKIP", self.name, f"{self.name} broke {side} but {reason}",
                         side=side, price=price, rng=r, reason=reason)
        # A real breakout shouldn't fall back deep into the box: SL sits sl_atr back inside the
        # broken edge, capped at the range midpoint so wide ranges don't get wide stops.
        mid = (r.high + r.low) / 2
        stop = rnd(max(mid, r.high - self.p.sl_atr * r.atr) if side == UP
                   else min(mid, r.low + self.p.sl_atr * r.atr))
        target = rnd(price + sign * self.p.target_r * abs(price - stop))
        self.scalp = Scalp(side, price, stop, target, ts, r)
        self.rng = None
        self.search_from = len(self.candles)  # next range starts after this candle
        return Event(
            "BREAKOUT", self.name,
            f"{self.name} BREAKOUT {side} @ {_fmt(price)} | range {_fmt(r.low)} - {_fmt(r.high)} "
            f"| SL {_fmt(stop)} | TGT {_fmt(target)}",
            side=side, price=price, rng=r, scalp=self.scalp,
        )

    def _check_fade(self, c, late):
        """Fade mode: enter the other way when a breakout closes back inside the range."""
        pd = self.pending
        r, sign = pd["rng"], 1 if pd["side"] == UP else -1
        pd["extreme"] = max(pd["extreme"], c.high) if sign == 1 else min(pd["extreme"], c.low)
        back_inside = c.close < r.high if sign == 1 else c.close > r.low
        if back_inside and not late and not self.scalp:
            self.pending = None
            side = DOWN if sign == 1 else UP
            stop = rnd(pd["extreme"] + sign * 0.1 * r.atr)  # just beyond the failed move's extreme
            entry = c.close
            target = rnd(entry - sign * self.p.target_r * abs(entry - stop))
            self.scalp = Scalp(side, entry, stop, target, c.ts, r)
            return [Event(
                "BREAKOUT", self.name,
                f"{self.name} FAILED BREAKOUT {pd['side']} -> FADE {side} @ {_fmt(entry)} | range "
                f"{_fmt(r.low)} - {_fmt(r.high)} | SL {_fmt(stop)} | TGT {_fmt(target)}",
                side=side, price=entry, rng=r, scalp=self.scalp,
            )]
        pd["n"] += 1
        if pd["n"] > self.p.fade_window or late:
            self.pending = None  # the breakout held (or it's too late): nothing to fade
        return []

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
    def _push(self, c):
        self.candles.append(c)
        if self.p.trend_ema:
            k = 2 / (self.p.trend_ema + 1)
            self.ema = c.close if self.ema is None else self.ema + k * (c.close - self.ema)

    def warmup(self, candles):
        """Load earlier days' candles for ATR, range ranking and EMA. No events."""
        for c in candles:
            self._push(c)

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
        self._push(c)

        # 1. an open scalp from an earlier candle
        if self.scalp and self.scalp.ts < c.ts:
            ev = self._check_scalp_candle(c)
            if ev:
                events.append(ev)
            elif self._end_time(c) >= SESSION_END:
                events.append(self._close_scalp("EXPIRED", c.close))

        late = self._end_time(c) > self.p.last_alert_time

        # 1b. fade mode: a breakout from an earlier candle that may fail
        if self.pending:
            events += self._check_fade(c, late)
            return events

        # 2. breakout of the active range
        if self.rng and c.ts.time() < self.p.no_entry_before:
            return events  # opening candle(s) ignored: the range stays as it was
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
            if side and not late and not self.scalp and self.p.mode == "fade":
                r = self.rng
                self.pending = {"side": side, "extreme": c.high if side == UP else c.low, "rng": r, "n": 0}
                self.rng = None
                self.search_from = len(self.candles)
                events += self._check_fade(c, late)  # a wick through that closes back inside fades at once
                return events
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
        if (self.rng and self.p.breakout_on == "ltp" and self.p.mode == "breakout"
                and ts.time() <= self.p.last_alert_time
                and ts.time() >= self.p.no_entry_before):
            up, down = self._triggers()
            if price >= up:
                return [self._breakout(UP, price, ts)]
            if price <= down:
                return [self._breakout(DOWN, price, ts)]
        return []
