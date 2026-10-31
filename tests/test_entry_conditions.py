"""Conditional entry levels must not be confused with actual trade fills."""

import re

import pytest


def _entry(mod, signal="BREAKOUT", **updates):
    e = mod.empty_entry()
    e.update({
        "signal": signal,
        "price": 100.0,
        "support": [95.0, 98.0],
        "resistance": [105.0, 102.0],
        "candle_date": "2026-10-29 15:15",
        "updated": "2026-10-29 16:12",
        "atr": 2.0,
        "risk_reward": 1.0,
        "targets": {"target1": 102.0, "target2": 105.0, "stop": 98.0},
    })
    e.update(updates)
    return e


def test_bullish_entries_use_nearest_levels_and_completed_candle(mod):
    e = _entry(mod, support=[95, "98", 90], resistance=[105, "102", 110])
    setups = mod.get_conditional_entries(e, "15MIN")
    assert setups["direction"] == "BULLISH"
    assert setups["breakout"]["level"] == 102.0
    assert "completed 15MIN candle" in setups["breakout"]["when"]
    assert "closes above ₹102.00" in setups["breakout"]["when"]
    assert setups["pullback"]["level"] == 98.0
    assert "at or below ₹98.00" in setups["pullback"]["when"]
    assert "closes above ₹98.00" in setups["pullback"]["when"]
    # A threshold is not a computed fill or target. The old T1 may equal 102,
    # which cannot be reused as a profit target after a close above 102.
    assert "entry_price" not in setups["breakout"]
    assert "target1" not in setups["breakout"]


def test_bearish_entries_mirror_long_conditions(mod):
    e = _entry(mod, "BREAKDOWN", support=[95, 98], resistance=[105, 102])
    setups = mod.get_conditional_entries(e, "DAY")
    assert setups["direction"] == "BEARISH"
    assert setups["breakout"]["level"] == 98.0
    assert "completed DAY candle" in setups["breakout"]["when"]
    assert "closes below ₹98.00" in setups["breakout"]["when"]
    assert setups["pullback"]["level"] == 102.0
    assert "at or above ₹102.00" in setups["pullback"]["when"]
    assert "closes below ₹102.00" in setups["pullback"]["when"]


def test_level_validation_never_invents_or_uses_wrong_side(mod):
    e = _entry(mod, support=[100, 103, -1, None, "junk", float("nan"),
                             float("inf"), 98, True],
                    resistance=[100, 97, -1, None, "junk", float("nan"),
                                float("inf"), 102, False])
    setups = mod.get_conditional_entries(e, "DAY")
    assert setups["breakout"]["level"] == 102.0
    assert setups["pullback"]["level"] == 98.0

    # A missing resistance should not be invented from T1, ATR, or the price.
    e["resistance"] = [97, 100, None, "bad"]
    setups = mod.get_conditional_entries(e, "DAY")
    assert setups["breakout"]["level"] is None
    assert "no resistance" in setups["breakout"]["when"].lower()
    assert setups["pullback"]["level"] == 98.0


@pytest.mark.parametrize("signal", ["SIDEWAYS", "NONE", None])
def test_no_direction_means_no_fabricated_entry_condition(mod, signal):
    setups = mod.get_conditional_entries(_entry(mod, signal), "DAY")
    assert setups["direction"] is None
    assert setups["breakout"]["level"] is None
    assert setups["pullback"]["level"] is None
    assert "directional" in setups["breakout"]["when"].lower()


@pytest.mark.parametrize("price", [0, -1, "junk", float("nan"), float("inf")])
def test_missing_price_does_not_create_entry_conditions(mod, price):
    setups = mod.get_conditional_entries(_entry(mod, price=price), "DAY")
    assert setups["breakout"]["level"] is None
    assert setups["pullback"]["level"] is None
    assert "reference price" in setups["breakout"]["when"].lower()


def _report(mod, monkeypatch, tmp_path, data, tfs=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args: "")
    mod.export_html(data, tfs or ["15MIN", "DAY"])
    return next(tmp_path.glob("master_report_*.html")).read_text(encoding="utf-8")


def test_html_shows_both_setups_but_not_a_fake_fill(mod, monkeypatch, tmp_path):
    day = _entry(mod)
    intraday = _entry(mod, "BREAKDOWN")
    html = _report(mod, monkeypatch, tmp_path, {"TEST": {"DAY": day, "15MIN": intraday}})
    assert "Conditional entries" in html
    assert "Breakout (bullish)" in html and "Pullback / retest (bullish)" in html
    assert "Breakdown (bearish)" in html and "Pullback / retest (bearish)" in html
    assert "completed DAY candle closes above ₹102.00" in html
    assert "completed 15MIN candle closes below ₹98.00" in html
    assert "completed DAY candle trades at or below ₹98.00" in html
    assert "completed 15MIN candle trades at or above ₹102.00" in html
    assert "2026-10-29 15:15" in html and "2026-10-29 16:12" in html
    assert "not monitored live" in html.lower()
    assert "full candle starting after the scan time" in html.lower()
    assert "entry checks use price only, with no extra volume filter" in html.lower()
    assert "not proof of an earlier breakout" in html.lower()
    assert "actual fill" in html.lower()
    assert "recalculate" in html.lower()
    assert "nearest resistance" in html and "nearest support" in html
    day_condition = html.split("Conditional entries · DAY", 1)[1].split("</section>", 1)[0]
    assert "Target 1" not in day_condition and "1.0:1" not in day_condition
    assert "not a conditional entry or actual fill" in html
    assert '<span class="tgt-lbl">Reference price</span>' in html
    assert '<span class="tgt-lbl">Entry</span>' not in html
    assert "Entry: ₹100" not in html


def test_missing_levels_are_visible_as_unavailable(mod, monkeypatch, tmp_path):
    e = _entry(mod, support=[], resistance=[])
    html = _report(mod, monkeypatch, tmp_path, {"TEST": {"DAY": e}})
    assert "No resistance above the reference price" in html
    assert "No support below the reference price" in html
    assert "₹0.00" not in html.split("Conditional entries", 1)[1].split("</section>", 1)[0]


def test_detail_and_text_export_show_both_conditions(mod, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args: "")
    e = _entry(mod)
    data = {"TEST": {"DAY": e}}
    mod.view_detail("TEST", data)
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "Breakout (bullish)" in out and "Pullback / retest (bullish)" in out
    assert "completed DAY candle closes above ₹102.00" in out
    assert "not monitored live" in out.lower()
    assert not re.search(r"\bEntry\s*:\s*₹100", out)

    mod.export_report(data, ["DAY"])
    txt = next(tmp_path.glob("master_report_*.txt")).read_text(encoding="utf-8")
    assert "Breakout (bullish)" in txt and "Pullback / retest (bullish)" in txt
    assert "completed DAY candle closes above ₹102.00" in txt
    assert "not monitored live" in txt.lower()


def test_stock_summary_does_not_recommend_reference_close_as_entry(mod):
    e = _entry(mod, composite_score=70, rsi=60)
    summary = mod.generate_stock_summary("TEST", {"DAY": e}, ["DAY"])
    text = " ".join(summary["buy_reasons"] + summary["sell_reasons"] + [summary["action"]])
    assert "Entry: ₹100" not in text
    assert "entry near ₹100" not in text
    assert "reference" in text.lower()
    assert "condition" in summary["action"].lower()


@pytest.mark.parametrize("updated", ["", None, 12, "yesterday", "2026-10-42 10:00"])
def test_missing_or_invalid_scan_time_cannot_start_an_entry_window(mod, updated):
    setups = mod.get_conditional_entries(_entry(mod, updated=updated), "DAY")
    assert setups["breakout"]["level"] is None
    assert setups["pullback"]["level"] is None
    assert "scan time" in setups["breakout"]["when"].lower()


def test_rescan_does_not_claim_to_have_tracked_old_trigger(mod):
    old = mod.get_conditional_entries(_entry(mod), "DAY")
    new = mod.get_conditional_entries(_entry(mod, price=103, support=[102],
                                             resistance=[105]), "DAY")
    assert old["breakout"]["level"] == 102
    assert new["breakout"]["level"] == 105
    assert "triggered" not in str(new).lower()
    assert "not monitored live" in mod.ENTRY_CONDITION_NOTE.lower()
    assert "recalculates levels" in mod.ENTRY_CONDITION_NOTE.lower()


def test_intraday_only_report_has_reachable_detail_and_entry_conditions(mod, monkeypatch, tmp_path):
    # ensure_symbol normally creates empty DAY/WEEK/MONTH entries even when
    # only 15MIN was scanned. They must not hide the intraday detail card or
    # replace its entry conditions and summary with invented DAY data.
    data = {"INTRADAY": {"DAY": mod.empty_entry(), "15MIN": _entry(mod)}}
    html = _report(mod, monkeypatch, tmp_path, data, ["15MIN"])
    assert 'id="sym-INTRADAY"' in html
    assert 'href="#sym-INTRADAY"' in html
    assert "completed 15MIN candle closes above ₹102.00" in html
    assert "Pullback / retest (bullish)" in html
    assert "15MIN candle close" in html
    assert "DAY data unavailable" in html
    summary = mod.generate_stock_summary("INTRADAY", data["INTRADAY"], ["15MIN"])
    assert any("bullish BREAKOUT signal on the 15MIN chart" in r
               for r in summary["buy_reasons"])
    # The all-TF table must identify which timeframe provides its metrics.
    all_tf = html.split("All-TF Breakout Setups", 1)[1].split("</table>", 1)[0]
    assert "Metric TF" in all_tf
    assert ">15MIN</td>" in all_tf
    assert "₹100.00" in all_tf
    assert "₹0.00" not in all_tf


@pytest.mark.parametrize("bad", ["junk", 0, -1, True, float("nan"), float("inf")])
def test_invalid_reference_price_is_unavailable_not_zero_in_html(mod, monkeypatch, tmp_path, bad):
    e = _entry(mod, price=bad)
    html = _report(mod, monkeypatch, tmp_path, {"TEST": {"DAY": e}})
    assert "Reference price unavailable" in html
    assert "no valid reference price" in html.lower()
    assert '<span class="tgt-lbl">Reference price</span><span class="mono">—</span>' in html
    assert "₹0.00" not in html


def test_bad_timestamp_is_escaped_and_does_not_enable_conditions(mod, monkeypatch, tmp_path):
    bad = '<img src=x onerror=alert(1)>'
    html = _report(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod, updated=bad)}})
    assert bad not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert "scan time is missing or invalid" in html.lower()
    assert "completed DAY candle closes above" not in html


@pytest.mark.parametrize("bad", [0, -1, True, float("inf"), float("nan"), "junk"])
def test_summary_uses_scanned_timeframe_instead_of_invalid_day_price(mod, bad):
    data = {"DAY": _entry(mod, price=bad), "15MIN": _entry(mod)}
    assert mod._score_tf_key(data, ["15MIN"]) == "15MIN"
    summary = mod.generate_stock_summary("TEST", data, ["15MIN"])
    assert any("on the 15MIN chart" in r for r in summary["buy_reasons"])


def test_condition_thresholds_keep_paise_and_grouping(mod):
    e = _entry(mod, price=11175.25, support=[10612.03, 10551.0],
               resistance=[11211.2, 11198.75])
    setups = mod.get_conditional_entries(e, "DAY")
    assert setups["breakout"]["level"] == 11198.75
    assert "closes above ₹11,198.75" in setups["breakout"]["when"]
    assert setups["pullback"]["level"] == 10612.03
    assert "at or below ₹10,612.03" in setups["pullback"]["when"]


@pytest.mark.parametrize("signal", ["NONE", "SIDEWAYS"])
def test_scanned_without_direction_explains_no_entries(mod, monkeypatch, tmp_path, signal):
    html = _report(mod, monkeypatch, tmp_path, {"TEST": {"DAY": _entry(mod, signal)}})
    assert "Conditional entries · DAY" in html
    assert "no directional breakout/breakdown signal" in html.lower()
    assert "completed DAY candle closes above ₹102.00" not in html


@pytest.mark.parametrize("bad", [None, [], "junk", 42])
def test_primary_timeframe_handles_corrupt_symbol_data(mod, bad):
    assert mod._score_tf_key(bad, ["DAY"]) == "DAY"
