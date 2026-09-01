"""
Robustness tests: the scanner must degrade gracefully instead of throwing when
data is missing, stale, or of the wrong shape.

Real-world sources of such data: an interrupted scan, a DB written by an older
version, a TF that failed to download, a symbol removed from the universe.
"""
import io
from contextlib import redirect_stdout

import pytest

ALL_TFS = ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"]
SWING = ["DAY", "WEEK", "MONTH"]


def run(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")


def _sym(mod, **over):
    e = mod.empty_entry()
    e.update(over)
    return e


@pytest.mark.parametrize("bad", [
    None, "", 0, 0.0, [], {}, "NONSENSE", float("nan"),
])
def test_views_tolerate_garbage_scalars(mod, bad):
    entry = {tf: _sym(mod, price=bad, rsi=bad, atr=bad, atr_pct=bad,
                      rel_vol=bad, trend_strength=bad, composite_score=bad,
                      risk_reward=bad, support=bad, resistance=bad,
                      candle_patterns=bad, targets=bad, fibonacci=bad,
                      **({"52w": bad} if bad is None else {}))
             for tf in SWING}
    entry["symbol"] = "WEIRD"
    data = {"WEIRD": entry}
    run(mod.dashboard, data, SWING)
    run(mod.view_detail, "WEIRD", data)
    run(mod.summary_view, "WEIRD", data, SWING)
    run(mod.statistics_view, data)
    run(mod.heatmap_view, data, SWING)
    run(mod.best_setups_view, data, SWING)
    run(mod.candle_pattern_view, data, SWING)
    run(mod.filter_view, data, "BREAKOUT", SWING)
    run(mod.momentum_screener, data, SWING)
    run(mod.trend_strength_view, data, SWING)
    run(mod.gap_scanner_view, data)
    run(mod.next_day_gap_view, data)
    run(mod.sector_view, data)


def test_views_tolerate_missing_tf_keys(mod):
    """A symbol scanned only on WEEK (DAY/WEEK/MONTH requested)."""
    data = {"PARTIAL": {"symbol": "PARTIAL",
                        "WEEK": _sym(mod, signal="BREAKOUT", price=100.0)}}
    run(mod.dashboard, data, SWING)
    run(mod.view_detail, "PARTIAL", data)
    run(mod.heatmap_view, data, SWING)
    run(mod.best_setups_view, data, SWING)
    run(mod.summary_view, "PARTIAL", data, SWING)


def test_views_tolerate_unicode_and_long_symbols(mod):
    long_name = "X" * 60
    data = {long_name: {"symbol": long_name,
                        "DAY": _sym(mod, signal="BREAKOUT", price=10.0)}}
    run(mod.dashboard, data, SWING)
    run(mod.view_detail, long_name, data)
    run(mod.sector_view, data)


def test_indicators_tolerate_tiny_frames(mod):
    import pandas as pd
    for n in (0, 1, 2, 5, 12):
        ts = pd.date_range("2024-01-01", periods=n, tz="Asia/Kolkata")
        df = pd.DataFrame({"ts": ts, "open": 1.0, "high": 2.0, "low": 0.5,
                           "close": 1.5, "vol": 10.0, "oi": 0.0})
        out = mod.calculate_indicators(df, "DAY")
        mod.detect_signal(out, "DAY")
        mod.calc_support_resistance(df)
        mod.calc_fibonacci_levels(df)
        mod.detect_candlestick_patterns(df)
        mod.calc_rsi_divergence(df, tf="DAY")
        mod.calc_volume_profile(df)
        mod.get_price_targets(out, "BREAKOUT", "DAY")
        mod.detect_retest_breakout(df)
        mod.calc_gap(df)


def test_indicators_tolerate_nan_and_zero_prices(mod):
    import numpy as np
    import pandas as pd
    n = 300
    ts = pd.date_range("2024-01-01", periods=n, tz="Asia/Kolkata")
    close = np.linspace(100, 120, n)
    close[150] = np.nan
    df = pd.DataFrame({"ts": ts, "open": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close,
                       "vol": np.where(np.arange(n) == 7, 0, 1000.0), "oi": 0.0})
    out = mod.calculate_indicators(df, "DAY")
    sig, *_ = mod.detect_signal(out, "DAY")
    assert sig in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE")


def test_db_roundtrip_preserves_types(mod):
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": _sym(
        mod, signal="BREAKOUT", price=123.5, candle_patterns=["HAMMER"],
        targets={"target1": 130.0, "target2": 135.0, "stop": 120.0},
        support=[119.0], resistance=[131.0])}}
    mod.save_data(data)
    back = mod.load_data()
    e = back["ALPHA"]["DAY"]
    assert e["price"] == 123.5
    assert e["candle_patterns"] == ["HAMMER"]
    assert e["targets"]["target1"] == 130.0
    assert e["support"] == [119.0]


def test_watchlist_roundtrip(mod):
    mod.toggle_watchlist("ALPHA")
    assert "ALPHA" in mod.load_watchlist()
    mod.toggle_watchlist("ALPHA")
    assert "ALPHA" not in mod.load_watchlist()


def test_notes_roundtrip(mod):
    mod.save_note("ALPHA", "first")
    assert mod.load_note("ALPHA") == "first"
    mod.save_note("ALPHA", "second")       # upsert, not duplicate
    assert mod.load_note("ALPHA") == "second"


def test_history_trim_keeps_latest_100(mod):
    for i in range(120):
        mod.log_history("ALPHA", "DAY",
                        {"signal": "BREAKOUT", "candle_date": "2024-01-%02d"
                         % (i % 28 + 1), "rsi": i})
    h = mod.load_history(limit_per_key=500)["ALPHA_DAY"]
    assert len(h) <= 100


def test_cache_rejects_garbage_rows(mod):
    import pandas as pd
    df = pd.DataFrame({"ts": pd.date_range("2024-01-01", periods=3,
                                           tz="Asia/Kolkata"),
                       "open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
                       "close": [1, 2, 3], "vol": [1, 2, 3]})
    mod._cache_save("K", "DAY", df)          # no 'oi' column
    back = mod._cache_load("K", "DAY")
    assert len(back) == 3 and "oi" in back.columns


def test_safe_float_is_total(mod):
    for v in (None, "", "x", float("inf"), float("-inf"), float("nan"),
              [], {}, 1, 1.5, "2.5", True):
        out = mod.safe_float(v, -9.0)
        assert isinstance(out, float)
        assert out == out          # never NaN (inf is allowed, NaN is not)


# ── a whole TF entry that is not a dict ─────────────────────
_NON_DICT = ["corrupt", 42, 3.14, None, [], True]


def test_non_dict_tf_entry_does_not_crash_views(mod, capsys):
    """sym_data[tf] comes from a JSON blob — it can be any JSON scalar.

    Regression: view_detail / gather_alerts / conflict_status indexed it and
    called .get() straight away, so one bad row took the whole screen down.
    """
    data = {"ALPHA": {"DAY": mod.empty_entry()}}
    for bad in _NON_DICT:
        d = {"ALPHA": {"DAY": mod.empty_entry(), "WEEK": bad}}
        for fn in (lambda dd: mod.view_detail("ALPHA", dd),
                   lambda dd: mod.gather_alerts("ALPHA", dd["ALPHA"]),
                   lambda dd: mod.conflict_status(dd["ALPHA"]),
                   lambda dd: mod.summary_view("ALPHA", dd),
                   lambda dd: mod.generate_stock_summary("ALPHA", dd)):
            try:
                fn(d)
            except Exception as exc:
                pytest.fail("crashed on entry %r: %r" % (bad, exc))
    capsys.readouterr()
    assert isinstance(mod.as_dict(data["ALPHA"]["DAY"]), dict)


def test_non_dict_tf_entry_does_not_crash_scanners(mod, capsys):
    d = {"ALPHA": {"DAY": mod.empty_entry(), "WEEK": "oops"}}
    try:
        mod.alert_scanner(d, ["DAY", "WEEK"])
        mod.dashboard(d, ["DAY", "WEEK"])
        mod.filter_view(d, "BREAKOUT", ["DAY", "WEEK"])
        mod.confluence_score(d["ALPHA"], ["DAY", "WEEK"])
        mod.heatmap_view(d, ["DAY", "WEEK"])
        mod.best_setups_view(d, ["DAY", "WEEK"])
        mod.watchlist_view(d, ["DAY", "WEEK"])
        mod.statistics_view(d)
        mod.candle_pattern_view(d, ["DAY", "WEEK"])
        mod.sector_view(d)
        mod.export_csv(d, ["DAY", "WEEK"])
        mod.export_report(d, ["DAY", "WEEK"])
    except Exception as exc:
        pytest.fail("scanner crashed on a non-dict TF entry: %r" % (exc,))
    capsys.readouterr()


def test_note_write_skips_non_dict_rows(mod, monkeypatch, capsys):
    """edit_note must not TypeError when a TF entry is corrupt."""
    data = {"ALPHA": {"DAY": mod.empty_entry(), "WEEK": "oops"}}
    monkeypatch.setattr("builtins.input", lambda *a: "my note")
    try:
        mod.edit_note("ALPHA", data)
    except Exception as exc:
        pytest.fail("edit_note crashed: %r" % (exc,))
    assert data["ALPHA"]["DAY"]["note"] == "my note"
    assert data["ALPHA"]["WEEK"] == "oops"      # left alone, not clobbered
    capsys.readouterr()
