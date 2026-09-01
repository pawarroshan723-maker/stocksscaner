"""
Smoke tests for every screen/export.

The scanner is 90% presentation code, and a single KeyError in a view kills the
whole session.  These tests run every view against realistic scan data (and
against empty data) and fail on any unhandled exception.
"""
import io
from contextlib import redirect_stdout
from datetime import date, timedelta

import pandas as pd
import pytest

from tests.fake_upstox import FakeUpstox, make_daily_series

ALL_TFS = ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"]
SWING = ["DAY", "WEEK", "MONTH"]
SYMS = {"K1": "ALPHA", "K2": "BETA", "K3": "GAMMA", "K4": "DELTA"}


@pytest.fixture
def api(mod, monkeypatch):
    fake = FakeUpstox()
    monkeypatch.setattr(mod, "requests", fake, raising=False)
    return fake


@pytest.fixture
def scanned(mod, api, monkeypatch, tmp_path):
    """Run a real scan against the fake API and return the populated data."""
    monkeypatch.setattr(mod, "SYMBOL_MAP", dict(SYMS), raising=False)
    monkeypatch.setattr(mod, "SECTOR_MAP",
                        {v: s for v, s in zip(SYMS.values(),
                                              ["IT", "BANK", "AUTO", "IT"])},
                        raising=False)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.chdir(tmp_path)

    today = date.today()
    api.daily = make_daily_series(n=500, end=today - timedelta(days=1), seed=42)
    live = pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [101.0], "high": [104.0], "low": [100.0], "close": [103.0],
        "vol": [98765.0], "oi": [0.0]})
    api.intraday = {("days", "1"): live,
                    ("minutes", "5"): live, ("minutes", "15"): live,
                    ("hours", "1"): live}
    data = mod.auto_scan({}, SWING)
    # give the watchlist something to show
    mod.toggle_watchlist("ALPHA")
    return data


def run(fn, *a, **kw):
    """Execute a view, capturing stdout; fail on any exception."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


# ── every view, populated data ────────────────────────────────
def test_dashboard(mod, scanned):
    out = run(mod.dashboard, scanned, SWING)
    assert "SYMBOL" in out and "ALPHA" in out


def test_dashboard_with_six_timeframes(mod, scanned):
    """The dashboard must render more than the first 3 TF columns."""
    out = run(mod.dashboard, scanned, ALL_TFS)
    assert "5MIN" in out


def test_view_detail(mod, scanned):
    out = run(mod.view_detail, "ALPHA", scanned)
    assert "ALPHA" in out and "TRADE PLAN" in out


def test_summary_view(mod, scanned):
    out = run(mod.summary_view, "ALPHA", scanned)
    assert "VERDICT" in out


def test_sector_view(mod, scanned):
    out = run(mod.sector_view, scanned)
    assert "IT" in out


def test_filter_view(mod, scanned):
    for sig in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE"):
        run(mod.filter_view, scanned, sig, SWING)


def test_statistics_view(mod, scanned):
    out = run(mod.statistics_view, scanned)
    assert "CONFLUENCE" in out


def test_alert_scanner(mod, scanned):
    run(mod.alert_scanner, scanned)


def test_history_view(mod, scanned):
    run(mod.history_view, "ALPHA", "DAY")


def test_best_setups_view(mod, scanned):
    run(mod.best_setups_view, scanned, SWING)


def test_watchlist_view(mod, scanned):
    out = run(mod.watchlist_view, scanned, SWING)
    assert "ALPHA" in out


def test_candle_pattern_view(mod, scanned):
    run(mod.candle_pattern_view, scanned, SWING)


def test_next_day_gap_view(mod, scanned):
    out = run(mod.next_day_gap_view, scanned)
    assert "GAP" in out


def test_heatmap_view(mod, scanned):
    run(mod.heatmap_view, scanned, SWING)
    run(mod.heatmap_view, scanned, ALL_TFS)


def test_gap_scanner_view(mod, scanned):
    run(mod.gap_scanner_view, scanned)


def test_momentum_screener(mod, scanned):
    run(mod.momentum_screener, scanned, SWING)


def test_trend_strength_view(mod, scanned):
    run(mod.trend_strength_view, scanned, SWING)


def test_volume_profile_view(mod, scanned, api, monkeypatch):
    answers = iter(["ALPHA", "DAY", "60", "24"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers, ""))
    out = run(mod.volume_profile_view, scanned, SWING)
    assert "POC" in out


# ── exports ───────────────────────────────────────────────────
def test_export_csv(mod, scanned, tmp_path):
    out = run(mod.export_csv, scanned, SWING)
    assert "CSV saved" in out
    files = list(tmp_path.glob("master_export_*.csv"))
    assert files and files[0].stat().st_size > 100
    head = files[0].read_text().splitlines()[0]
    assert "symbol" in head and "composite_score" in head


def test_export_report(mod, scanned, tmp_path):
    run(mod.export_report, scanned)
    assert list(tmp_path.glob("master_report_*.txt"))


def test_export_html(mod, scanned, tmp_path):
    run(mod.export_html, scanned, SWING)
    files = list(tmp_path.glob("master_report_*.html"))
    assert files
    html = files[0].read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_export_html_escapes_user_text(mod, scanned, tmp_path):
    """A note containing markup must not be injected raw into the report."""
    mod.save_note("ALPHA", "<script>alert(1)</script> & <b>boom</b>")
    for tf in SWING:
        if tf in scanned["ALPHA"]:
            scanned["ALPHA"][tf]["note"] = "<script>alert(1)</script>"
    run(mod.export_html, scanned, SWING)
    html = next(tmp_path.glob("master_report_*.html")).read_text()
    assert "<script>alert(1)</script>" not in html


# ── empty / unscanned data must never crash ───────────────────
def test_views_survive_empty_data(mod, monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.chdir(tmp_path)
    empty = {}
    run(mod.dashboard, empty, SWING)
    run(mod.sector_view, empty)
    run(mod.statistics_view, empty)
    run(mod.best_setups_view, empty, SWING)
    run(mod.watchlist_view, empty, SWING)
    run(mod.candle_pattern_view, empty, SWING)
    run(mod.heatmap_view, empty, SWING)
    run(mod.momentum_screener, empty, SWING)
    run(mod.trend_strength_view, empty, SWING)
    for sig in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE"):
        run(mod.filter_view, empty, sig, SWING)


def test_view_detail_survives_unscanned_symbol(mod, monkeypatch):
    """A symbol with no price data anywhere must still render."""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    blank = {"GHOST": {tf: mod.empty_entry() for tf in ALL_TFS}}
    blank["GHOST"]["symbol"] = "GHOST"
    out = run(mod.view_detail, "GHOST", blank)
    assert "GHOST" in out


def test_generate_stock_summary_handles_missing_fields(mod):
    out = mod.generate_stock_summary("GHOST", {"DAY": mod.empty_entry()})
    assert out["verdict"] in ("BUY", "SELL", "HOLD", "AVOID")
    assert isinstance(out["buy_reasons"], list)


def test_generate_stock_summary_uses_active_tfs(mod, scanned):
    """Confluence must follow the TFs the user actually enabled."""
    a = mod.generate_stock_summary("ALPHA", scanned["ALPHA"], SWING)
    b = mod.generate_stock_summary("ALPHA", scanned["ALPHA"], ["DAY"])
    assert isinstance(a["verdict"], str) and isinstance(b["verdict"], str)


# ── helpers ───────────────────────────────────────────────────
def test_score_tf_key_falls_back_gracefully(mod):
    entry = {"symbol": "X", "WEEK": {"price": 0, "signal": "NONE"}}
    assert mod._score_tf_key(entry, SWING) == "WEEK"
    assert mod._score_tf_key({"symbol": "X"}, SWING) == "DAY"


def test_confluence_label_covers_wide_scores():
    m = pytest.importorskip("master_scanner_pro")
    for s in range(-6, 7):
        assert "[" in m.confluence_label(s)


def test_next_day_gap_score_bounds(mod, scanned):
    for sym, sd in scanned.items():
        score, bias, facts = mod.calc_next_day_gap_score(sd)
        assert 0 <= score <= 100
        assert bias in ("GAP_UP", "NEUTRAL", "GAP_DOWN")
        assert isinstance(facts, list)


def test_add_custom_symbol_validates_key(mod, monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    answers = iter(["NOT_A_KEY", "SYM", "IT"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers, ""))
    before = len(mod.SYMBOL_MAP)
    run(mod.add_custom_symbol)
    assert len(mod.SYMBOL_MAP) == before, "malformed instrument key accepted"


def test_add_custom_symbol_accepts_valid_key(mod, monkeypatch):
    answers = iter(["NSE_EQ|INE123456789", "NEWSYM", "IT"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers, ""))
    before = len(mod.SYMBOL_MAP)
    run(mod.add_custom_symbol)
    assert len(mod.SYMBOL_MAP) == before + 1
