"""Telegram message formatting for the range scanner (HTML, same style as the ORB alerts)."""
from datetime import timedelta
from html import escape

from .strategy import UP, span_label

ICON = {"TARGET": "✅", "STOP": "❌", "EXPIRED": "⏹️"}


def fmt_range(ev, p) -> str:
    r = ev.rng
    end = r.end + timedelta(minutes=p.candle_minutes)
    return (
        f"🟡 <b>TIGHT RANGE {escape(ev.name)}</b>\n"
        f"<b>High:</b> {r.high:.2f}  <b>Low:</b> {r.low:.2f}\n"
        f"Width {r.width:.2f} pts ({r.width / r.high * 100:.2f}%) | ATR {r.atr:.2f}\n"
        + (f"Narrower than {100 - r.pctile:.0f}% of {p.min_candles}-candle ranges in the last "
           f"{p.lookback_days} sessions\n" if p.tight_percentile else "")
        + f"<i>{r.candles}x{p.candle_minutes}m candles, {span_label(r.start, end)}. "
        f"Watching for a break either way.</i>"
    )


def fmt_breakout(ev, p) -> str:
    s, r = ev.scalp, ev.rng
    up = s.side == UP
    return (
        f"{'🟢' if up else '🔴'} <b>BREAKOUT {'UP' if up else 'DOWN'} {escape(ev.name)}</b>\n"
        f"<b>Entry:</b> {s.entry:.2f}\n"
        f"<b>SL:</b> {s.stop:.2f}  (risk {abs(s.entry - s.stop):.2f} pts)\n"
        f"<b>Target:</b> {s.target:.2f}  ({p.target_r:g}R)\n"
        f"<i>Range {r.low:.2f} - {r.high:.2f} ({r.candles}x{p.candle_minutes}m) | "
        f"{s.ts:%H:%M}</i>"
    )


def fmt_skip(ev) -> str:
    r = ev.rng
    return (f"⚪ <b>SKIPPED {escape(ev.name)}</b> broke {'UP' if ev.side == UP else 'DOWN'} of "
            f"{r.low:.2f} - {r.high:.2f}, but {escape(ev.reason)}")


def fmt_exit(ev) -> str:
    s = ev.scalp
    label = {"TARGET": "TARGET HIT", "STOP": "SL HIT", "EXPIRED": "EOD CLOSE"}[ev.kind]
    return (
        f"{ICON[ev.kind]} <b>{label} {escape(ev.name)}</b> {'UP' if s.side == UP else 'DOWN'} @ {ev.price:.2f}\n"
        f"Entry {s.entry:.2f} | {ev.points:+.2f} pts"
    )


def fmt_event(ev, p) -> str:
    if ev.kind == "RANGE":
        return fmt_range(ev, p)
    if ev.kind == "BREAKOUT":
        return fmt_breakout(ev, p)
    if ev.kind == "SKIP":
        return fmt_skip(ev)
    return fmt_exit(ev)
