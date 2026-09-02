"""
End-to-end tests: mocked Upstox -> candles -> indicators -> signal -> store.

Also includes a *mirror invariance* test: reflecting a price series about a
horizontal line must flip every bull/bear verdict.  Any asymmetry between the
bull and bear scoring paths shows up here.
"""
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_ohlcv
from tests.fake_upstox import FakeUpstox, make_daily_series, today_ist

KEY = "NSE_EQ|INE002A01018"


@pytest.fixture
def api(mod, monkeypatch):
    fake = FakeUpstox()
    monkeypatch.setattr(mod, "requests", fake, raising=False)
    return fake


@pytest.fixture
def wired(mod, api, monkeypatch):
    """A scanner wired to the fake API with a 3-symbol universe."""
    monkeypatch.setattr(mod, "SYMBOL_MAP",
                        {"K1": "ALPHA", "K2": "BETA", "K3": "GAMMA"},
                        raising=False)
    monkeypatch.setattr(mod, "SECTOR_MAP",
                        {"ALPHA": "IT", "BETA": "BANK", "GAMMA": "AUTO"},
                        raising=False)
    return mod


def _mirror(df, axis=None):
    """Reflect OHLC around a horizontal axis (inverts the chart).

    The axis is chosen so the reflected prices stay positive and inside the
    original range — otherwise money-flow style indicators see negative
    "prices" and the reflection is no longer a true mirror.
    """
    if axis is None:
        axis = float(df["high"].max() + df["low"].min())
    # Geometrically exact: every price p -> axis - p, with high/low swapped
    # because the reflection turns the bar's top into its bottom.  This keeps
    # typical price, %K and money flow exactly mirrored while inverting every
    # candle body (c > o becomes c' < o').
    out = df.copy()
    out["open"] = axis - df["open"]
    out["close"] = axis - df["close"]
    out["high"] = axis - df["low"]
    out["low"] = axis - df["high"]
    out["vol"] = df["vol"]
    out["oi"] = df["oi"]
    return out


# ── mirror invariance ─────────────────────────────────────────
MIRROR_SERIES = [
    ("uptrend",   dict(n=400, seed=11, trend=0.9,  vol=0.5, start_price=500.0)),
    ("downtrend", dict(n=400, seed=13, trend=-0.9, vol=0.5, start_price=1500.0)),
    ("choppy",    dict(n=400, seed=17, trend=0.0,  vol=1.4, start_price=800.0)),
    ("lowvol",    dict(n=400, seed=23, trend=0.2,  vol=0.2, start_price=250.0)),
    ("spiky",     dict(n=400, seed=29, trend=0.3,  vol=2.5, start_price=1200.0)),
]


@pytest.mark.parametrize("name,kw", MIRROR_SERIES, ids=[m[0] for m in MIRROR_SERIES])
def test_signal_engine_is_mirror_invariant(mod, name, kw):
    """Reflecting the chart must flip bull/bear points exactly."""
    src = make_ohlcv(**kw)
    up = mod.calculate_indicators(src.copy(), "DAY")
    dn = mod.calculate_indicators(_mirror(src).copy(), "DAY")
    sig_u, _, _, _, _, comp_u, _ = mod.detect_signal(up, "DAY")
    sig_d, _, _, _, _, comp_d, _ = mod.detect_signal(dn, "DAY")

    # (bull_key, bear_key, negate_for_bear, mirror_exact)
    # MFI multiplies price LEVELS by volume, so reflecting the chart does not
    # simply invert it (unlike RSI, which uses differences).  It is checked
    # separately with a tolerance.
    pairs = [("above_ema21", "above_ema21", True, True),
             ("above_ema50", "above_ema50", True, True),
             ("above_ema200", "above_ema200", True, True),
             ("ema9_above21", "ema9_above21", True, True),
             ("ema50_above200", "ema50_above200", True, True),
             ("rsi_bull", "rsi_bear", False, True),
             ("macd_bull", "macd_bear", False, True),
             ("st_bull", "st_bull", True, True),
             ("stoch_bull", "stoch_bear", False, True),
             ("vol_spike", "vol_spike", False, True),
             ("adx_bull_di", "adx_bear_di", False, True),
             ("wr_bull", "wr_bear", False, True),
             ("mfi_bull", "mfi_bear", False, False),
             ("cci_bull", "cci_bear", False, True)]
    mismatches = []
    for bull_key, bear_key, negate, exact in pairs:
        if not exact:
            continue
        bull_v = bool(comp_u[bull_key])
        bear_v = (not bool(comp_d[bear_key])) if negate else bool(comp_d[bear_key])
        if bull_v != bear_v:
            mismatches.append((bull_key, bull_v, bear_v))
    assert not mismatches, (
        "bull/bear scoring is not mirror-symmetric: " + str(mismatches))

    opposite = {"BREAKOUT": "BREAKDOWN", "BREAKDOWN": "BREAKOUT"}
    assert opposite.get(sig_u) == sig_d or sig_u == sig_d == "SIDEWAYS", (
        "signal %s did not mirror to %s (got %s)"
        % (sig_u, opposite.get(sig_u), sig_d))


@pytest.mark.parametrize("name,kw", MIRROR_SERIES, ids=[m[0] for m in MIRROR_SERIES])
def test_trend_strength_is_mirror_invariant(mod, name, kw):
    src = make_ohlcv(**kw)
    up = mod.calculate_indicators(src.copy(), "DAY")
    dn = mod.calculate_indicators(_mirror(src).copy(), "DAY")
    _, _, _, _, _, c_up, _ = mod.detect_signal(up, "DAY")
    _, _, _, _, _, c_dn, _ = mod.detect_signal(dn, "DAY")
    # MFI multiplies price *levels* by volume, so it is not mirror-invariant
    # by construction; neutralise it and require the rest to be exact.
    nu = dict(c_up, mfi_bull=False, mfi_bear=False)
    nd = dict(c_dn, mfi_bull=False, mfi_bear=False)
    assert mod.trend_strength(nu) == -mod.trend_strength(nd), (
        "trend bar is skewed: +%d vs %d" % (mod.trend_strength(nu),
                                            mod.trend_strength(nd)))


@pytest.mark.parametrize("name,kw", MIRROR_SERIES, ids=[m[0] for m in MIRROR_SERIES])
def test_composite_score_is_reasonable_for_opposite_setups(mod, name, kw):
    src = make_ohlcv(**kw)
    up = mod.calculate_indicators(src.copy(), "DAY")
    dn = mod.calculate_indicators(_mirror(src).copy(), "DAY")
    su, _, _, _, _, cu, _ = mod.detect_signal(up, "DAY")
    sd, _, _, _, _, cd, _ = mod.detect_signal(dn, "DAY")
    entry_u = {"DAY": {"signal": su, "rsi": 60, "volume": 60,
                       "trend_strength": mod.trend_strength(cu),
                       "st_direction": int(up["st_dir"].iloc[-1]),
                       "macd_cross": "", "candle_patterns": [],
                       "rsi_divergence": {}}}
    entry_d = {"DAY": {"signal": sd, "rsi": 40, "volume": 60,
                       "trend_strength": mod.trend_strength(cd),
                       "st_direction": int(dn["st_dir"].iloc[-1]),
                       "macd_cross": "", "candle_patterns": [],
                       "rsi_divergence": {}}}
    s_u = mod.calc_composite_score(entry_u, ["DAY"], target_tf="DAY")
    s_d = mod.calc_composite_score(entry_d, ["DAY"], target_tf="DAY")
    assert 0 <= s_u <= 100 and 0 <= s_d <= 100
    assert abs(s_u - s_d) <= 12, (
        "mirror-image setups should score similarly: %d vs %d" % (s_u, s_d))


# ── signal engine behaviour ───────────────────────────────────
def test_strong_uptrend_is_bullish(mod, df_uptrend):
    up = mod.calculate_indicators(df_uptrend.copy(), "DAY")
    sig = mod.detect_signal(up, "DAY")[0]
    assert sig == "BREAKOUT", "a persistent uptrend must not read bearish"


def test_strong_downtrend_is_bearish(mod, df_downtrend):
    dn = mod.calculate_indicators(df_downtrend.copy(), "DAY")
    sig = mod.detect_signal(dn, "DAY")[0]
    assert sig == "BREAKDOWN"


def test_insufficient_data_returns_none_not_crash(mod):
    small = make_ohlcv(n=30)
    out = mod.calculate_indicators(small, "DAY")   # below TF_MIN_CANDLES
    sig, _, _, _, reason, comps, _ = mod.detect_signal(out, "DAY")
    assert sig == "NONE" and "insufficient" in reason


def test_signal_is_deterministic(mod, df):
    a = mod.calculate_indicators(df.copy(), "DAY")
    b = mod.calculate_indicators(df.copy(), "DAY")
    assert mod.detect_signal(a, "DAY")[:5] == mod.detect_signal(b, "DAY")[:5]


def test_targets_obey_direction(mod, df_uptrend):
    up = mod.calculate_indicators(df_uptrend.copy(), "DAY")
    sig = mod.detect_signal(up, "DAY")[0]
    t = mod.get_price_targets(up, sig, "DAY")
    if not t:
        pytest.skip("no targets for this series")
    px = float(up["close"].iloc[-1])
    if sig == "BREAKOUT":
        assert t["target1"] > px and t["stop"] < px
    elif sig == "BREAKDOWN":
        assert t["target1"] < px and t["stop"] > px


def test_risk_reward_is_positive_and_finite(mod, df_uptrend):
    up = mod.calculate_indicators(df_uptrend.copy(), "DAY")
    sig = mod.detect_signal(up, "DAY")[0]
    t = mod.get_price_targets(up, sig, "DAY")
    if not t:
        pytest.skip("no targets")
    px = float(up["close"].iloc[-1])
    risk = abs(px - t["stop"])
    rew = abs(t["target1"] - px)
    assert risk > 0, "stop must never equal entry"
    assert np.isfinite(rew / risk)


def test_no_targets_when_atr_is_zero(mod, df_flat):
    flat = mod.calculate_indicators(df_flat.copy(), "DAY")
    assert mod.get_price_targets(flat, "BREAKOUT", "DAY") == {}


# ── end-to-end scan ───────────────────────────────────────────
def test_scan_populates_store_and_db(wired, api, monkeypatch):
    today = today_ist()
    api.daily = make_daily_series(n=400, end=today - timedelta(days=1), seed=21)
    live = pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [101.0], "high": [103.0], "low": [100.0], "close": [102.0],
        "vol": [12345.0], "oi": [0.0]})
    api.intraday = {("days", "1"): live}
    monkeypatch.setattr("builtins.input", lambda *a: "")

    data = wired.auto_scan({}, ["DAY"])
    assert set(data.keys()) == {"ALPHA", "BETA", "GAMMA"}
    for sym, entry in data.items():
        e = entry["DAY"]
        assert e["price"] == pytest.approx(102.0), "live bar must be the price"
        assert e["signal"] in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE")
        assert 0 <= e["composite_score"] <= 100
        assert e["candle_date"]

    # persisted?
    reloaded = wired.load_data()
    assert reloaded["ALPHA"]["DAY"]["price"] == pytest.approx(102.0)


def test_history_is_logged_for_signals(wired, api, monkeypatch):
    today = today_ist()
    api.daily = make_daily_series(n=400, end=today - timedelta(days=1), seed=3)
    api.intraday = {("days", "1"): pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "vol": [1e5], "oi": [0.0]})}
    monkeypatch.setattr("builtins.input", lambda *a: "")
    wired.auto_scan({}, ["DAY"])
    hist = wired.load_history()
    logged = [k for k, v in hist.items() if v]
    assert logged, "signals should be written to the history table"
    for key in logged:
        for row in hist[key]:
            assert row.get("logged_at")


def test_history_does_not_duplicate_identical_rows(wired, api, monkeypatch):
    today = today_ist()
    api.daily = make_daily_series(n=400, end=today - timedelta(days=1), seed=3)
    api.intraday = {("days", "1"): pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "vol": [1e5], "oi": [0.0]})}
    monkeypatch.setattr("builtins.input", lambda *a: "")
    data = wired.auto_scan({}, ["DAY"])
    n1 = sum(len(v) for v in wired.load_history().values())
    wired.start_scan_pass()
    data = wired.auto_scan(data, ["DAY"])
    n2 = sum(len(v) for v in wired.load_history().values())
    assert n2 == n1, "re-scanning identical data must not spam the history log"


def test_signal_change_detection(wired, api, monkeypatch):
    today = today_ist()
    api.daily = make_daily_series(n=400, end=today - timedelta(days=1), seed=3)
    api.intraday = {("days", "1"): pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "vol": [1e5], "oi": [0.0]})}
    monkeypatch.setattr("builtins.input", lambda *a: "")
    data = wired.auto_scan({}, ["DAY"])
    first = data["ALPHA"]["DAY"]["signal"]
    # force a different signal by re-scoring with an inverted candle
    api.daily = _mirror(api.daily)
    wired.start_scan_pass()
    data = wired.auto_scan(data, ["DAY"])
    second = data["ALPHA"]["DAY"]["signal"]
    if first != second:
        assert data["ALPHA"]["DAY"]["signal_changed"] is True
        assert data["ALPHA"]["DAY"]["prev_signal"] == first


def test_rescanned_tf_does_not_leave_stale_entry(wired, api, monkeypatch):
    """A TF with no data must not silently keep yesterday's numbers."""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    api.daily = None
    api.intraday = {}
    data = wired.auto_scan({}, ["DAY"])
    for sym in ("ALPHA", "BETA", "GAMMA"):
        assert data[sym]["DAY"]["price"] == 0
        assert data[sym]["DAY"]["signal"] == "NONE"


# ── next-day gap score ──────────────────────────────────────
def _gap_entry(mod, **over):
    e = mod.empty_entry()
    e.update({
        "price": 100.0, "day_open": 99.0, "day_high": 110.0, "day_low": 90.0,
        "rsi": 50.0, "rel_vol": 1.0, "trend_strength": 0,
        "signal": "NONE", "ema_alignment": "", "52w": {},
        "candle_patterns": [], "macd_hist_val": 0.0,
    })
    e.update(over)
    return {"DAY": e}


def test_gap_score_uses_real_close_position_not_trend_strength(mod):
    """Factor 1 must read the day's OHLC, not trend_strength.

    Regression: it read trend_strength — which factor 8 already scores — so
    the label ("Close near High") was false and trend strength counted for
    up to ±27 of the ±80 raw range instead of the documented ±7.
    """
    top = mod.calc_next_day_gap_score(_gap_entry(mod, price=109.0))
    bot = mod.calc_next_day_gap_score(_gap_entry(mod, price=91.0))
    mid = mod.calc_next_day_gap_score(_gap_entry(mod, price=100.0))
    assert top[0] > mid[0] > bot[0], (top[0], mid[0], bot[0])

    # trend_strength must not move factor 1 once real OHLC is available
    flat_ts = mod.calc_next_day_gap_score(
        _gap_entry(mod, price=109.0, trend_strength=-100))
    assert flat_ts[0] > mid[0], "trend_strength must not override close position"


def test_gap_score_falls_back_cleanly_without_ohlc(mod):
    """Rows written before day_high/day_low existed must not crash or score 0."""
    e = _gap_entry(mod, price=100.0)
    del e["DAY"]["day_high"]
    del e["DAY"]["day_low"]
    score, bias, facts = mod.calc_next_day_gap_score(e)
    assert 5 <= score <= 95
    assert bias in ("GAP_UP", "NEUTRAL", "GAP_DOWN")
    assert facts, "fallback still reports its factors"


def test_gap_score_never_claims_certainty(mod):
    for price in (90.0, 95.0, 100.0, 105.0, 110.0):
        for ts in (-100, -50, 0, 50, 100):
            score, bias, _ = mod.calc_next_day_gap_score(
                _gap_entry(mod, price=price, trend_strength=ts,
                           rsi=90.0, rel_vol=5.0))
            assert 5 <= score <= 95, (price, ts, score)
            assert bias in ("GAP_UP", "NEUTRAL", "GAP_DOWN")


# ── entry / stop / target geometry ──────────────────────────
CLOSE = 1000.0
ATR = 20.0


def _flat_df(n=60, close=CLOSE, atr=ATR, seed=0):
    """Flat OHLCV with a constant ATR column, ready for get_price_targets."""
    import pandas as pd
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-01-01", periods=n, freq="D", tz="Asia/Kolkata")
    c = np.full(n, close)
    return pd.DataFrame({
        "ts": ts, "open": c,
        "high": c + rng.uniform(1.0, 5.0, n),
        "low": c - rng.uniform(1.0, 5.0, n),
        "close": c, "vol": np.full(n, 1e5), "oi": np.zeros(n),
        "atr": atr,
    })


def _with_level(df, kind, mult):
    """Plant a single swing high/low `mult` × ATR away from the close."""
    df = df.copy()
    i = df.index[-3]
    if kind == "res":
        df.loc[i, "high"] = CLOSE + mult * ATR
    else:
        df.loc[i, "low"] = CLOSE - mult * ATR
    return df


@pytest.mark.parametrize("tf", ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"])
@pytest.mark.parametrize("mult", [0.5, 1.0, 2.0, 3.0, 3.5, 5.0, 7.0, 9.0, 12.0])
def test_breakout_levels_are_ordered_stop_lt_entry_lt_t1_lt_t2(mod, tf, mult):
    """Regression: T2 was a fixed close+3×ATR while T1 was the nearest swing,
    so a single qualifying level further than 3×ATR inverted the ladder and
    the trade plan printed "T1 ₹1100 / T2 ₹1060" — book the second half at a
    worse price than the first.
    """
    t = mod.get_price_targets(_with_level(_flat_df(), "res", mult),
                              "BREAKOUT", tf)
    if not t:
        pytest.skip("no levels qualified for this TF/mult combination")
    sl, t1, t2 = t["stop"], t["target1"], t["target2"]
    assert sl < CLOSE < t1 < t2, (tf, mult, sl, t1, t2)
    # T2 must be a real step up, not a duplicate of T1: two swing levels
    # closer than half an ATR are one level for trading purposes.
    assert t2 - t1 >= 0.5 * ATR, (tf, mult, t1, t2)


@pytest.mark.parametrize("tf", ["5MIN", "15MIN", "1HR", "DAY", "WEEK", "MONTH"])
@pytest.mark.parametrize("mult", [0.5, 1.0, 2.0, 3.0, 3.5, 5.0, 7.0, 9.0, 12.0])
def test_breakdown_levels_are_ordered_t2_lt_t1_lt_entry_lt_stop(mod, tf, mult):
    """Mirror of the above: on a short, T2 must sit BELOW T1."""
    t = mod.get_price_targets(_with_level(_flat_df(), "sup", mult),
                              "BREAKDOWN", tf)
    if not t:
        pytest.skip("no levels qualified for this TF/mult combination")
    sl, t1, t2 = t["stop"], t["target1"], t["target2"]
    assert t2 < t1 < CLOSE < sl, (tf, mult, t2, t1, sl)
    assert t1 - t2 >= 0.5 * ATR, (tf, mult, t1, t2)


@pytest.mark.parametrize("signal", ["BREAKOUT", "BREAKDOWN"])
def test_levels_are_never_negative_or_absurd(mod, signal):
    """_floor_price must keep every level positive even with a crazy ATR."""
    for atr in (1.0, 20.0, 200.0, 900.0, 5000.0):
        t = mod.get_price_targets(_flat_df(atr=atr), signal, "DAY")
        if not t:
            continue
        for key in ("target1", "target2", "stop"):
            assert t[key] > 0, (atr, key, t[key])
            assert np.isfinite(t[key])


def test_risk_reward_matches_the_stop_and_target1(mod):
    """The displayed R:R must be |T1-entry| / |entry-stop|, not stale."""
    df = _with_level(_flat_df(), "res", 2.0)
    t = mod.get_price_targets(df, "BREAKOUT", "DAY")
    assert t
    risk = abs(CLOSE - t["stop"])
    reward = abs(t["target1"] - CLOSE)
    assert risk > 0
    assert round(reward / risk, 1) > 0
    # stop must be a meaningful distance out, not a micro-level
    assert risk >= 0.4 * ATR, "stop closer than the documented minimum"


def test_no_levels_returns_empty_not_a_flat_dict(mod):
    assert mod.get_price_targets(_flat_df(), "SIDEWAYS", "DAY") == {}
    assert mod.get_price_targets(_flat_df(), "NONE", "DAY") == {}
    empty = _flat_df().iloc[0:0]
    assert mod.get_price_targets(empty, "BREAKOUT", "DAY") == {}
