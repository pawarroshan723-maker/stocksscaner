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


# ═══════════════════════════════════════════════════════════════
#  The TF that _score_tf_key PICKS is a corrupt non-dict
#  (previous tests always kept one healthy dict-valued TF around,
#   so the pick never landed on the corrupt row)
# ═══════════════════════════════════════════════════════════════

_VIEWS_ON_SYM = [
    ("dashboard",        lambda mod, d: mod.dashboard(d, ["DAY", "WEEK"])),
    ("view_detail",      lambda mod, d: mod.view_detail("ALPHA", d)),
    ("summary_view",     lambda mod, d: mod.summary_view("ALPHA", d, ["DAY", "WEEK"])),
    ("gen_summary",      lambda mod, d: mod.generate_stock_summary("ALPHA", d)),
    ("best_setups",      lambda mod, d: mod.best_setups_view(d, ["DAY", "WEEK"])),
    ("watchlist_view",   lambda mod, d: mod.watchlist_view(d, ["DAY", "WEEK"])),
    ("candle_pattern",   lambda mod, d: mod.candle_pattern_view(d, ["DAY", "WEEK"])),
    ("momentum",         lambda mod, d: mod.momentum_screener(d, ["DAY", "WEEK"])),
    ("trend_strength",   lambda mod, d: mod.trend_strength_view(d, ["DAY", "WEEK"])),
    ("next_day_gap",     lambda mod, d: mod.next_day_gap_view(d)),
    ("heatmap",          lambda mod, d: mod.heatmap_view(d, ["DAY", "WEEK"])),
    ("heatmap_no_day",   lambda mod, d: mod.heatmap_view(d, ["WEEK", "MONTH"])),
    ("statistics",       lambda mod, d: mod.statistics_view(d)),
    ("gap_scanner",      lambda mod, d: mod.gap_scanner_view(d)),
    ("filter",           lambda mod, d: mod.filter_view(d, "BREAKOUT", ["DAY", "WEEK"])),
    ("alert",            lambda mod, d: mod.alert_scanner(d, ["DAY", "WEEK"])),
    ("sector",           lambda mod, d: mod.sector_view(d)),
    ("export_csv",       lambda mod, d: mod.export_csv(d, ["DAY", "WEEK"])),
    ("export_report",    lambda mod, d: mod.export_report(d, ["DAY", "WEEK"])),
    ("export_html",      lambda mod, d: mod.export_html(d, ["DAY", "WEEK"])),
    ("volume_profile",   lambda mod, d: mod.volume_profile_view(d, ["DAY", "WEEK"])),
]


@pytest.mark.parametrize("bad", _NON_DICT)
@pytest.mark.parametrize("plant", ["only_week", "only_day"])
def test_views_survive_when_the_picked_entry_is_non_dict(mod, bad, plant):
    """The picked TF's value can itself be the corrupt one.

    Regression: every view did `entry.get(tf_key, {}).get(...)` — `.get(...,
    {})` only guards a MISSING key, so a symbol whose only TF row is a JSON
    scalar (42 / "oops" / None / []) raised AttributeError in the dashboard
    sort, best setups, watchlist, candle, momentum, trend, gap, summary,
    heatmap (sector counts), exports and the RSI-divergence menu screen.
    """
    if plant == "only_week":
        data = {"ALPHA": {"symbol": "ALPHA", "WEEK": bad}}
    else:
        data = {"ALPHA": {"symbol": "ALPHA", "DAY": bad}}
    mod.toggle_watchlist("ALPHA")   # watchlist_view only reads starred symbols
    for name, fn in _VIEWS_ON_SYM:
        try:
            fn(mod, data)
        except Exception as exc:
            pytest.fail("{} crashed on {!r}: {!r}".format(name, bad, exc))


def test_divergence_menu_screen_survives_junk_price(mod, capsys):
    """Menu option D printed int(price) raw — 'junk' raised ValueError."""
    e = mod.empty_entry()
    e.update({"price": "junk", "signal": "BREAKOUT",
              "rsi_divergence": {"regular_bull": True}})
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e, "WEEK": "corrupt"}}
    import io
    from contextlib import redirect_stdout
    # Replicate the menu's divergence screen body through the same primitives
    # it uses after the fix: _score_tf_key + as_dict + safe_float.
    tf_key = mod._score_tf_key(data["ALPHA"], ["DAY", "WEEK"])
    entry = mod.as_dict(data["ALPHA"].get(tf_key))
    div_ = mod.as_dict(entry.get("rsi_divergence"))
    assert any(div_.values())
    price = mod.safe_float(entry.get("price", 0))
    buf = io.StringIO()
    with redirect_stdout(buf):
        print("  ₹{:<8}  RSI:{:<5}".format(int(price) if price else 0,
                                           entry.get("rsi", 0)))
    assert "₹0" in buf.getvalue()
    capsys.readouterr()


def test_partial_gap_dict_survives_views(mod):
    """A gap dict missing gap_type used to KeyError in view_detail and
    KeyError on the bucket sort in gap_scanner_view."""
    e = mod.empty_entry()
    e.update({"price": 100.0, "gap": {"gap_pct": 1.5}})
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e}}
    import io
    from contextlib import redirect_stdout
    for fn in (lambda: mod.view_detail("ALPHA", data),
               lambda: mod.gap_scanner_view(data)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn()
            except Exception as exc:
                pytest.fail("partial gap dict crashed: {!r}".format(exc))


def test_junk_numeric_fields_survive_detail_and_exports(mod, tmp_path, monkeypatch):
    """Legacy rows can hold junk strings in numeric fields: view_detail's
    Fib/52W/R:R rendering, export_report's ATR format, export_csv's int()
    on divergence flags and export_html's float()/raw interpolation."""
    e = mod.empty_entry()
    e.update({
        "price": "not-a-number", "atr_pct": "junk", "risk_reward": "lots",
        "composite_score": "high", "signal": "BREAKOUT",
        "rsi_divergence": {"regular_bull": "yes", "regular_bear": True},
        "fibonacci": {"fib_38.2": "oops", "fib_50": 100.0, "fib_61.8": 110.0,
                      "sw_high": 120.0, "sw_low": 90.0},
        "52w": {"pct_from_high": "nope", "pct_from_low": None,
                "high_52w": "<b>injected</b>", "low_52w": 90.0},
        "gap": {"gap_type": "GAP_UP", "gap_pct": "x", "gap_filled": "yes",
                "gap_rs": 5, "prev_close": 100, "today_open": 105},
    })
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e}}
    mod.toggle_watchlist("ALPHA")
    monkeypatch.chdir(tmp_path)
    import io
    from contextlib import redirect_stdout
    calls = [
        ("view_detail",    lambda: mod.view_detail("ALPHA", data)),
        ("watchlist",      lambda: mod.watchlist_view(data, ["DAY"])),
        ("gap_scanner",    lambda: mod.gap_scanner_view(data)),
        ("export_csv",     lambda: mod.export_csv(data, ["DAY"])),
        ("export_report",  lambda: mod.export_report(data, ["DAY"])),
        ("export_html",    lambda: mod.export_html(data, ["DAY"])),
    ]
    for name, fn in calls:
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn()
            except Exception as exc:
                pytest.fail("{} crashed on junk numerics: {!r}".format(name, exc))
    # the HTML report must not contain the raw injected markup
    htmls = list(tmp_path.glob("*.html"))
    assert htmls, "no HTML written"
    body = htmls[0].read_text(encoding="utf-8")
    assert "<b>injected</b>" not in body
    assert "&lt;b&gt;injected&lt;/b&gt;" in body


def test_all_timeframes_claim_requires_unanimous_vote(mod):
    """generate_stock_summary printed 'ALL 3 active timeframes agree' on
    2-of-3 agreement (conf >= len(tfs)-1) — a false claim."""
    def mk(sig, **kw):
        e = mod.empty_entry()
        e.update({"signal": sig, "price": 100.0, "updated": "2026-09-02 10:00",
                  **kw})
        return e

    split = {"symbol": "X",
             "DAY": mk("BREAKOUT"), "WEEK": mk("BREAKOUT"),
             "MONTH": mk("NONE")}
    s = mod.generate_stock_summary("X", split, ["DAY", "WEEK", "MONTH"])
    claims = [r for r in (s["buy_reasons"] + s["sell_reasons"])
              if r.startswith("ALL ")]
    assert not claims, claims

    unani = {"symbol": "X",
             "DAY": mk("BREAKOUT"), "WEEK": mk("BREAKOUT"),
             "MONTH": mk("BREAKOUT")}
    s = mod.generate_stock_summary("X", unani, ["DAY", "WEEK", "MONTH"])
    assert any(r.startswith("ALL 3") for r in s["buy_reasons"])


def test_strong_confluence_alert_detail_counts_votes(mod):
    """With 6 active TFs, 3 BREAKOUT + 3 NONE scored 3 and the alert said
    'All 6 TFs BREAKOUT'."""
    def bo():
        e = mod.empty_entry()
        e.update({"signal": "BREAKOUT", "price": 100.0,
                  "updated": "2026-09-02 10:00", "volume": 80})
        return e

    all6 = ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"]
    sym_data = {"symbol": "X"}
    for tf in all6[:3]:
        sym_data[tf] = bo()
    for tf in all6[3:]:
        sym_data[tf] = mod.empty_entry()      # never scanned → NONE
    alerts = mod.gather_alerts("X", sym_data, all6)
    strong = [a for a in alerts if "STRONG BULL" in a[1]]
    assert strong, alerts
    assert "All 6" not in strong[0][2], strong[0]
    assert "3/6" in strong[0][2], strong[0]

    sym_data2 = {"symbol": "X"}
    for tf in all6:
        sym_data2[tf] = bo()
    alerts2 = mod.gather_alerts("X", sym_data2, all6)
    strong2 = [a for a in alerts2 if "STRONG BULL" in a[1]]
    assert strong2 and "All 6 TFs BREAKOUT" == strong2[0][2], strong2


def test_non_str_text_fields_survive_summary_gap_and_exports(mod, tmp_path, monkeypatch):
    """Legacy rows can hold non-str values in TEXT fields.

    `in ema_alignment` raised TypeError (int not iterable), str+int on
    signal/reason/candle_date/note raised TypeError, and conflict_status
    concatenated a raw int signal into 'ALIGNED → '.
    """
    monkeypatch.chdir(tmp_path)
    import io
    from contextlib import redirect_stdout

    e = mod.empty_entry()
    e.update({"price": 100.0, "signal": 42, "reason": 123, "ema_alignment": 42,
              "candle_date": 42, "note": 42, "macd_cross": 42,
              "rel_vol": 1.5, "updated": "2026-09-02 10:00"})
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e}}

    calls = [
        ("gen_summary",     lambda: mod.generate_stock_summary("ALPHA", data["ALPHA"])),
        ("gap_score",       lambda: mod.calc_next_day_gap_score(data["ALPHA"])),
        ("view_detail",     lambda: mod.view_detail("ALPHA", data)),
        ("summary_view",    lambda: mod.summary_view("ALPHA", data, ["DAY"])),
        ("next_day_gap",    lambda: mod.next_day_gap_view(data)),
        ("dashboard",       lambda: mod.dashboard(data, ["DAY"])),
        ("conflict_status", lambda: str(mod.conflict_status(data["ALPHA"], ["DAY"]))),
        ("alert_scanner",   lambda: mod.alert_scanner(data, ["DAY"])),
        ("export_report",   lambda: mod.export_report(data, ["DAY"])),
        ("export_csv",      lambda: mod.export_csv(data, ["DAY"])),
        ("export_html",     lambda: mod.export_html(data, ["DAY"])),
        ("history_view",    lambda: mod.history_view("ALPHA", "DAY")),
    ]
    for name, fn in calls:
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn()
            except Exception as exc:
                pytest.fail("{} crashed on non-str text fields: {!r}".format(name, exc))

    # conflict_status must return a str even when the only signal is an int
    out = mod.conflict_status(data["ALPHA"], ["DAY"])
    assert isinstance(out, str)


# ═══════════════════════════════════════════════════════════════
#  Unhashable / non-str `signal` values
#  (dict.get(list) raises TypeError: unhashable type — the signal
#   flows into SIGNAL_BIAS/SIGNAL_SHORT/SIGNAL_LABEL/SIG_CELL lookups,
#   set() in conflict_status/gather_alerts, and statistics' count dict)
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_signal", [[1, 2], {"a": 1}, None, 42, 3.14, True])
def test_every_signal_lookup_tolerates_junk_signal(mod, bad_signal, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    import io
    from contextlib import redirect_stdout

    e = mod.empty_entry()
    e.update({"signal": bad_signal, "price": 100.0,
              "updated": "2026-09-02 10:00", "rel_vol": 1.2})
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e, "WEEK": mod.empty_entry()}}

    calls = [
        ("signal_color",   lambda: mod.signal_color(bad_signal)),
        ("sig_str",        lambda: mod.sig_str(bad_signal)),
        ("confluence",     lambda: mod.confluence_score(data["ALPHA"], ["DAY", "WEEK"])),
        ("conflict",       lambda: str(mod.conflict_status(data["ALPHA"], ["DAY", "WEEK"]))),
        ("gather_alerts",  lambda: mod.gather_alerts("ALPHA", data["ALPHA"], ["DAY", "WEEK"])),
        ("statistics",     lambda: mod.statistics_view(data)),
        ("dashboard",      lambda: mod.dashboard(data, ["DAY"])),
        ("view_detail",    lambda: mod.view_detail("ALPHA", data)),
        ("momentum",       lambda: mod.momentum_screener(data, ["DAY"])),
        ("trend_view",     lambda: mod.trend_strength_view(data, ["DAY"])),
        ("watchlist",      lambda: (mod.toggle_watchlist("ALPHA"),
                                    mod.watchlist_view(data, ["DAY"]))),
        ("candle_view",    lambda: mod.candle_pattern_view(data, ["DAY"])),
        ("best_setups",    lambda: mod.best_setups_view(data, ["DAY"])),
        ("heatmap",        lambda: mod.heatmap_view(data, ["DAY"])),
        ("gap_scanner",    lambda: mod.gap_scanner_view(data)),
        ("gap_score",      lambda: mod.calc_next_day_gap_score(data["ALPHA"])),
        ("gen_summary",    lambda: mod.generate_stock_summary("ALPHA", data["ALPHA"])),
        ("export_csv",     lambda: mod.export_csv(data, ["DAY"])),
        ("export_report",  lambda: mod.export_report(data, ["DAY"])),
        ("export_html",    lambda: mod.export_html(data, ["DAY"])),
    ]
    for name, fn in calls:
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn()
            except Exception as exc:
                pytest.fail("signal={!r} crashed {}: {!r}".format(bad_signal, name, exc))


def test_candle_pattern_list_with_non_str_items(mod):
    """`" ".join([1, 2])` raised TypeError — pattern lists from legacy rows
    can contain non-str items, and only *membership* checks are safe."""
    e = mod.empty_entry()
    e.update({"price": 100.0, "signal": "BREAKOUT",
              "candle_patterns": [1, 2, "HAMMER"]})
    data = {"ALPHA": {"symbol": "ALPHA", "DAY": e}}
    import io
    from contextlib import redirect_stdout
    for name, fn in (("candle_view", lambda: mod.candle_pattern_view(data, ["DAY"])),
                     ("export_csv",  lambda: mod.export_csv(data, ["DAY"])),
                     ("export_report", lambda: mod.export_report(data, ["DAY"])),
                     ("view_detail", lambda: mod.view_detail("ALPHA", data)),
                     ("gen_summary", lambda: mod.generate_stock_summary("ALPHA", data["ALPHA"]))):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn()
            except Exception as exc:
                pytest.fail("{} crashed on non-str pattern items: {!r}".format(name, exc))


# ═══════════════════════════════════════════════════════════════
#  select_db_file: loose _DATE_DB_RE + strict strptime
#  (a file named "31-02-2026.db" matched the regex, then the sort key
#   raised ValueError — crashing the app at STARTUP before any screen)
# ═══════════════════════════════════════════════════════════════

def test_select_db_file_survives_impossible_date_names(mod, monkeypatch, tmp_path):
    import io
    from contextlib import redirect_stdout
    for fname in ("31-02-2026.db", "99-99-9999.db", "13-13-2026.db", "01-09-2026.db"):
        (tmp_path / fname).write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "")   # ENTER → keep current
    monkeypatch.setattr(mod, "DB_FILE", "master_scanner.db")
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.select_db_file(silent=True)          # must not raise ValueError
    out = buf.getvalue()
    assert "01-09-2026.db" in out                # valid backup still listed
    assert "31-02-2026.db" not in out            # impossible dates skipped


def test_select_db_file_startup_empty_dir_is_quiet(mod, monkeypatch, tmp_path):
    import io
    from contextlib import redirect_stdout
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.select_db_file(silent=True)
    assert buf.getvalue() == ""                  # silent=True prints nothing


# ═══════════════════════════════════════════════════════════════
#  export_html: custom symbol/sector names are free-text user input
#  (add_custom_symbol) yet the side nav, detail-card id and sector
#  cells interpolated them RAW while every table cell used esc())
# ═══════════════════════════════════════════════════════════════

def test_export_html_escapes_custom_symbol_and_sector_names(mod, monkeypatch, tmp_path):
    import io, glob, os
    from contextlib import redirect_stdout
    monkeypatch.chdir(tmp_path)   # report file lands in tmp, not the repo
    evil_sym = '</div><script>alert(1)</script>'
    e = mod.empty_entry()
    e.update({"price": 100.0, "signal": "BREAKOUT", "composite_score": 70,
              "updated": "2026-09-02 10:00"})
    data = {evil_sym: {"symbol": evil_sym, "DAY": e}}
    monkeypatch.setitem(mod.SECTOR_MAP, evil_sym, '<img src=x onerror=alert(2)>')
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.export_html(data, ["DAY"])
    # export_html prints "✓ HTML saved → <file>"; read the newest report
    files = sorted(glob.glob("master_report_*.html"), key=os.path.getmtime)
    assert files, buf.getvalue()
    html = open(files[-1], encoding="utf-8").read()
    os.remove(files[-1])
    raw_sym = "</div><script>alert(1)</script>"
    raw_sec = "<img src=x onerror=alert(2)>"
    assert raw_sym not in html, "custom symbol interpolated raw into report"
    assert raw_sec not in html, "custom sector interpolated raw into report"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html   # shown, but escaped
    # Nav href is URL-encoded and the card id is HTML-escaped; the decoded
    # fragments are checked against IDs in test_custom_symbol_links_resolve_after_url_decoding.
    assert 'href="#sym-' in html and 'id="sym-' in html


# ═══════════════════════════════════════════════════════════════
#  Stale intraday history detection
#  (Upstox can publish the minutes/hours candles of the latest session
#  later than the daily ones — 5MIN/1HR closes then disagree with DAY
#  with no error anywhere; observed live: ₹1205.7 vs ₹1220.0.)
# ═══════════════════════════════════════════════════════════════

def _stale_entry(mod, price, cdate, signal="BREAKOUT"):
    e = mod.empty_entry()
    e.update({"price": price, "candle_date": cdate, "signal": signal,
              "updated": cdate})
    return e


def _stale_data(mod, day_px=1000.0, day_date="2026-10-28 00:00",
                m5_px=1000.0, m5_date="2026-10-28 15:25"):
    return {"ALPHA": {
        "symbol": "ALPHA",
        "DAY":  _stale_entry(mod, day_px, day_date),
        "5MIN": _stale_entry(mod, m5_px, m5_date),
    }}


def test_stale_warning_when_intraday_close_drifts_from_day(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: False)
    warns = mod._stale_intraday_warnings(
        "ALPHA", _stale_data(mod, m5_px=985.0), ["DAY", "5MIN"])
    assert len(warns) == 1
    assert "5MIN" in warns[0] and "off" in warns[0]


def test_stale_warning_when_intraday_date_lags_day(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: False)
    # price identical, but the minute history ends a session earlier
    warns = mod._stale_intraday_warnings(
        "ALPHA", _stale_data(mod, m5_date="2026-10-27 15:25"),
        ["DAY", "5MIN"])
    assert len(warns) == 1
    assert "history ends 2026-10-27" in warns[0]


def test_no_stale_warning_when_data_agrees(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: False)
    # 0.1% is inside the tolerance; same session date
    assert mod._stale_intraday_warnings(
        "ALPHA", _stale_data(mod, m5_px=999.0), ["DAY", "5MIN"]) == []


def test_no_stale_warning_while_market_open(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: True)
    assert mod._stale_intraday_warnings(
        "ALPHA", _stale_data(mod, m5_px=900.0), ["DAY", "5MIN"]) == []


def test_no_stale_warning_without_day_this_pass(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: False)
    # intraday-only scan (option 2 with 5MIN,1HR): no DAY row to compare
    assert mod._stale_intraday_warnings(
        "ALPHA", _stale_data(mod, m5_px=900.0), ["5MIN"]) == []


def test_resampled_tfs_never_flagged(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_market_open", lambda: False)
    data = _stale_data(mod)
    data["ALPHA"]["WEEK"] = _stale_entry(mod, 500.0, "Wk 2026-10-26")
    data["ALPHA"]["MONTH"] = _stale_entry(mod, 400.0, "Mo 2026-10")
    assert mod._stale_intraday_warnings(
        "ALPHA", data, ["DAY", "5MIN", "WEEK", "MONTH"]) == []


# ═══════════════════════════════════════════════════════════════
#  DB layer: schema memo, truthful save reporting, cache fallback
#  (live log: every op failed "no such table" because _SCHEMAS_READY
#   still listed the path after the file behind it lost its tables,
#   yet the scan announced "✓ Done. Saved to master_scanner.db")
# ═══════════════════════════════════════════════════════════════

def test_schema_self_heals_when_db_file_replaced(mod, tmp_path, monkeypatch):
    """The memo records 'DDL once ran for this PATH', but the FILE behind
    the path can be deleted/recreated empty while the process runs —
    afterwards every query died with 'no such table' for the process's
    whole lifetime."""
    db = tmp_path / "replaced.db"
    monkeypatch.setattr(mod, "DB_FILE", str(db))
    con = mod._db_connect()
    con.close()
    assert str(db) in mod._SCHEMAS_READY
    # the file behind the path vanishes (deleted / replaced by a restore)
    db.unlink()
    con = mod._db_connect()          # must detect and rebuild the schema
    try:
        row = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='scan_data'").fetchone()
    finally:
        con.close()
    assert row, "schema not rebuilt after the DB file was replaced"


def test_save_data_reports_success_and_failure(mod, tmp_path, monkeypatch):
    sample = {"X": {"symbol": "X", "DAY": {"signal": "NONE"}}}
    # isolated_db points DB_FILE at a real file → True
    assert mod.save_data(sample) is True
    # a directory as DB_FILE cannot be opened → False (not swallowed)
    monkeypatch.setattr(mod, "DB_FILE", str(tmp_path))
    assert mod.save_data(sample) is False


def test_cache_reload_failure_keeps_fresh_download(mod, monkeypatch):
    """After downloading the missing range, the code reloaded the cache to
    rebuild the frame; when that reload came back empty the ENTIRE fresh
    download was discarded (observed live: 2500d downloaded, then '1 WEEK
    candle' resampled from the lone live bar)."""
    import pandas as pd
    from tests.fake_upstox import FakeUpstox, make_daily_series
    fake = FakeUpstox(daily=make_daily_series(n=40, seed=11))
    monkeypatch.setattr(mod, "requests", fake, raising=False)
    # simulate a broken cache table: every reload comes back empty
    monkeypatch.setattr(mod, "_cache_load", lambda *a, **k: pd.DataFrame())
    df = mod.fetch_historical_cached("NSE_EQ|INE002A01018", "days", "1",
                                     30, {}, verbose=False)
    assert not df.empty, "fresh download was thrown away when reload failed"
