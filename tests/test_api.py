"""
Upstox API-layer tests: URL construction, date handling, chunking, retries,
error handling and the SQLite candle cache.

These run entirely offline against `tests/fake_upstox.py`, which reproduces the
documented V3 contract (see docs/AUDIT.md for the doc references).
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from tests.fake_upstox import FakeUpstox, make_daily_series

KEY = "NSE_EQ|INE002A01018"
ENC = "NSE_EQ%7CINE002A01018"


@pytest.fixture
def api(mod, monkeypatch):
    """Install a FakeUpstox as the module's `requests` and return it."""
    fake = FakeUpstox()
    monkeypatch.setattr(mod, "requests", fake, raising=False)
    return fake


def _hist_urls(fake):
    return [u for u in fake.calls if "/intraday/" not in u]


def _live_urls(fake):
    return [u for u in fake.calls if "/intraday/" in u]


# ── URL construction ─────────────────────────────────────────
def test_historical_url_matches_v3_contract(mod, api):
    api.daily = make_daily_series(n=30)
    mod.fetch_historical(KEY, "days", "1", 10, {}, verbose=False)
    urls = _hist_urls(api)
    assert urls, "no historical request was made"
    u = urls[0]
    assert u.startswith("https://api.upstox.com/v3/historical-candle/")
    assert ENC in u, "instrument_key must be URL-encoded (pipe -> %7C)"
    tail = u.split(ENC + "/", 1)[1]
    unit, interval, to_d, from_d = tail.split("/")
    assert (unit, interval) == ("days", "1")
    date.fromisoformat(to_d) and date.fromisoformat(from_d)
    assert date.fromisoformat(from_d) <= date.fromisoformat(to_d)


def test_intraday_url_matches_v3_contract(mod, api):
    mod.fetch_intraday_v3(KEY, "minutes", "5", {}, verbose=False)
    u = _live_urls(api)[0]
    assert u == ("https://api.upstox.com/v3/historical-candle/intraday/"
                 + ENC + "/minutes/5")


def test_intraday_url_for_daily_live_bar(mod, api):
    mod.fetch_intraday_v3(KEY, "days", "1", {}, verbose=False)
    assert _live_urls(api)[0].endswith("/intraday/" + ENC + "/days/1")


# ── date-window handling ─────────────────────────────────────
def test_to_date_and_from_date_are_never_weekends(mod, api):
    api.daily = make_daily_series(n=1200)
    for unit, val, lb in [("days", "1", 2500), ("minutes", "5", 30),
                          ("hours", "1", 90)]:
        api.calls.clear()
        if unit != "days":
            api.daily = None          # intraday history unsupported by fake
        mod.fetch_historical(KEY, unit, val, lb, {}, verbose=False)
        for u in _hist_urls(api):
            to_d = date.fromisoformat(u.rsplit("/", 2)[-2])
            from_d = date.fromisoformat(u.rsplit("/", 1)[-1])
            assert to_d.weekday() < 5, "to_date fell on a weekend: " + u
            assert from_d.weekday() < 5, "from_date fell on a weekend: " + u


def test_to_date_is_never_today_or_future(mod, api):
    api.daily = make_daily_series(n=600)
    mod.fetch_historical(KEY, "days", "1", 400, {}, verbose=False)
    for u in _hist_urls(api):
        to_d = date.fromisoformat(u.rsplit("/", 2)[-2])
        assert to_d < date.today()


# ── chunking ─────────────────────────────────────────────────
def test_long_range_is_chunked_without_gaps(mod, api):
    api.daily = make_daily_series(n=1600)      # ~6 yrs of business days
    mod.fetch_historical(KEY, "days", "1", 2500, {}, verbose=False)
    urls = _hist_urls(api)
    assert len(urls) > 1, "expected the 2500-day range to be chunked"
    spans = []
    for u in urls:
        to_d = date.fromisoformat(u.rsplit("/", 2)[-2])
        from_d = date.fromisoformat(u.rsplit("/", 1)[-1])
        spans.append((from_d, to_d))
    spans.sort()
    # adjacent chunks must touch: next.from <= prev.to + 1 calendar day
    for (f1, t1), (f2, t2) in zip(spans, spans[1:]):
        assert f2 <= t1 + timedelta(days=1), "gap between chunks %s %s" % (t1, f2)


def test_chunk_size_respects_documented_limit(mod, api):
    api.daily = make_daily_series(n=1600)
    mod.fetch_historical(KEY, "days", "1", 2500, {}, verbose=False)
    limit = mod._HIST_MAX_DAYS["days"]
    for u in _hist_urls(api):
        to_d = date.fromisoformat(u.rsplit("/", 2)[-2])
        from_d = date.fromisoformat(u.rsplit("/", 1)[-1])
        assert (to_d - from_d).days <= limit + 1


# ── response parsing ─────────────────────────────────────────
def test_parse_handles_seven_column_response(mod):
    rows = [["2024-04-01T09:15:00+05:30", 1, 2, 0.5, 1.5, 100, 7]]
    df = mod._parse_candle_df(rows)
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "vol", "oi"]
    assert df["oi"].iloc[0] == 7


def test_parse_handles_six_column_response(mod):
    rows = [["2024-04-01T09:15:00+05:30", 1, 2, 0.5, 1.5, 100]]
    df = mod._parse_candle_df(rows)
    assert df["oi"].iloc[0] == 0


def test_parse_sorts_newest_first_and_dedupes(mod):
    rows = [
        ["2024-04-03T09:15:00+05:30", 3, 3, 3, 3, 1, 0],
        ["2024-04-01T09:15:00+05:30", 1, 1, 1, 1, 1, 0],
        ["2024-04-02T09:15:00+05:30", 2, 2, 2, 2, 1, 0],
        ["2024-04-02T09:15:00+05:30", 2, 2, 2, 2, 1, 0],
    ]
    df = mod._parse_candle_df(rows)
    assert len(df) == 3
    assert df["ts"].is_monotonic_increasing


def test_parse_preserves_ist_wall_clock_for_naive_timestamps(mod):
    """Upstox NSE timestamps are IST. A naive string must NOT be treated as UTC
    (that would shift every candle by +5:30 and move bars across day boundaries)."""
    rows = [["2024-04-01T09:15:00", 100, 101, 99, 100, 1000, 0]]
    df = mod._parse_candle_df(rows)
    ts = pd.Timestamp(df["ts"].iloc[0])
    assert ts.strftime("%Y-%m-%d %H:%M") == "2024-04-01 09:15"
    assert ts.strftime("%z") in ("+0530",)


def test_parse_converts_aware_timestamps_to_ist(mod):
    rows = [["2024-04-01T09:15:00+00:00", 100, 101, 99, 100, 1000, 0]]
    df = mod._parse_candle_df(rows)
    assert pd.Timestamp(df["ts"].iloc[0]).strftime("%H:%M") == "14:45"


def test_parse_empty_returns_empty_frame(mod):
    assert mod._parse_candle_df([]).empty


# ── retries / error handling ─────────────────────────────────
def test_empty_200_is_not_retried(mod, api):
    api.daily = None
    mod.fetch_historical(KEY, "days", "1", 30, {}, verbose=False)
    assert len(api.calls) == 1, "empty payload must short-circuit, not retry"


def test_429_is_retried_and_then_succeeds(mod, api):
    api.daily = make_daily_series(n=60)
    api.fail_codes = [429]
    out = mod.fetch_historical(KEY, "days", "1", 30, {}, verbose=False)
    assert len(api.calls) == 2
    assert not out.empty


def test_transient_error_retries_three_times_then_gives_up(mod, api):
    api.fail_codes = [500, 500, 500, 500]
    out = mod.fetch_historical(KEY, "days", "1", 30, {}, verbose=False)
    assert len(api.calls) == 3
    assert out.empty


def test_invalid_token_aborts_immediately(mod, api):
    """401/403 means the token is dead — retrying wastes a full scan's worth of
    rate limit and hides the real problem behind 'no data' messages."""
    api.fail_codes = [401, 401, 401, 401, 401]
    with pytest.raises(mod.TokenError):
        mod._fetch_historical_single(KEY, "days", "1", "2024-01-01",
                                     "2024-02-01", {}, verbose=False)
    assert len(api.calls) == 1, "must not retry an auth failure"


def test_scan_aborts_on_invalid_token(mod, api):
    """One 401 must stop the whole symbol — not retry across every TF."""
    api.fail_codes = [401] * 500
    data = {}
    with pytest.raises(mod.TokenError):
        mod._scan_one_symbol(KEY, "RELIANCE", data,
                             ["DAY", "WEEK", "MONTH"], {})
    assert len(api.calls) == 1, "must not keep calling with a dead token"


def test_auto_scan_survives_dead_token(mod, api, monkeypatch):
    """auto_scan must report the auth failure instead of crashing or
    silently walking all 50 symbols."""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    import tempfile, os, json
    tokdir = tempfile.mkdtemp()
    tokfile = os.path.join(tokdir, "upstox_token.json")
    with open(tokfile, "w") as f:
        json.dump({"access_token": "dead-token"}, f)
    monkeypatch.setattr(mod, "TOKEN_FILE", tokfile, raising=False)
    api.fail_codes = [401] * 5000
    monkeypatch.setattr(mod, "SYMBOL_MAP", {"K1": "AAA", "K2": "BBB"},
                        raising=False)
    data = mod.auto_scan({}, ["DAY"])
    assert len(api.calls) == 1
    assert data is not None


def test_live_daily_bar_fetched_once_per_symbol_per_scan(mod, api):
    """DAY, WEEK and MONTH all resample from the same daily bar — it must be
    downloaded once, not three times (recurring cost of every scan)."""
    api.daily = make_daily_series(n=600)
    mod.start_scan_pass()
    for tf in ("DAY", "WEEK", "MONTH"):
        mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False, tf_name=tf)
    live = [u for u in _live_urls(api) if u.endswith("/days/1")]
    assert len(live) == 1, "expected one live daily-bar call, got %d" % len(live)


# ── candle cache ─────────────────────────────────────────────
def test_second_fetch_hits_cache_with_no_api_calls(mod, api):
    api.daily = make_daily_series(n=600)
    api.intraday = {}
    first = mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False,
                              tf_name="DAY")
    n_after_first = len(api.calls)
    assert not first.empty
    second = mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False,
                               tf_name="DAY")
    assert len(api.calls) == n_after_first, "cache should avoid all API calls"
    assert len(second) == len(first)


def test_cache_incremental_fill_only_downloads_gap(mod, api):
    api.daily = make_daily_series(n=600)
    mod.fetch_historical_cached(KEY, "days", "1", 2500, {}, verbose=False,
                                cache_tf="DAY")
    n1 = len(_hist_urls(api))
    # extend the ground truth forward by 40 business days
    extra = make_daily_series(n=40, end=date.today() - timedelta(days=1), seed=99)
    api.daily = extra
    mod.fetch_historical_cached(KEY, "days", "1", 2500, {}, verbose=False,
                                cache_tf="DAY")
    n2 = len(_hist_urls(api)) - n1
    assert n2 <= 2, "incremental fill must not re-download the whole history"


def test_daily_series_has_no_missing_business_days(mod, api, monkeypatch):
    """The daily series handed to the indicators must be contiguous.

    Regression: the cache used to stop at T-2 (yesterday's bar was skipped as
    'not yet settled') while the live intraday call only returns today's bar,
    leaving a permanent one-day hole in the EMA/RSI/ATR inputs.
    """
    today = date.today()
    hist = make_daily_series(n=200, end=today - timedelta(days=1), seed=5)
    api.daily = hist
    live = pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [101.0], "high": [103.0], "low": [100.0], "close": [102.0],
        "vol": [12345.0], "oi": [0.0]})
    api.intraday = {("days", "1"): live}
    out = mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False,
                            tf_name="DAY")
    assert not out.empty
    last_hist = pd.Timestamp([t for t in out["ts"]][-2]).date()
    assert (pd.Timestamp(out["ts"].iloc[-1]).date() - last_hist).days <= 4, (
        "missing bars between %s and the live bar" % last_hist)


def test_live_bar_is_never_written_to_cache(mod, api):
    today = date.today()
    api.daily = make_daily_series(n=200, end=today - timedelta(days=1))
    live = pd.DataFrame({
        "ts": [pd.Timestamp(today).tz_localize("Asia/Kolkata")],
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
        "vol": [1.0], "oi": [0.0]})
    api.intraday = {("days", "1"): live}
    mod.fetch_candles(KEY, "days", "1", 600, {}, verbose=False, tf_name="DAY")
    cached = mod._cache_last_date(KEY, "DAY")
    assert cached is not None and cached < today, (
        "today's forming bar must not be cached (it would freeze a partial OHLC)")


def test_week_and_month_resample_from_daily(mod, api):
    api.daily = make_daily_series(n=600)
    wk = mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False,
                           tf_name="WEEK")
    mo = mod.fetch_candles(KEY, "days", "1", 2500, {}, verbose=False,
                           tf_name="MONTH")
    assert not wk.empty and not mo.empty
    assert len(wk) < len(mo) * 6          # sanity: weekly bars >> monthly bars
    assert all(u.endswith("/days/1/") or "/days/1/" in u for u in _hist_urls(api))


# ── rate limiting ────────────────────────────────────────────
def test_api_calls_are_rate_limited(mod, api, monkeypatch):
    """A full scan issues ~2 calls per symbol/TF. Without a global throttle the
    scanner bursts well past Upstox's per-second limits and eats 429s."""
    slept = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
    api.daily = make_daily_series(n=600)
    for _ in range(6):
        mod.fetch_historical(KEY, "days", "1", 300, {}, verbose=False)
    assert slept, "expected throttling sleeps between API calls"
