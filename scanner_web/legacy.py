"""Compatibility adapter for the existing, tested Upstox scanner engine.

The web application has its own storage and orchestration.  Until the indicator
engine is independently migrated, this adapter deliberately reuses the legacy
calculations instead of silently replacing their formulas.
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEFRAMES = ("5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH")


class ScanError(Exception):
    """A safe, user-facing scan failure."""


@lru_cache(maxsize=1)
def engine():
    spec = importlib.util.spec_from_file_location("scanner_legacy_engine", ROOT / "master-scanner-pro.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def catalog():
    return [{"symbol": name, "sector": sector, "key": key}
            for key, (name, sector) in engine().SYMBOL_MAP_FULL.items()]


def read_token(token_file: Path) -> str | None:
    """Read a token from the environment or the existing local token file.

    Tokens are never returned to the browser or included in error messages.
    """
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        import json
        value = json.loads(token_file.read_text(encoding="utf-8"))
        token = value.get("access_token", "") if isinstance(value, dict) else ""
        return token.strip() if isinstance(token, str) and token.strip() else None
    except (OSError, ValueError):
        return None


def scan(symbol: str, tf: str, token: str, cache_path: Path):
    """Fetch one symbol/timeframe and return a stored snapshot plus chart bars."""
    m = engine()
    found = next((item for item in catalog() if item["symbol"] == symbol), None)
    if not found or tf not in m.TF_CONFIG:
        raise ScanError("Unknown symbol or timeframe")
    # The legacy adapter writes its candle cache to the new app's isolated DB,
    # never to the original master_scanner.db.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    m.DB_FILE = str(cache_path)
    m.DATA_FILE = str(cache_path)
    m.start_scan_pass()
    unit, interval, lookback = m.TF_CONFIG[tf]
    frame = m.fetch_candles(found["key"], unit, interval, lookback,
                            m.make_headers(token), verbose=False, tf_name=tf)
    minimum = m.TF_MIN_CANDLES.get(tf, 60)
    if frame.empty or len(frame) < minimum:
        raise ScanError("Not enough candles returned for this timeframe; no snapshot was saved.")
    frame = m.calculate_indicators(frame, tf)
    signal, atr_pct, volume, oi, reason, components, _ = m.detect_signal(frame, tf)
    support, resistance = m.calc_support_resistance(frame, m._SR_LOOKBACK.get(tf, 30))
    targets = m.get_price_targets(frame, signal, tf)
    last, previous = frame.iloc[-1], frame.iloc[-2]
    price = round(m.safe_float(last.get("close")), 2)
    if not math.isfinite(price) or price <= 0:
        raise ScanError("The provider returned an invalid closing price; no snapshot was saved.")
    atr = round(m.safe_float(last.get("atr")), 2)
    hist, prev_hist = m.safe_float(last.get("macd_hist")), m.safe_float(previous.get("macd_hist"))
    macd = "BULL_CROSS" if hist > 0 >= prev_hist else "BEAR_CROSS" if hist < 0 <= prev_hist else ""
    target, stop = targets.get("target1"), targets.get("stop")
    rr = round(abs(target - price) / abs(price - stop), 2) if target and stop and price != stop else 0
    stamp = last["ts"]
    candle_date = stamp.strftime("%Y-%m-%d %H:%M") if tf in m.TIMEFRAMES_INTRADAY else stamp.strftime("%Y-%m-%d")
    entry = {
        "price": price, "signal": signal, "atr": atr, "atr_pct": atr_pct,
        "volume": volume, "oi": oi, "rsi": round(m.safe_float(last.get("rsi")), 1),
        "rel_vol": round(m.safe_float(last.get("rel_vol")), 2),
        "trend_strength": m.trend_strength(components), "reason": reason,
        "support": support, "resistance": resistance, "targets": targets,
        "risk_reward": rr, "macd_cross": macd,
        "st_direction": int(m.safe_float(last.get("st_dir"))),
        "ema_alignment": m.ema_alignment_label(components),
        "candle_patterns": m.detect_candlestick_patterns(frame),
        "rsi_divergence": m.calc_rsi_divergence(frame, tf=tf),
        # The legacy retrospective retest detector needs an independent audit;
        # the separate forward-looking conditional rules do not use its flags.
        "updated": datetime.now(m.IST).strftime("%Y-%m-%d %H:%M"),
        "candle_date": candle_date,
    }
    entry["composite_score"] = (m.calc_composite_score({tf: entry}, [tf], target_tf=tf)
                                if signal in ("BREAKOUT", "BREAKDOWN") else 0)
    bars = []
    for row in frame.tail(60).itertuples(index=False):
        close = m.safe_float(getattr(row, "close", 0))
        if math.isfinite(close) and close > 0:
            bars.append({"time": row.ts.strftime("%Y-%m-%d %H:%M"), "close": round(close, 2)})
    return entry, bars
