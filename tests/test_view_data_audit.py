"""
Regression tests for defects surfaced while auditing a live view_detail dump
(LT, 29 Oct 2026).

In every case the underlying SCORE/maths was correct — the display layer hid
or misstated the evidence, so correct arithmetic looked wrong to the user:
  1. calc_support_resistance kept the three FARTHEST swings, dropping the
     nearest level — exactly the one get_price_targets uses as SL/T1.
  2. view_detail's gap section printed only the top-3 factors while the score
     was computed from all of them (shown points summed to 61% vs the 65%
     displayed).
  3. The summary's FULL BULL/BEAR glosses over-claimed where price sat
     relative to every EMA — the labels only assert EMA-to-EMA order, and the
     gloss even contradicted the per-TF reasons (above_EMA50).
  4. Several Fibonacci levels clustered around price each spawned their own
     "bouncing off …" buy factor (same zone counted twice).
  5. The gap confluence fact keyed its label on the NET swing score, so
     2 breakouts + 1 breakdown (net +1) was labelled "1 TF BREAKOUT",
     hiding the conflict.
"""

import re

import pandas as pd


# ── 1. S/R nearest-level slice ────────────────────────────────
def _zigzag_df():
    """40 bars with seven distinct swing lows below (and highs above) 100.

    low[i]  = 90 - 2*(i//4) + (i%4)  → local minima at i%4==0 (88, 86, …, 72
    within the 30-bar tail window); high = 200 - low mirrors them above.
    Price finishes at 100.
    """
    rows = []
    for i in range(40):
        lo = 90 - 2 * (i // 4) + (i % 4)
        rows.append({"open": 100.0, "high": 200 - lo, "low": float(lo),
                     "close": 100.0, "volume": 1000})
    return pd.DataFrame(rows)


def test_support_resistance_keeps_nearest_levels(mod):
    sup, res = mod.calc_support_resistance(_zigzag_df(), lookback=30)

    # Seven swing candidates exist; the top-3 cut must keep the THREE
    # CLOSEST to price — nearest first — not the three farthest.
    assert len(sup) == 3
    assert len(res) == 3
    assert sup == sorted(sup, reverse=True)      # nearest support first
    assert res == sorted(res)                    # nearest resistance first
    assert sup[0] == 84.0                        # max of the candidate set
    assert res[0] == 116.0                       # min of the candidate set
    assert all(s < 100.0 for s in sup)
    assert all(r > 100.0 for r in res)
    # Old behaviour: ascending support kept {72,74,76} and descending
    # resistance kept {124,126,128} — nearest levels vanished.
    assert max(sup) > 76.0
    assert min(res) < 124.0


# ── 2. gap facts completeness in view_detail ─────────────────
def _gap_view_data():
    day = {
        "price": 100.0, "signal": "BREAKOUT", "candle_date": "29 Oct 2026",
        "day_high": 101.0, "day_low": 95.0,        # close near high → +20
        "rsi": 70.0,                                # >65            → +10
        "rel_vol": 2.0,                             # strong close   → +12
        "ema_alignment": "FULL BULL STACK",         #                → +10
        "trend_strength": 60,                       # strong trend   →  +4
        "52w": {"pct_from_high": -0.5},             # at 52W high    →  +8
        "candle_patterns": ["BULL_ENGULF"],         # strong bull    → +10
        "atr": 2.0, "rsi14": 70.0, "macd_cross": "BULL",
        "st_direction": 1, "composite_score": 70.0, "risk_reward": 2.0,
        "support": [95.5], "resistance": [102.5],
    }
    return {"LTAUDIT": {"DAY": day}}


def test_view_detail_prints_all_non_zero_gap_factors(mod, capsys, monkeypatch):
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "")
    mod.view_detail("LTAUDIT", _gap_view_data())
    # strip ANSI colour codes so plain-text matching works
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    # At least 6 non-zero factors exist (20/12/10/10/8/6/4) — the old
    # "Top 3" slice showed exactly 3 and hid e.g. the 52W and trend facts,
    # so the listed points summed to a different raw value than the score.
    pts_lines = re.findall(r"([+-]\d+)pts\s{2}(.+)", out)
    assert len(pts_lines) >= 6, pts_lines
    assert any("52W" in lbl for _, lbl in pts_lines)
    assert any("Trend Str" in lbl for _, lbl in pts_lines)
    assert any("Confluence" in lbl for _, lbl in pts_lines)
    # Score must be internally consistent with the shown factors now that
    # everything is visible (raw ≥ 60 → GAP_UP band, score > 61).
    m = re.search(r"Score\s+:\s+(\d+)%", out)
    assert m, "gap score line missing"
    assert int(m.group(1)) > 61


# ── 3. summary EMA gloss wording ─────────────────────────────
def test_summary_ema_glosses_do_not_overclaim_price_position(mod):
    bear = {"DAY": {"price": 100.0, "signal": "BREAKDOWN", "rsi": 45.0,
                    "rel_vol": 0.9, "composite_score": 30.0,
                    "ema_alignment": "FULL BEAR STACK"}}
    s = mod.generate_stock_summary("AUD1", bear, tfs=["DAY"])
    txt = " ".join(s["sell_reasons"])
    # The label only asserts EMA order — never where price sits vs all EMAs.
    assert "price below all key EMAs" not in txt
    assert "acting as resistance above price" not in txt
    assert "EMA9 < EMA21 < EMA50 < EMA200" in txt

    bull = {"DAY": {"price": 100.0, "signal": "BREAKOUT", "rsi": 60.0,
                    "rel_vol": 1.4, "composite_score": 75.0,
                    "ema_alignment": "FULL BULL STACK"}}
    s2 = mod.generate_stock_summary("AUD2", bull, tfs=["DAY"])
    txt2 = " ".join(s2["buy_reasons"])
    assert "price is above all key moving averages" not in txt2
    assert "EMA9 > EMA21 > EMA50 > EMA200" in txt2


# ── 4. clustered Fibonacci zone = one factor ─────────────────
def test_clustered_fib_levels_count_as_single_zone_factor(mod):
    entry = {
        "price": 101.0, "signal": "BREAKOUT", "rsi": 60.0, "rel_vol": 1.2,
        "composite_score": 70.0, "ema_alignment": "BULLISH STACK",
        "fibonacci": {"fib_38.2": 104.0, "fib_50": 102.0, "fib_61.8": 100.0,
                      "sw_high": 105.0, "sw_low": 95.0},
    }
    s = mod.generate_stock_summary("AUD3", {"DAY": entry}, tfs=["DAY"])
    fib_facts = [r for r in s["buy_reasons"] if "Fibonacci" in r]
    # 101 is within 2.5% of BOTH 50% (102) and 61.8% (100) — one zone,
    # one factor. The old loop fired "bouncing off" for each level.
    assert len(fib_facts) == 1, fib_facts
    assert "zone" in fib_facts[0]


# ── 5. gap confluence fact counts both sides ─────────────────
def test_gap_confluence_label_counts_breakouts_and_breakdowns(mod):
    data = {
        "DAY":   {"signal": "BREAKOUT",  "price": 100.0},
        "WEEK":  {"signal": "BREAKDOWN"},
        "MONTH": {"signal": "BREAKOUT"},
    }
    _score, _bias, facts = mod.calc_next_day_gap_score(data)
    con = [(l, p) for l, p in facts if l.startswith("Confluence:")]
    assert len(con) == 1
    label, pts = con[0]
    # Net +1 used to be labelled "1 TF BREAKOUT" — hiding the WEEK
    # breakdown sitting right behind it.
    assert label == "Confluence: Mixed: 2 BREAKOUT / 1 BREAKDOWN", label
    # Weighting itself must be unchanged: net(+1) * 6 = +6.
    assert pts == 6


# ── 6. trade plan must side with the verdict ────────────────
def test_trade_plan_direction_matches_verdict(mod, capsys, monkeypatch):
    """A BUY verdict must not be paired with a BREAKDOWN trade plan.

    view_detail picked the single highest composite score regardless of
    direction: a MONTH BREAKDOWN at 80 beat the DAY BREAKOUT at 60, so the
    plan recommended the SHORT (T1 below entry) while the summary printed
    VERDICT: BUY with long targets — opposite sides on one screen.
    """
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "")
    day = {
        "price": 100.0, "signal": "BREAKOUT", "composite_score": 60.0,
        "risk_reward": 2.3, "rsi": 62.0, "rel_vol": 1.5, "atr": 2.0,
        "day_high": 101.0, "day_low": 95.0, "candle_date": "2026-10-29",
        "ema_alignment": "BULLISH STACK", "st_direction": 1,
        "targets": {"target1": 110.0, "target2": 116.0, "stop": 95.0},
    }
    month = {
        "price": 100.0, "signal": "BREAKDOWN", "composite_score": 80.0,
        "risk_reward": 1.4, "rsi": 42.0, "rel_vol": 1.6, "atr": 14.0,
        "day_high": 101.0, "day_low": 95.0, "candle_date": "2026-10",
        "ema_alignment": "BEARISH STACK", "st_direction": -1,
        "targets": {"target1": 80.0, "target2": 70.0, "stop": 115.0},
    }
    mod.view_detail("PLAUDIT", {"PLAUDIT": {"DAY": day, "MONTH": month}})
    import re
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    v = re.search(r"VERDICT:\s+(\w+)", out)
    assert v, "verdict line missing"
    assert v.group(1) == "BUY"          # primary DAY breakout → BUY
    b = re.search(r"Best Setup\s*:\s*(?:[↑↓]\s*)?(BREAKOUT|BREAKDOWN)\s+on\s+(\w+)", out)
    assert b, "best-setup line missing"
    # Plan must side with the verdict, not the lone higher-scored counter-trend TF.
    assert b.group(1) == "BREAKOUT", f"plan recommends {b.group(1)} under a BUY verdict"
    assert b.group(2) == "DAY"
    assert "opposite side" not in out   # fallback warning not needed here
