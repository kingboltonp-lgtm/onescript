"""Offline tests on synthetic candles (no API, no orb_scalper files needed):
    python tests/test_range_scanner.py   or   python -m pytest -q tests/test_range_scanner.py
"""
import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from range_backtest import simulate  # noqa: E402
from range_scan.alerts import fmt_event  # noqa: E402
from range_scan.strategy import DOWN, IST, UP, Candle, RangeParams, TightRangeScanner  # noqa: E402

T0 = datetime(2026, 9, 1, 9, 15, tzinfo=IST)


def candles(ohlc, start=T0):
    return [Candle(start + timedelta(minutes=15 * i), *p) for i, p in enumerate(ohlc)]


def history():
    """A previous day of 20-point candles so ATR is ~20."""
    prev = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    return candles([(100, 110, 90, 100)] * 25, prev)


WIDE = (100, 125, 75, 100)  # 50 pts, too big to be part of a tight range
TIGHT = (100, 104, 98, 101)  # inside 98-104 (6 pts)


SHORT = dict(min_candles=4, max_candles=12, range_atr_mult=1.5, span_days=False)  # small intraday ranges


def scanner(**kw):
    sc = TightRangeScanner("NIFTY 50", RangeParams(**{"breakout_buffer_pct": 0, **SHORT, **kw}))
    sc.warmup(history())
    return sc


def feed(sc, cs):
    return [e for c in cs for e in sc.on_candle(c)]


class TightRangeTest(unittest.TestCase):
    def test_detects_range_after_min_candles(self):
        sc = scanner()
        evs = feed(sc, candles([WIDE, TIGHT, TIGHT, TIGHT]))
        self.assertEqual(evs, [])
        evs = feed(sc, candles([TIGHT], T0 + timedelta(minutes=60)))
        self.assertEqual([e.kind for e in evs], ["RANGE"])
        self.assertEqual((sc.rng.low, sc.rng.high, sc.rng.candles), (98, 104, 4))

    def test_wide_candles_never_form_a_range(self):
        sc = scanner()
        trend = [(100 + 15 * i, 110 + 15 * i, 90 + 15 * i, 105 + 15 * i) for i in range(10)]
        self.assertEqual(feed(sc, candles(trend)), [])

    def test_range_grows_while_inside(self):
        sc = scanner()
        feed(sc, candles([WIDE] + [TIGHT] * 4 + [(101, 103, 99, 102)] * 2))
        self.assertEqual(sc.rng.candles, 6)

    def test_upside_breakout_on_candle_hits_target(self):
        sc = scanner()
        evs = feed(sc, candles([WIDE] + [TIGHT] * 4 + [(102, 106, 101, 105), (105, 111, 104, 110)]))
        kinds = [e.kind for e in evs]
        self.assertEqual(kinds, ["RANGE", "BREAKOUT", "TARGET"])
        b = evs[1]
        self.assertEqual((b.side, b.price), (UP, 104))
        self.assertEqual(sc.scalp, None)
        self.assertEqual(evs[2].points, 6)  # target = range width

    def test_downside_breakout_hits_stop(self):
        sc = scanner()
        evs = feed(sc, candles([WIDE] + [TIGHT] * 4 + [(99, 100, 96, 97), (97, 105, 96, 104)]))
        self.assertEqual([e.kind for e in evs], ["RANGE", "BREAKOUT", "STOP"])
        self.assertEqual(evs[1].side, DOWN)
        self.assertEqual(evs[2].price, 104)

    def test_ltp_breakout_and_exit(self):
        sc = scanner()
        feed(sc, candles([WIDE] + [TIGHT] * 4))
        ts = T0 + timedelta(minutes=80)
        self.assertEqual(sc.on_price(ts, 103.5), [])
        ev = sc.on_price(ts, 104.5)
        self.assertEqual((ev[0].kind, ev[0].side), ("BREAKOUT", UP))
        ev = sc.on_price(ts + timedelta(minutes=2), 110.6)
        self.assertEqual(ev[0].kind, "TARGET")

    def test_close_mode_needs_close_beyond(self):
        sc = scanner(breakout_on="close")
        evs = feed(sc, candles([WIDE] + [TIGHT] * 4 + [(102, 106, 101, 103)]))
        self.assertNotIn("BREAKOUT", [e.kind for e in evs])
        self.assertIsNotNone(sc.rng)
        self.assertEqual(sc.on_price(T0 + timedelta(minutes=100), 106), [])

    def test_no_alerts_after_cutoff(self):
        sc = scanner(last_alert_time=time(10, 30))
        evs = feed(sc, candles([WIDE] * 2 + [TIGHT] * 4))  # range would end 10:45
        self.assertEqual(evs, [])

    def test_new_range_after_breakout(self):
        sc = scanner()
        day = [WIDE] + [TIGHT] * 4 + [(102, 106, 101, 105), (105, 111, 104, 110)]
        day += [(110, 110.5, 108, 110)] * 4
        evs = feed(sc, candles(day))
        self.assertEqual([e.kind for e in evs], ["RANGE", "BREAKOUT", "TARGET", "RANGE"])
        # the new range starts after the breakout candle, never overlapping the old one
        self.assertEqual(sc.rng.start, T0 + timedelta(minutes=90))

    def test_ranges_do_not_span_days(self):
        sc = scanner()
        feed(sc, candles([TIGHT] * 3))
        next_day = datetime(2026, 9, 2, 9, 15, tzinfo=IST)
        evs = feed(sc, candles([TIGHT], next_day))
        self.assertEqual(evs, [])

    def test_open_scalp_expires_at_session_end(self):
        day = [WIDE] + [TIGHT] * 4 + [(102, 106, 101, 105)] + [(105, 107, 103, 106)] * 19
        evs = [e for _, e in simulate("NIFTY 50", history() + candles(day), RangeParams(breakout_buffer_pct=0, **SHORT))]
        self.assertEqual(evs[-1].kind, "EXPIRED")
        self.assertEqual(evs[-1].points, 2)

    def test_needs_atr_history(self):
        sc = TightRangeScanner("x", RangeParams(**SHORT))
        self.assertEqual(feed(sc, candles([TIGHT] * 6)), [])

    def test_extended_breakout_is_skipped(self):
        sc = scanner()
        feed(sc, candles([WIDE] + [TIGHT] * 4))
        ev = sc.on_price(T0 + timedelta(minutes=80), 120)  # 16 pts past the edge, ATR ~20
        self.assertEqual(ev[0].kind, "SKIP")
        self.assertIsNone(sc.scalp)
        self.assertIn("MISSED", fmt_event(ev[0], sc.p))

    def test_default_needs_30_candles_across_days(self):
        p = RangeParams(breakout_buffer_pct=0)
        self.assertGreaterEqual(p.min_candles, 30)
        sc = TightRangeScanner("NIFTY 50", p)
        prev = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
        sc.warmup(candles([(300, 310, 290, 300)] * 25, prev))  # ATR 20 (limit 80 pts), far from today's prices
        quiet = (100, 120, 70, 100)  # 50 pts wide, all inside 70-120
        day1 = candles([quiet] * 25, datetime(2026, 9, 1, 9, 15, tzinfo=IST))
        self.assertEqual(feed(sc, day1), [])  # 25 candles is not enough
        day2 = candles([quiet] * 5, datetime(2026, 9, 2, 9, 15, tzinfo=IST))
        evs = feed(sc, day2)
        self.assertEqual([e.kind for e in evs], ["RANGE"])  # alert on the 30th candle
        self.assertEqual(sc.rng.candles, 30)
        self.assertEqual(sc.rng.start.date().isoformat(), "2026-09-01")
        self.assertIn("01 Sep 09:15 - 02 Sep 10:30", fmt_event(evs[0], p))

    def test_gap_through_range_uses_open(self):
        sc = scanner(span_days=True)
        feed(sc, candles([WIDE] + [TIGHT] * 4))
        nxt = datetime(2026, 9, 2, 9, 15, tzinfo=IST)
        evs = feed(sc, candles([(130, 131, 129, 130)], nxt))  # gaps 26 pts over the 104 high
        self.assertEqual(evs[0].kind, "SKIP")  # too far past the edge to chase

    def test_alert_formatting(self):
        sc = scanner()
        evs = feed(sc, candles([WIDE] + [TIGHT] * 4 + [(102, 106, 101, 105), (105, 111, 104, 110)]))
        msgs = [fmt_event(e, sc.p) for e in evs]
        self.assertIn("TIGHT RANGE NIFTY 50", msgs[0])
        self.assertIn("BREAKOUT UP NIFTY 50", msgs[1])
        self.assertIn("<b>SL:</b> 98.00", msgs[1])
        self.assertIn("TARGET HIT", msgs[2])


if __name__ == "__main__":
    unittest.main()
