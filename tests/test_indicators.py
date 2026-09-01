"""
Correctness tests for the indicator maths.

Each indicator is checked against an independent reference implementation
written from the published formula — not against the scanner's own output.
"""

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_ohlcv


# ── reference implementations ────────────────────────────────
def ref_rsi_wilder(close, period=14):
    """Textbook Wilder RSI (SMA seed, then Wilder smoothing)."""
    close = np.asarray(close, dtype=float)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.empty_like(close)
    avg_loss = np.empty_like(close)
    avg_gain[:period] = np.nan
    avg_loss[:period] = np.nan
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    rsi = np.full_like(close, np.nan)
    for i in range(period, len(close)):
        if avg_loss[i] == 0:
            rsi[i] = 100.0 if avg_gain[i] > 0 else 50.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100 - 100 / (1 + rs)
    return rsi


def ref_atr(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def ref_cci(df, period=20):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)


def ref_williams_r(df, period=14):
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return -100 * (hh - df["close"]) / (hh - ll)


def ref_mfi(df, period=14):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["vol"]
    d = tp.diff()
    pos = mf.where(d > 0, 0.0).rolling(period).sum()
    neg = mf.where(d < 0, 0.0).rolling(period).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


# ── tests ────────────────────────────────────────────────────
def test_rsi_matches_wilder_reference(mod, df):
    got = mod.calc_rsi(df["close"]).to_numpy()
    ref = ref_rsi_wilder(df["close"].to_numpy())
    # The only difference is the seed (SMA vs EWM from bar 0); Wilder's
    # recurrence then decays that difference away, so compare past warm-up.
    tail = slice(150, None)
    np.testing.assert_allclose(got[tail], ref[tail], rtol=1e-6, atol=0.05)
    np.testing.assert_allclose(got[-60:], ref[-60:], rtol=1e-6, atol=1e-6)


def test_rsi_never_returns_50_when_series_only_rises(mod):
    """A monotonic rise has zero downside → RSI must be 100, not the NaN fill."""
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert mod.calc_rsi(s).iloc[-1] == pytest.approx(100.0)


def test_rsi_monotonic_fall_is_zero(mod):
    s = pd.Series(np.arange(100, 0, -1, dtype=float))
    assert mod.calc_rsi(s).iloc[-1] == pytest.approx(0.0)


def test_rsi_bounds(mod, df):
    r = mod.calc_rsi(df["close"])
    assert r.min() >= 0 and r.max() <= 100


def test_rsi_flat_series_is_neutral(mod, df_flat):
    r = mod.calc_rsi(df_flat["close"])
    assert r.iloc[-1] == pytest.approx(50.0)


def test_atr_matches_reference(mod, df):
    np.testing.assert_allclose(mod.calc_atr(df).to_numpy(),
                               ref_atr(df).to_numpy(), rtol=1e-9)


def test_atr_uses_wilder_alpha_not_simple_ma(mod, df):
    """Guard against regressions to span=period (alpha = 2/(n+1))."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    wrong = tr.ewm(span=14, adjust=False).mean()
    right = mod.calc_atr(df)
    assert not np.allclose(wrong.to_numpy()[-50:], right.to_numpy()[-50:])


def test_cci_matches_reference(mod, df):
    got = mod.calc_cci(df).to_numpy()
    ref = ref_cci(df).fillna(0).round(1).to_numpy()
    np.testing.assert_allclose(got[25:], ref[25:], atol=0.11)


def test_williams_r_matches_reference_and_bounds(mod, df):
    got = mod.calc_williams_r(df).to_numpy()
    ref = ref_williams_r(df).fillna(-50).round(1).to_numpy()
    np.testing.assert_allclose(got[20:], ref[20:], atol=0.11)
    assert got.min() >= -100.001 and got.max() <= 0.001


def test_mfi_matches_reference(mod, df):
    got = mod.calc_mfi(df).to_numpy()
    ref = ref_mfi(df).fillna(50).round(1).to_numpy()
    # allow divergence only where one side has zero flow (the 50/100 edge case)
    np.testing.assert_allclose(got[20:], ref[20:], atol=0.11)


def test_mfi_all_positive_flow_is_100(mod):
    n = 60
    df = pd.DataFrame({
        "ts": pd.date_range("2023-01-02", periods=n, tz="Asia/Kolkata"),
        "open": np.arange(100, 100 + n, dtype=float),
        "high": np.arange(101, 101 + n, dtype=float),
        "low": np.arange(99, 99 + n, dtype=float),
        "close": np.arange(100, 100 + n, dtype=float),
        "vol": np.full(n, 1000.0), "oi": 0.0})
    assert mod.calc_mfi(df).iloc[-1] == pytest.approx(100.0)


def test_mfi_flat_series_is_neutral(mod, df_flat):
    assert mod.calc_mfi(df_flat).iloc[-1] == pytest.approx(50.0)


def test_macd_identity(mod, df):
    macd, signal, hist = mod.calc_macd(df["close"])
    np.testing.assert_allclose(hist.to_numpy(),
                               (macd - signal).to_numpy(), rtol=1e-12)
    np.testing.assert_allclose(
        macd.to_numpy(),
        (df["close"].ewm(span=12, adjust=False).mean()
         - df["close"].ewm(span=26, adjust=False).mean()).to_numpy(), rtol=1e-12)


def test_stochastic_bounds_and_identity(mod, df):
    k, d = mod.calc_stochastic(df)
    assert d.notna().all(), "%D must not leak NaN into the signal engine"
    assert k.between(0, 100).all()
    assert d.between(0, 100).all()
    low_min = df["low"].rolling(14).min()
    high_max = df["high"].rolling(14).max()
    exp = ((df["close"] - low_min) / (high_max - low_min) * 100).fillna(50)
    np.testing.assert_allclose(k.to_numpy()[14:], exp.to_numpy()[14:], atol=1e-9)


def test_supertrend_direction_flips_and_values(mod, df_uptrend, df_downtrend):
    st_up, dir_up = mod.calc_supertrend(df_uptrend)
    st_dn, dir_dn = mod.calc_supertrend(df_downtrend)
    assert dir_up.iloc[-1] == 1
    assert dir_dn.iloc[-1] == -1
    # in an uptrend the ST line must sit below price; in a downtrend above
    assert st_up.iloc[-1] < df_uptrend["close"].iloc[-1]
    assert st_dn.iloc[-1] > df_downtrend["close"].iloc[-1]
    assert set(np.unique(dir_up)) <= {-1, 1}


def test_supertrend_band_is_continuous(mod, df):
    """SuperTrend must never produce NaNs and must not be constant."""
    st, d = mod.calc_supertrend(df)
    assert st.notna().all()
    assert st.nunique() > 10


def test_adx_matches_manual_wilder(mod, df):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    up, dn = h - h.shift(1), l.shift(1) - l
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = 1 / 14
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
    ndi = 100 * ndm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
    dx = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=a, adjust=False).mean()
    got_adx, got_pdi, got_ndi = mod.calc_adx(df)
    np.testing.assert_allclose(got_adx.to_numpy()[30:], adx.round(1).to_numpy()[30:], atol=0.11)
    np.testing.assert_allclose(got_pdi.to_numpy()[30:], pdi.round(1).to_numpy()[30:], atol=0.11)
    np.testing.assert_allclose(got_ndi.to_numpy()[30:], ndi.round(1).to_numpy()[30:], atol=0.11)


def test_adx_bounds(mod, df):
    adx, pdi, ndi = mod.calc_adx(df)
    assert adx.between(0, 100).all()
    assert pdi.between(0, 100).all()
    assert ndi.between(0, 100).all()


def test_adx_short_frame_is_neutral_not_zero(mod):
    """With too little history ADX must read 'no trend' (20), not 0 — 0 would
    make ADX look maximally range-bound and break the >25 trending test."""
    small = make_ohlcv(n=8)
    adx, pdi, ndi = mod.calc_adx(small)
    assert len(adx) == 8 and (adx == 20).all()


def test_volume_profile_volume_conservation(mod, df):
    profile, hvn, lvn, poc = mod.calc_volume_profile(df.tail(60), n_bins=24)
    total = sum(v for _, v, _ in profile)
    assert total == pytest.approx(df.tail(60)["vol"].sum(), rel=1e-6)
    assert len(profile) == 24
    assert poc > 0
    assert [p for p, _, _ in hvn][0] == poc


def test_volume_profile_zero_range_safe(mod, df_flat):
    profile, hvn, lvn, poc = mod.calc_volume_profile(df_flat, n_bins=24)
    assert profile == [] and poc == 0.0


def test_support_resistance_side_of_price(mod, df):
    sup, res = mod.calc_support_resistance(df)
    px = float(df["close"].iloc[-1])
    assert all(s < px for s in sup)
    assert all(r > px for r in res)


def test_fibonacci_level_labels_and_ordering(mod, df):
    fib = mod.calc_fibonacci_levels(df)
    assert {"fib_0", "fib_38.2", "fib_50", "fib_61.8", "fib_100"} <= set(fib)
    assert fib["fib_0"] == pytest.approx(fib["sw_high"])
    assert fib["fib_100"] == pytest.approx(fib["sw_low"])
    assert fib["fib_38.2"] > fib["fib_50"] > fib["fib_61.8"]


def test_fibonacci_zero_range_returns_empty(mod, df_flat):
    assert mod.calc_fibonacci_levels(df_flat) == {}


def test_52w_uses_tf_window(mod):
    df = make_ohlcv(n=300)
    w = mod.calc_52w_levels(df, window_bars=52)
    assert w["high_52w"] == pytest.approx(float(df["high"].tail(52).max()), abs=0.01)
    assert w["low_52w"] == pytest.approx(float(df["low"].tail(52).min()), abs=0.01)


def test_gap_detection_up_down_and_filled(mod):
    ts = pd.date_range("2024-01-01", periods=3, tz="Asia/Kolkata")
    df = pd.DataFrame({
        "ts": ts, "open": [100, 100, 110], "high": [101, 105, 112],
        "low": [99, 95, 98], "close": [100, 100, 111],
        "vol": [1e5, 1e5, 1e5], "oi": 0.0})
    g = mod.calc_gap(df)
    assert g["gap_type"] == "GAP_UP"
    assert g["gap_pct"] == pytest.approx(10.0)
    assert g["gap_filled"] is True        # low 98 <= prev close 100
    df2 = df.copy()
    df2.loc[2, ["open", "high", "low", "close"]] = [90, 92, 88, 89]
    g2 = mod.calc_gap(df2)
    assert g2["gap_type"] == "GAP_DOWN"
    assert g2["gap_filled"] is False      # high 92 < prev close 100


def test_gap_no_gap_within_threshold(mod):
    ts = pd.date_range("2024-01-01", periods=2, tz="Asia/Kolkata")
    df = pd.DataFrame({"ts": ts, "open": [100, 100.1], "high": [101, 101],
                       "low": [99, 99], "close": [100, 100.1],
                       "vol": [1e5, 1e5], "oi": 0.0})
    assert mod.calc_gap(df)["gap_type"] == "NO_GAP"


def test_indicators_do_not_mutate_caller_frame(mod, df):
    before = df.copy()
    mod.calculate_indicators(df, "DAY")
    pd.testing.assert_frame_equal(df, before)


def test_indicators_short_frame_is_passed_through(mod):
    small = make_ohlcv(n=20)          # below every TF_MIN_CANDLES
    out = mod.calculate_indicators(small, "DAY")
    assert "rsi" not in out.columns


def test_indicators_vwap_intraday_only(mod):
    df = make_ohlcv(n=300, freq="30min")
    out_day = mod.calculate_indicators(df.copy(), "DAY")
    out_5m = mod.calculate_indicators(df.copy(), "5MIN")
    assert out_day["vwap"].isna().all()
    assert out_5m["vwap"].notna().any()


def test_indicators_vwap_resets_each_session(mod):
    df = make_ohlcv(n=300, freq="30min")
    out = mod.calculate_indicators(df, "5MIN")
    grp = out.groupby(df["ts"].dt.date.astype(str))
    # VWAP is session-cumulative: it must stay inside the session's
    # running high/low envelope, and reset at the start of each new day.
    assert (out["vwap"] <= grp["high"].cummax() + 1e-9).all()
    assert (out["vwap"] >= grp["low"].cummin() - 1e-9).all()
    first_bar = out.groupby(df["ts"].dt.date.astype(str)).head(1)
    tp_first = (first_bar["high"] + first_bar["low"] + first_bar["close"]) / 3
    np.testing.assert_allclose(first_bar["vwap"].to_numpy(),
                               tp_first.to_numpy(), rtol=1e-9)


def test_safe_float_handles_nan_and_junk(mod):
    assert mod.safe_float(float("nan")) == 0.0
    assert mod.safe_float("abc", 5.0) == 5.0
    assert mod.safe_float(None, -1.0) == -1.0
    assert mod.safe_float("3.5") == 3.5
