import unittest
from datetime import datetime, time, timedelta

from range_breakout.backtest import run
from range_breakout.strategy import IST, LONG, SHORT, Candle, Params, RangeBreakout


def day(prices, d="2026-09-01"):
    """prices: list of (open, high, low, close) for consecutive 15m candles from 09:15."""
    t0 = datetime.fromisoformat(f"{d}T09:15:00").replace(tzinfo=IST)
    return [Candle(t0 + timedelta(minutes=15 * i), *p) for i, p in enumerate(prices)]


def params(**kw):
    base = dict(risk_per_trade=1000, lot_size=1, max_lots=1000)
    base.update(kw)
    return Params(**base)


FLAT = (100, 101, 99, 100)


class RangeBreakoutTest(unittest.TestCase):
    def test_long_breakout_hits_target(self):
        # range 99-101; close 102 -> long, risk 3, target 108
        candles = day([FLAT, (100, 102.5, 100, 102), (102, 109, 101.5, 108.5)])
        s = RangeBreakout(params())
        evs = [e for c in candles for e in s.on_candle(c)]
        entry = next(e for e in evs if e.kind == "ENTRY")
        self.assertEqual((entry.side, entry.price), (LONG, 102))
        self.assertEqual(entry.qty, 333)  # 1000 / 3
        exit_ = next(e for e in evs if e.kind == "EXIT")
        self.assertEqual((exit_.reason, exit_.price), ("target", 108))

    def test_short_breakout_hits_stop(self):
        candles = day([FLAT, (100, 100, 97, 98), (98, 101.5, 97, 101)])
        s = RangeBreakout(params())
        evs = [e for c in candles for e in s.on_candle(c)]
        self.assertEqual(evs[1].side, SHORT)
        self.assertEqual((evs[2].reason, evs[2].price), ("stop", 101))
        self.assertLess(evs[2].pnl, 0)

    def test_stop_assumed_first_when_both_hit(self):
        candles = day([FLAT, (100, 102.5, 100, 102), (102, 120, 90, 100)])
        s = RangeBreakout(params())
        evs = [e for c in candles for e in s.on_candle(c)]
        self.assertEqual(evs[-1].reason, "stop")

    def test_one_trade_per_day(self):
        candles = day([FLAT, (100, 102.5, 100, 102), (102, 102, 98, 98.5), (98, 98, 96, 96)])
        s = RangeBreakout(params())
        evs = [e for c in candles for e in s.on_candle(c)]
        self.assertEqual(sum(e.kind == "ENTRY" for e in evs), 1)

    def test_square_off(self):
        n = 24  # 09:15 .. 15:15 start
        prices = [FLAT, (100, 102.5, 100, 102)] + [(102, 103, 101.5, 102.5)] * (n - 2)
        trades = run(day(prices), params())
        self.assertEqual(trades[0]["reason"], "square-off")
        self.assertEqual(trades[0]["exit_ts"][11:16], "15:00")

    def test_no_entry_after_cutoff(self):
        prices = [FLAT] * 21 + [(100, 103, 100, 102.5)]  # breakout candle ends 14:45
        s = RangeBreakout(params(last_entry_time=time(14, 30)))
        evs = [e for c in day(prices) for e in s.on_candle(c)]
        self.assertFalse(any(e.kind == "ENTRY" for e in evs))

    def test_skip_when_one_lot_exceeds_risk(self):
        s = RangeBreakout(params(lot_size=75, risk_per_trade=100))
        evs = [e for c in day([FLAT, (100, 102.5, 100, 102)]) for e in s.on_candle(c)]
        self.assertEqual(evs[-1].kind, "SKIP")
        self.assertIsNone(s.s.position)

    def test_wide_range_skips_day(self):
        s = RangeBreakout(params(max_range_pct=1.0))
        evs = [e for c in day([(100, 105, 95, 100), (100, 110, 100, 108)]) for e in s.on_candle(c)]
        self.assertFalse(any(e.kind == "ENTRY" for e in evs))

    def test_lot_rounding_and_cap(self):
        s = RangeBreakout(params(lot_size=50, risk_per_trade=1000, max_lots=2))
        evs = [e for c in day([FLAT, (100, 102.5, 100, 102)]) for e in s.on_candle(c)]
        self.assertEqual(evs[-1].qty, 100)  # floor(1000/150)=6 lots, capped at 2

    def test_on_price_stop_and_state_roundtrip(self):
        s = RangeBreakout(params())
        for c in day([FLAT, (100, 102.5, 100, 102)]):
            s.on_candle(c)
        s2 = RangeBreakout(params())
        s2.load_dict(s.to_dict())
        ev = s2.on_price(datetime(2026, 9, 1, 10, 0, tzinfo=IST), 98.9)
        self.assertEqual(ev[0].reason, "stop")

    def test_duplicate_candle_ignored(self):
        s = RangeBreakout(params())
        candles = day([FLAT, (100, 102.5, 100, 102)])
        for c in candles:
            s.on_candle(c)
        self.assertEqual(s.on_candle(candles[-1]), [])


if __name__ == "__main__":
    unittest.main()
