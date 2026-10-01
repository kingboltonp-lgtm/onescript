"""15-minute opening range breakout.

Pure logic with no I/O, so the live bot and the backtest share it.

Rules (defaults, all configurable):
  * Opening range = high/low of the first candle of the session (09:15-09:30).
  * Long when a completed candle closes above the range high, short when one
    closes below the range low. Entry at that candle's close.
  * Stop = opposite side of the range. Target = entry +/- REWARD_RISK x risk.
  * No new entries after LAST_ENTRY_TIME; anything open is squared off at
    SQUARE_OFF_TIME. At most MAX_TRADES_PER_DAY trades.
  * Quantity = whole lots whose loss at the stop fits RISK_PER_TRADE, capped
    at MAX_LOTS. If not even one lot fits, the signal is skipped.
"""
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START = time(9, 15)

LONG = "LONG"
SHORT = "SHORT"


@dataclass
class Candle:
    ts: datetime  # candle start, IST
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Params:
    candle_minutes: int = 15
    reward_risk: float = 2.0
    max_trades_per_day: int = 1
    last_entry_time: time = time(14, 30)
    square_off_time: time = time(15, 15)
    max_range_pct: float = 0.0
    risk_per_trade: float = 7500.0
    lot_size: int = 1
    max_lots: int = 1

    @classmethod
    def from_config(cls, cfg):
        return cls(
            candle_minutes=cfg.candle_minutes,
            reward_risk=cfg.reward_risk,
            max_trades_per_day=cfg.max_trades_per_day,
            last_entry_time=cfg.last_entry_time,
            square_off_time=cfg.square_off_time,
            max_range_pct=cfg.max_range_pct,
            risk_per_trade=cfg.risk_per_trade,
            lot_size=cfg.lot_size,
            max_lots=cfg.max_lots,
        )


@dataclass
class Position:
    side: str
    entry: float
    stop: float
    target: float
    qty: int
    entry_ts: str


@dataclass
class Event:
    kind: str  # RANGE, ENTRY, EXIT, SKIP
    message: str
    side: str = ""
    price: float = 0.0
    qty: int = 0
    pnl: float = 0.0
    reason: str = ""


@dataclass
class DayState:
    day: str = ""
    range_high: float = 0.0
    range_low: float = 0.0
    range_set: bool = False
    skip_day: bool = False
    trades: int = 0
    last_candle_ts: str = ""
    position: Position = None
    realized_pnl: float = 0.0
    closed: list = field(default_factory=list)


class RangeBreakout:
    def __init__(self, params):
        self.p = params
        self.s = DayState()

    # --- persistence -------------------------------------------------------
    def to_dict(self):
        return asdict(self.s)

    def load_dict(self, data):
        pos = data.get("position")
        data = dict(data, position=Position(**pos) if pos else None)
        self.s = DayState(**data)

    # --- helpers -----------------------------------------------------------
    def _candle_end(self, c):
        return (c.ts + timedelta(minutes=self.p.candle_minutes)).time()

    def _size(self, entry, stop):
        risk_per_lot = abs(entry - stop) * self.p.lot_size
        if risk_per_lot <= 0:
            return 0
        lots = min(math.floor(self.p.risk_per_trade / risk_per_lot), self.p.max_lots)
        return max(lots, 0) * self.p.lot_size

    def _exit(self, price, reason, ts):
        pos = self.s.position
        sign = 1 if pos.side == LONG else -1
        pnl = round((price - pos.entry) * sign * pos.qty, 2)
        self.s.realized_pnl = round(self.s.realized_pnl + pnl, 2)
        self.s.closed.append(dict(asdict(pos), exit=price, exit_ts=ts.isoformat(), reason=reason, pnl=pnl))
        self.s.position = None
        return Event(
            "EXIT",
            f"EXIT {pos.side} x{pos.qty} @ {price:.2f} ({reason}) | P&L {pnl:+.2f} | day {self.s.realized_pnl:+.2f}",
            side=pos.side, price=price, qty=pos.qty, pnl=pnl, reason=reason,
        )

    # --- inputs ------------------------------------------------------------
    def on_candle(self, c):
        """Feed one completed candle. Returns a list of Events."""
        day = c.ts.date().isoformat()
        if day != self.s.day:
            self.s = DayState(day=day)
        if self.s.last_candle_ts and c.ts.isoformat() <= self.s.last_candle_ts:
            return []  # already processed
        self.s.last_candle_ts = c.ts.isoformat()
        events = []

        if not self.s.range_set:
            if c.ts.time() != SESSION_START:
                self.s.skip_day = True
                self.s.range_set = True
                return [Event("SKIP", f"{day}: first candle missing, no trades today")]
            self.s.range_high, self.s.range_low, self.s.range_set = c.high, c.low, True
            width = c.high - c.low
            msg = f"{day} opening range {c.low:.2f} - {c.high:.2f} (width {width:.2f})"
            if self.p.max_range_pct and width / c.close * 100 > self.p.max_range_pct:
                self.s.skip_day = True
                msg += f" wider than {self.p.max_range_pct}%, skipping the day"
            return [Event("RANGE", msg)]

        end = self._candle_end(c)

        if self.s.position:
            pos = self.s.position
            if pos.side == LONG:
                if c.low <= pos.stop:  # stop checked first: conservative
                    events.append(self._exit(pos.stop, "stop", c.ts))
                elif c.high >= pos.target:
                    events.append(self._exit(pos.target, "target", c.ts))
            else:
                if c.high >= pos.stop:
                    events.append(self._exit(pos.stop, "stop", c.ts))
                elif c.low <= pos.target:
                    events.append(self._exit(pos.target, "target", c.ts))
            if self.s.position and end >= self.p.square_off_time:
                events.append(self._exit(c.close, "square-off", c.ts))
            return events

        if self.s.skip_day or self.s.trades >= self.p.max_trades_per_day:
            return events
        if end > self.p.last_entry_time:
            return events

        side = LONG if c.close > self.s.range_high else SHORT if c.close < self.s.range_low else None
        if not side:
            return events
        entry = c.close
        stop = self.s.range_low if side == LONG else self.s.range_high
        risk = abs(entry - stop)
        target = entry + self.p.reward_risk * risk * (1 if side == LONG else -1)
        qty = self._size(entry, stop)
        if qty <= 0:
            self.s.trades += 1  # count it so we don't chase later candles
            return [Event("SKIP", f"{side} breakout @ {entry:.2f} skipped: risk {risk:.2f}/unit too big for budget")]
        self.s.trades += 1
        self.s.position = Position(side, entry, stop, round(target, 2), qty, c.ts.isoformat())
        events.append(Event(
            "ENTRY",
            f"ENTRY {side} x{qty} @ {entry:.2f} | SL {stop:.2f} | TGT {target:.2f} | range {self.s.range_low:.2f}-{self.s.range_high:.2f}",
            side=side, price=entry, qty=qty,
        ))
        return events

    def on_price(self, ts, price):
        """Live price check between candles for stop/target/square-off."""
        pos = self.s.position
        if not pos:
            return []
        if pos.side == LONG:
            if price <= pos.stop:
                return [self._exit(price, "stop", ts)]
            if price >= pos.target:
                return [self._exit(price, "target", ts)]
        else:
            if price >= pos.stop:
                return [self._exit(price, "stop", ts)]
            if price <= pos.target:
                return [self._exit(price, "target", ts)]
        if ts.time() >= self.p.square_off_time:
            return [self._exit(price, "square-off", ts)]
        return []
