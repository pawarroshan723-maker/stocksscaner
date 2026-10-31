"""Reject unsupported signals and make reference estimates auditable."""
import re

import pandas as pd
import pytest

from tests.conftest import make_ohlcv


def test_quality_checks_reject_missing_and_undefined_inputs(mod):
    data = mod.calculate_indicators(make_ohlcv(n=300), "DAY")
    assert mod._scan_input_issue(data, "DAY") == ""
    bad = data.copy()
    bad.loc[bad.index[-1], "rsi"] = float("nan")
    assert "rsi" in mod._scan_input_issue(bad, "DAY")
    bad = data.copy()
    bad.loc[bad.index[-20:], "vol"] = 0
    bad.loc[bad.index[-1], "vol_ma20"] = 0
    assert mod._scan_input_issue(bad, "DAY")
    assert "insufficient data" in mod._scan_input_issue(data.tail(10), "DAY")


def test_failed_rescan_invalidates_previous_signal(mod, monkeypatch):
    monkeypatch.setattr(mod, "fetch_candles", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(mod, "load_note", lambda *a: "")
    old = mod.empty_entry()
    old.update(signal="BREAKOUT", price=100, updated="2026-10-29 15:30")
    data = {"TEST": {"DAY": old}}
    mod._scan_one_symbol("key", "TEST", data, ["DAY"], {})
    assert data["TEST"]["DAY"]["signal"] == "NONE"
    assert not mod.is_scanned_entry(data["TEST"]["DAY"])
    assert "scan failed" in data["TEST"]["DAY"]["data_quality"]


@pytest.mark.parametrize("signal,price,t1,stop,expected", [
    ("BREAKDOWN", 2502.6, 2495.54, 2507.3, 1.5),
    ("BREAKOUT", 100, 105, 98, 2.5),
    ("BREAKOUT", 100, 95, 98, None),
    ("BREAKDOWN", 100, 95, 98, None),
    ("BREAKDOWN", 100, 95, 100, None),
    ("NONE", 100, 105, 98, None),
    ("BREAKOUT", 100, None, 98, None),
])
def test_reference_rr_checks_direction_and_levels(mod, signal, price, t1, stop, expected):
    assert mod._reference_rr({"signal": signal, "price": price,
                              "targets": {"target1": t1, "stop": stop}}) == expected


def test_target_sources_match_fallbacks(mod):
    df = make_ohlcv(n=300)
    df["atr"] = 2.0
    # Monotonic values leave no eligible swing points.
    df["high"] = range(101, 401)
    df["low"] = range(99, 399)
    df["close"] = range(100, 400)
    sources = {}
    targets = mod.get_price_targets(df, "BREAKOUT", "DAY", sources=sources)
    assert targets
    assert sources == {"target1": "ATR fallback", "target2": "ATR fallback", "stop": "ATR fallback"}


def test_detail_missing_indicators_and_unscanned_not_fabricated(mod, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    e = {"signal": "BREAKOUT", "price": 100, "updated": "2026-10-29 15:30",
         "targets": {"target1": 95, "stop": 98}, "adx": None, "rsi": None}
    mod.view_detail("TEST", {"TEST": {"DAY": e, "5MIN": mod.empty_entry()}})
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "unavailable — not scanned" in out
    assert "ADX=unavailable" in out
    assert "RSI(14)     : unavailable" in out
    assert "Ref R:R: unavailable" in out
    assert "Rule-based score, not a probability" in out
    assert "Level source: T1=unknown" in out
