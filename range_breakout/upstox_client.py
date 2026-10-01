"""Thin wrapper over the Upstox REST API (v2 auth, v3 candles/quotes/orders)."""
import logging
from datetime import datetime
from urllib.parse import quote, urlencode

import requests

from .strategy import Candle, IST

log = logging.getLogger(__name__)

API = "https://api.upstox.com"
HFT = "https://api-hft.upstox.com"


class TokenExpired(Exception):
    pass


def login_url(api_key, redirect_uri):
    qs = urlencode({"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri})
    return f"{API}/v2/login/authorization/dialog?{qs}"


def exchange_code(api_key, api_secret, redirect_uri, code):
    resp = requests.post(
        f"{API}/v2/login/authorization/token",
        headers={"Accept": "application/json"},
        data={
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _parse_candles(rows):
    candles = [
        Candle(
            ts=datetime.fromisoformat(r[0]).astimezone(IST),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[5] or 0),
        )
        for r in rows
    ]
    return sorted(candles, key=lambda c: c.ts)  # API returns newest first


class Upstox:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        )

    def _get(self, url, **params):
        resp = self.session.get(url, params=params, timeout=15)
        if resp.status_code == 401:
            raise TokenExpired("Upstox token rejected (401); generate a new one")
        resp.raise_for_status()
        return resp.json()["data"]

    def profile(self):
        return self._get(f"{API}/v2/user/profile")

    def intraday_candles(self, instrument_key, minutes):
        key = quote(instrument_key, safe="")
        data = self._get(f"{API}/v3/historical-candle/intraday/{key}/minutes/{minutes}")
        return _parse_candles(data["candles"])

    def historical_candles(self, instrument_key, minutes, from_date, to_date):
        key = quote(instrument_key, safe="")
        data = self._get(
            f"{API}/v3/historical-candle/{key}/minutes/{minutes}/{to_date}/{from_date}"
        )
        return _parse_candles(data["candles"])

    def ltp(self, instrument_key):
        data = self._get(f"{API}/v3/market-quote/ltp", instrument_key=instrument_key)
        # response is keyed "EXCHANGE:SYMBOL"; we only ever ask for one instrument
        return float(next(iter(data.values()))["last_price"])

    def place_market_order(self, instrument_key, side, quantity, tag="range-breakout"):
        body = {
            "quantity": quantity,
            "product": "I",
            "validity": "DAY",
            "price": 0,
            "tag": tag,
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": True,
        }
        resp = self.session.post(f"{HFT}/v3/order/place", json=body, timeout=15)
        if resp.status_code == 401:
            raise TokenExpired("Upstox token rejected (401); generate a new one")
        resp.raise_for_status()
        return resp.json()["data"]
