"""Regression tests for issues found in the 29 Oct 2026 HTML report.

The examples reproduce contradictions in the report, rather than just
checking that the exporter writes an HTML file.
"""

import re
from html.parser import HTMLParser
from urllib.parse import unquote

import pytest


ALL_TFS = ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"]


def _entry(mod, signal="BREAKOUT", **fields):
    result = mod.empty_entry()
    result.update({
        "price": 100.0,
        "signal": signal,
        "rsi": 50.0,
        "rel_vol": 1.0,
        "composite_score": 50,
        "updated": "2026-10-29 16:12",
    })
    result.update(fields)
    return result


def _render(mod, monkeypatch, tmp_path, data, tfs=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args: "")
    mod.export_html(data, tfs or ALL_TFS)
    files = list(tmp_path.glob("master_report_*.html"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


def test_mixed_timeframes_report_both_sides_and_warn(mod):
    # ASIANPAINT: 5MIN/15MIN BO, 1HR sideways, DAY/WEEK/MONTH BD.
    signals = ["BREAKOUT", "BREAKOUT", "SIDEWAYS", "BREAKDOWN", "BREAKDOWN", "BREAKDOWN"]
    data = {tf: _entry(mod, sig) for tf, sig in zip(ALL_TFS, signals)}
    summary = mod.generate_stock_summary("ASIANPAINT", data, ALL_TFS)
    buy = "\n".join(summary["buy_reasons"])
    sell = "\n".join(summary["sell_reasons"])
    cautions = "\n".join(summary["cautions"])

    assert "Bullish signal aligns across multiple timeframes (5MIN, 15MIN)" in buy
    assert "Bearish signal aligns across multiple timeframes (DAY, WEEK, MONTH)" in sell
    assert "Mixed signals: bullish on 5MIN, 15MIN but bearish on DAY, WEEK, MONTH" in cautions
    assert "all horizons" not in (buy + sell).lower()
    assert not any(r.startswith("ALL ") for r in summary["buy_reasons"] + summary["sell_reasons"])


def test_one_opposing_timeframe_still_produces_mixed_warning(mod):
    data = {tf: _entry(mod) for tf in ALL_TFS}
    data["MONTH"] = _entry(mod, "BREAKDOWN")
    s = mod.generate_stock_summary("MIXED", data, ALL_TFS)
    assert any("Mixed signals:" in r and "bearish on MONTH" in r for r in s["cautions"])
    assert not any("all horizons" in r.lower() for r in s["buy_reasons"] + s["sell_reasons"])


@pytest.mark.parametrize("signal,side", [("BREAKOUT", "buy_reasons"), ("BREAKDOWN", "sell_reasons")])
def test_unanimous_confluence_counts_as_only_one_reason(mod, signal, side):
    data = {tf: _entry(mod, signal) for tf in ALL_TFS}
    s = mod.generate_stock_summary("CONSENSUS", data, ALL_TFS)
    reasons = [r for r in s[side]
               if r.startswith("ALL ") or "signal aligns across multiple timeframes" in r]
    assert len(reasons) == 1, reasons
    assert reasons[0].startswith("ALL 6 active timeframes agree")
    assert not any("Mixed signals:" in r for r in s["cautions"])


@pytest.mark.parametrize("signal,side,other", [
    ("BREAKOUT", "buy_reasons", "sell_reasons"),
    ("BREAKDOWN", "sell_reasons", "buy_reasons"),
])
@pytest.mark.parametrize("rel_vol", [1.5, 1.58, 1.99, 2.0])
def test_volume_reason_follows_signal_direction(mod, signal, side, other, rel_vol):
    # HINDUNILVR: DAY breakdown with 1.58x volume appeared as a BUY factor.
    s = mod.generate_stock_summary("VOLUME", {"DAY": _entry(mod, signal, rel_vol=rel_vol)}, ["DAY"])
    assert any("volume" in r.lower() for r in s[side]), s
    assert not any("volume" in r.lower() for r in s[other]), s


@pytest.mark.parametrize("signal", ["NONE", "SIDEWAYS"])
def test_volume_without_direction_is_not_a_buy_or_sell_factor(mod, signal):
    s = mod.generate_stock_summary("VOLUME", {"DAY": _entry(mod, signal, rel_vol=1.7)}, ["DAY"])
    assert not any("volume" in r.lower() for r in s["buy_reasons"] + s["sell_reasons"])


def _gap_factor(mod, value):
    e = _entry(mod, day_high=110.0, day_low=90.0, **{"52w": value})
    score, bias, facts = mod.calc_next_day_gap_score({"DAY": e})
    fact = next((label, pts) for label, pts in facts if label.startswith("52W High:"))
    return score, bias, fact


@pytest.mark.parametrize("pct,label", [
    (-1.0, "Near 52W High (-1.0%)"),
    (-0.3, "Near 52W High (-0.3%)"),
    (0.0, "At 52W High (0.0%)"),
    (0.3, "Above 52W High (+0.3%)"),
])
def test_gap_high_proximity_displays_the_real_sign(mod, pct, label):
    # ADANIPORTS reported -1.0% in its DAY detail but +1.0% in the gap fact.
    _, _, (text, points) = _gap_factor(mod, {"pct_from_high": pct})
    assert label in text
    assert points == 8  # keep the existing weighting for known values


@pytest.mark.parametrize("w52", [{}, {"pct_from_high": None},
                                  {"pct_from_high": "junk"}, {"pct_from_high": float("nan")},
                                  {"pct_from_high": float("inf")}])
def test_missing_52w_data_is_not_invented_as_minus_50_percent(mod, w52):
    _, _, (text, points) = _gap_factor(mod, w52)
    assert "unavailable" in text.lower()
    assert points == 0


def test_gap_range_fallback_identifies_it_as_a_proxy(mod):
    e = _entry(mod, trend_strength=75)  # old rows have no real day-high/low
    _, _, facts = mod.calc_next_day_gap_score({"DAY": e})
    label, pts = facts[0]
    assert pts == 20  # preserve the legacy fallback weighting
    assert "proxy" in label.lower()
    assert "range" in label.lower() and "unavailable" in label.lower()


def test_trade_levels_keep_paisa_even_for_high_price_stocks(mod, monkeypatch, tmp_path):
    # In the upload ULTRACEMCO target 11546.16 was rendered as ₹11,546.
    e = _entry(mod, price=11175.25,
               targets={"target1": 11546.16, "target2": 11917.32, "stop": 10612.03})
    html = _render(mod, monkeypatch, tmp_path, {"ULTRACEMCO": {"DAY": e}})
    assert '<span class="tgt-lbl">Entry</span><span class="mono">₹11,175.25</span>' in html
    assert '<span class="tgt-lbl">Target 1</span><span class="mono gr">₹11,546.16</span>' in html
    assert '<span class="tgt-lbl">Stop Loss</span><span class="mono re">₹10,612.03</span>' in html


def test_dashboard_timeframe_headers_are_not_truncated(mod, monkeypatch, tmp_path):
    html = _render(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod)}})
    dashboard = html.split("All Symbols — DAY Timeframe", 1)[1].split("</thead>", 1)[0]
    for tf in ALL_TFS:
        assert "<th>" + tf + "</th>" in dashboard
    assert "<th>15MI</th>" not in dashboard
    assert "<th>MONT</th>" not in dashboard


def test_mobile_snapshot_scroll_and_grid_sizing_are_present(mod, monkeypatch, tmp_path):
    html = _render(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod)}})
    # The table needs its own scroll container: otherwise .det-card clips its
    # date and price columns on a narrow screen.
    assert re.search(r'<div class="table-scroll"[^>]*tabindex="0"[^>]*>\s*<table', html)
    assert re.search(r'\.table-scroll\s*\{[^}]*overflow-x\s*:\s*auto', html)
    # A fractional grid with the default auto minimum lets the heatmap make
    # the whole document > 600px wide on a 320–390px viewport.
    assert re.search(r'\.g2\s*>\s*\*\s*,\s*\.g3\s*>\s*\*\s*\{[^}]*min-width\s*:\s*0', html)
    assert re.search(r'@media\(max-width:1000px\)\{\s*\.g2,\.g3\{grid-template-columns:minmax\(0,1fr\)', html)
    # The fixed sidebar overlaps the 1400px main column until ~1760px.
    assert "@media(max-width:1760px){.nav{display:none}}" in html


def test_gap_score_is_not_described_as_a_probability(mod, monkeypatch, tmp_path):
    html = _render(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod)}})
    assert "gap-up bias score" in html.lower()
    assert "not a calibrated probability" in html.lower()


def test_macd_text_is_escaped_in_export(mod, monkeypatch, tmp_path):
    payload = '<img src=x onerror=alert(1)>'
    html = _render(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod, macd_cross=payload)}})
    assert payload not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html


class _LinksAndIDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href", "").startswith("#sym-"):
            self.links.append(attrs["href"])
        if "id" in attrs:
            self.ids.add(attrs["id"])


def test_custom_symbol_links_resolve_after_url_decoding(mod, monkeypatch, tmp_path):
    # Percent escapes in a user-supplied display name must not be interpreted
    # as a different ID when the browser follows a fragment.
    symbols = ["A%20B", "M&M", "R&D/1"]
    data = {s: {"DAY": _entry(mod)} for s in symbols}
    parser = _LinksAndIDs()
    parser.feed(_render(mod, monkeypatch, tmp_path, data))
    assert parser.links
    assert all(unquote(href[1:]) in parser.ids for href in parser.links)
    assert {unquote(link[1:]) for link in parser.links} == {"sym-" + s for s in symbols}


def test_summary_does_not_invent_missing_52w_high(mod):
    # A partial legacy record may contain a low but no high. Treat missing
    # percentages/levels as absent rather than claiming a new 52W high.
    e = _entry(mod, **{"52w": {"low_52w": 90.0, "pct_from_low": 6.4}})
    s = mod.generate_stock_summary("PARTIAL", {"DAY": e}, ["DAY"])
    text = " ".join(s["buy_reasons"] + s["sell_reasons"] + s["cautions"])
    assert "52-week high" not in text
    assert "52-week low (₹90.0)" in text
    assert "₹—" not in text


@pytest.mark.parametrize("bad", [None, "junk", float("nan"), float("inf")])
def test_summary_rejects_invalid_52w_high_fields(mod, bad):
    e = _entry(mod, **{"52w": {"high_52w": 110.0, "pct_from_high": bad}})
    s = mod.generate_stock_summary("PARTIAL", {"DAY": e}, ["DAY"])
    text = " ".join(s["buy_reasons"] + s["sell_reasons"] + s["cautions"])
    assert "52-week high" not in text
    assert "52-week low" not in text


def test_summary_still_uses_complete_52w_data(mod):
    e = _entry(mod, **{"52w": {"high_52w": 110.0, "pct_from_high": -1.0,
                               "low_52w": 80.0, "pct_from_low": 25.0}})
    s = mod.generate_stock_summary("COMPLETE", {"DAY": e}, ["DAY"])
    assert any("1.0% of its 52-week high (₹110.0)" in r for r in s["buy_reasons"])


def test_gap_views_describe_heuristic_not_probability(mod, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *args: "")
    data = {"TEST": {"DAY": _entry(mod)}}
    mod.view_detail("TEST", data)
    detail = capsys.readouterr().out
    assert "gap-up bias score" in detail.lower()
    assert "not a probability" in detail.lower()
    assert "% probability" not in detail.lower()

    mod.next_day_gap_view(data)
    gap_view = capsys.readouterr().out
    assert "BIAS BAR" in gap_view
    assert "not a calibrated probability" in gap_view.lower()
    assert "PROB BAR" not in gap_view
