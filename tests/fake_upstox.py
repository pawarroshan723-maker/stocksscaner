"""
A stand-in for the Upstox HTTP API.

Mimics the documented V3 contracts:

  GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}
  GET /v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}

      * instrument_key is URL-encoded ("NSE_EQ%7CINE...")
      * response: {"status":"success","data":{"candles":[[ts,o,h,l,c,v,oi], ...]}}
      * candles are returned NEWEST FIRST  (as the real API does)
      * candle timestamp = candle START time, ISO-8601 with +05:30 offset

The fake server holds a ground-truth daily candle series and answers range
queries against it, so tests can assert on chunking, gaps and cache behaviour.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import pandas as pd

IST_OFFSET = "+05:30"
IST = timezone(timedelta(hours=5, minutes=30))


def today_ist():
    """Today's date in IST — the same clock the scanner uses.

    The scanner resolves "today" against Asia/Kolkata, not the machine's local
    zone. Tests must do the same or they drift a day out of step with it
    whenever the host timezone is behind IST.
    """
    return datetime.now(IST).date()


class FakeResponse:
    def __init__(self, status_code, payload, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


class FakeUpstox:
    """Drop-in replacement for the `requests` module inside the scanner."""

    def __init__(self, daily: pd.DataFrame | None = None,
                 intraday: dict | None = None,
                 status_override: dict | None = None,
                 fail_codes: list | None = None):
        """
        daily          : DataFrame with ts/open/high/low/close/vol/oi (IST tz-aware)
        intraday       : {(unit, interval): DataFrame} for the intraday endpoint
        status_override: {call_index: status_code} — force a given HTTP status
        fail_codes     : list of status codes to return in order, then succeed
        """
        self.daily = daily
        self.intraday = intraday or {}
        self.calls: list[str] = []          # every URL requested, in order
        self.status_override = status_override or {}
        self.fail_codes = list(fail_codes or [])
        self.chunk_sizes: list[int] = []    # rows returned per historical call

    # ── requests-compatible surface ──────────────────────────
    def get(self, url, headers=None, timeout=None, **kw):
        idx = len(self.calls)
        self.calls.append(url)

        if idx in self.status_override:
            return FakeResponse(self.status_override[idx], {}, "forced")
        if self.fail_codes:
            code = self.fail_codes.pop(0)
            return FakeResponse(code, {"status": "error"}, "HTTP %d" % code)

        path = url.split("api.upstox.com", 1)[1]
        parts = [unquote(p) for p in path.strip("/").split("/")]

        # /v3/historical-candle/intraday/{key}/{unit}/{interval}
        if parts[:3] == ["v3", "historical-candle", "intraday"]:
            unit, interval = parts[4], parts[5]
            df = self.intraday.get((unit, interval))
            return FakeResponse(200, {"status": "success",
                                      "data": {"candles": _to_candles(df)}})

        # /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
        if parts[:2] == ["v3", "historical-candle"]:
            # /v3/historical-candle/{key}/{unit}/{interval}/{to_date}/{from_date}
            unit, interval, to_d, from_d = parts[3], parts[4], parts[5], parts[6]
            to_d = date.fromisoformat(to_d)
            from_d = date.fromisoformat(from_d)
            if from_d > to_d:
                # UDAPI1015
                return FakeResponse(400, {"status": "error",
                                          "errors": [{"errorCode": "UDAPI1015"}]},
                                    "to_date must be >= from_date")
            df = self._slice(unit, interval, from_d, to_d)
            rows = _to_candles(df)
            self.chunk_sizes.append(len(rows))
            return FakeResponse(200, {"status": "success",
                                      "data": {"candles": rows}})

        return FakeResponse(404, {"status": "error"}, "not found")

    # ── data helpers ─────────────────────────────────────────
    def _slice(self, unit, interval, from_d, to_d):
        if self.daily is None or self.daily.empty:
            return None
        if unit != "days":
            # intraday history: synthesise from the daily frame
            return None
        d = self.daily
        mask = (d["ts"].dt.date >= from_d) & (d["ts"].dt.date <= to_d)
        return d.loc[mask]


def _to_candles(df):
    if df is None or len(df) == 0:
        return []
    out = []
    # newest first, like the real API
    for row in df.sort_values("ts", ascending=False).itertuples(index=False):
        out.append([
            row.ts.strftime("%Y-%m-%dT%H:%M:%S") + IST_OFFSET,
            float(row.open), float(row.high), float(row.low), float(row.close),
            float(row.vol), float(getattr(row, "oi", 0.0)),
        ])
    return out


def make_daily_series(n=400, end=None, seed=3, start_price=1000.0, freq="B"):
    """Business-day OHLCV ending on `end` (a date), tz-aware IST."""
    import numpy as np
    end = end or (today_ist() - timedelta(days=1))
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    steps = rng.normal(0.05, 1.2, n)
    close = start_price * np.exp(np.cumsum(steps) / 100.0)
    high = close * (1 + np.abs(rng.normal(0, .006, n)))
    low = close * (1 - np.abs(rng.normal(0, .006, n)))
    op = close * (1 + rng.normal(0, .004, n))
    return pd.DataFrame({
        "ts": pd.DatetimeIndex(idx).tz_localize("Asia/Kolkata"),
        "open": op, "high": np.maximum.reduce([high, close, op]),
        "low": np.minimum.reduce([low, close, op]), "close": close,
        "vol": np.abs(rng.normal(1e6, 2e5, n)) + 1000, "oi": 0.0,
    }).reset_index(drop=True)
