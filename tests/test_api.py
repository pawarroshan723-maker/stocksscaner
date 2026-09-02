"""
Upstox API-layer tests: URL construction, date handling, chunking, retries,
error handling and the SQLite candle cache.

These run entirely offline against `tests/fake_upstox.py`, which reproduces the
documented V3 contract (see docs/AUDIT.md for the doc references).
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from tests.fake_upstox import FakeUpstox, make_daily_series, today_ist

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
        assert to_d < today_ist()


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
    extra = make_daily_series(n=40, end=today_ist() - timedelta(days=1), seed=99)
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
    today = today_ist()
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
    today = today_ist()
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


# ── IST clock & NSE holiday calendar ────────────────────────
def test_today_ist_matches_the_scanner_clock(mod):
    """_today_ist() must resolve against Asia/Kolkata, not the host zone.

    Regression: the codebase used date.today(), so on any machine set to a
    zone behind IST the scanner was a day out for most of the trading day.
    """
    import datetime as _dt
    expected = _dt.datetime.now(
        _dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()
    assert mod._today_ist() == expected
    assert mod._today_ist() != _dt.date.today() or True   # never compare to local


def test_last_trading_day_skips_weekends_and_holidays(mod):
    # 2026-10-02 is Gandhi Jayanti (Friday) and 2026-10-20 is Dussehra (Tuesday).
    for holiday in (mod.date(2026, 10, 2), mod.date(2026, 10, 20)):
        assert holiday in mod.NSE_HOLIDAYS
        stepped = mod._last_trading_day(holiday)
        assert stepped < holiday, "must step back off a holiday"
        assert mod._is_trading_day(stepped)

    # a Saturday steps back to the preceding Friday
    sat = mod.date(2026, 10, 3)
    assert sat.weekday() == 5
    assert mod._last_trading_day(sat) < sat
    assert mod._last_trading_day(sat).weekday() < 5


def test_next_trading_day_skips_weekends_and_holidays(mod):
    # stepping forward off a holiday must not land on the holiday itself
    d = mod._next_trading_day(mod.date(2026, 10, 2))
    assert d > mod.date(2026, 10, 2)
    assert mod._is_trading_day(d)
    # and never on a weekend
    assert mod._next_trading_day(mod.date(2026, 10, 3)).weekday() < 5


def test_is_market_open_returns_false_on_a_holiday(mod, monkeypatch):
    import datetime as _dt

    class FrozenDT(_dt.datetime):
        _when = _dt.datetime(2026, 10, 2, 11, 0, tzinfo=mod.IST)  # Gandhi Jayanti

        @classmethod
        def now(cls, tz=None):
            return cls._when if tz is None else cls._when.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", FrozenDT)
    # 11:00 on a Friday is inside trading hours — only the holiday stops it.
    assert mod._is_trading_day(_dt.date(2026, 10, 2)) is False
    assert mod.is_market_open() is False


def test_stale_holiday_calendar_warns(mod, monkeypatch, capsys):
    """A year missing from NSE_HOLIDAYS must not pass silently."""
    monkeypatch.setattr(mod, "_NSE_HOLIDAY_YEARS", {1970})
    mod._warn_stale_holiday_calendar()
    assert "NSE_HOLIDAYS" in capsys.readouterr().out

    monkeypatch.setattr(mod, "_NSE_HOLIDAY_YEARS", {mod._today_ist().year})
    capsys.readouterr()
    mod._warn_stale_holiday_calendar()
    assert capsys.readouterr().out == ""


# ── Cache hole repair ────────────────────────────────────────
def test_cache_repairs_a_hole_in_the_middle(mod, api, monkeypatch):
    """A partially-failed chunked download must not become a permanent gap.

    Regression: the cache was keyed on MAX(ts), which says nothing about what
    sits in between. A chunked download that half-failed left the newest bar
    in place, so every later scan saw `last_cached >= effective_to`, declared
    a full cache hit, and never refilled the hole — permanently. EMA/MACD/RSI
    were then computed on a series with months missing out of the middle.
    """
    mod._GAP_REPAIR_DONE.clear()
    full = make_daily_series(n=300, seed=3)
    api.daily = full

    # Seed a cache that reaches T-1 but is missing 100 sessions in the middle.
    hole = pd.concat([full.iloc[:100], full.iloc[200:]]).reset_index(drop=True)
    mod._cache_save(KEY, "DAY", hole)
    assert mod._cache_last_date(KEY, "DAY") is not None

    out = mod.fetch_historical_cached(KEY, "days", "1", 2500, {},
                                      verbose=False, cache_tf="DAY")
    missing = sorted(set(full["ts"]) - set(out["ts"]))
    assert not missing, "cache still has {0} missing bars".format(len(missing))
    assert len(out) == len(full)


def test_cache_without_a_hole_does_not_refetch(mod, api):
    """A complete cache must still short-circuit to a plain cache hit.

    The repair scan must not turn every cache hit into a re-download — that
    would defeat the cache and hammer the API on every scan pass.
    """
    mod._GAP_REPAIR_DONE.clear()
    api.daily = make_daily_series(n=300, seed=5)
    first = mod.fetch_historical_cached(KEY, "days", "1", 2500, {},
                                        verbose=False, cache_tf="DAY")
    assert not first.empty
    calls_after_first = len(api.calls)

    second = mod.fetch_historical_cached(KEY, "days", "1", 2500, {},
                                         verbose=False, cache_tf="DAY")
    assert len(api.calls) == calls_after_first, "complete cache was refetched"
    assert len(second) == len(first)


def test_cache_missing_days_ignores_weekends_and_holidays(mod, api):
    """_cache_missing_days must only count sessions the exchange actually held."""
    mod._GAP_REPAIR_DONE.clear()
    full = make_daily_series(n=200, seed=6)
    api.daily = full
    mod.fetch_historical_cached(KEY, "days", "1", 2500, {},
                                verbose=False, cache_tf="DAY")
    cached = mod._cache_load(KEY, "DAY")
    lo = cached["ts"].min().date()
    hi = cached["ts"].max().date()
    assert mod._cache_missing_days(cached, lo, hi) == [], \
        "a freshly downloaded cache reported gaps"


def test_cache_missing_days_finds_a_planted_hole(mod, api):
    mod._GAP_REPAIR_DONE.clear()
    full = make_daily_series(n=200, seed=7)
    api.daily = full
    mod.fetch_historical_cached(KEY, "days", "1", 2500, {},
                                verbose=False, cache_tf="DAY")
    cached = mod._cache_load(KEY, "DAY")
    lo, hi = cached["ts"].min().date(), cached["ts"].max().date()

    # Punch out a 10-session block from the middle.
    victim = sorted(cached["ts"])[60:70]
    pruned = cached[~cached["ts"].isin(victim)]
    missing = mod._cache_missing_days(pruned, lo, hi)
    # make_daily_series emits business days; _is_trading_day additionally
    # skips NSE holidays, so a victim date may legitimately not be a session.
    expected = sorted(pd.Timestamp(d).date() for d in victim
                      if mod._is_trading_day(pd.Timestamp(d).date()))
    assert missing == expected
    assert missing, "no gap detected in a deliberately pruned cache"


def test_cache_missing_days_survives_junk(mod):
    assert mod._cache_missing_days(None, None, None) == []
    assert mod._cache_missing_days(pd.DataFrame(), date(2026, 1, 1), date(2026, 2, 1)) == []
    assert mod._cache_missing_days(pd.DataFrame({"ts": []}), date(2026, 2, 1), date(2026, 1, 1)) == []


def _weekday_span(days, end):
    """Every Mon–Fri over `days` calendar days ending at `end`."""
    start = end - timedelta(days=days)
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return start, out


def test_cache_gap_detector_ignores_isolated_market_closures(mod):
    """A healthy multi-year cache must report no gaps at all.

    Regression: every absent weekday was judged individually, so with a
    holiday calendar covering only the current year, ~73 legitimate NSE
    closures across seven years were reported as holes. Every symbol then
    logged "CACHE GAP ... still incomplete — one repair attempt per run" and
    burned an API call chasing dates the exchange never traded.
    """
    end = date(2026, 9, 1)
    start, sessions = _weekday_span(2500, end)
    # Simulate closures: real ones in covered years, synthetic ones elsewhere
    # (the detector cannot know about these, which is the whole point).
    import random
    rng = random.Random(7)
    closed = set()
    for y in range(2019, 2027):
        if y in mod._NSE_HOLIDAY_YEARS:
            closed |= {h for h in mod.NSE_HOLIDAYS if h.year == y}
            continue
        for _ in range(16):
            d = date(y, rng.randint(1, 12), rng.randint(1, 28))
            if d.weekday() < 5:
                closed.add(d)
    kept = [s for s in sessions if s not in closed]
    assert len(closed) > 40, "test needs a realistic number of closures"
    cache = pd.DataFrame({"ts": pd.to_datetime(kept)})
    assert mod._cache_missing_days(cache, start, end) == []


def test_cache_gap_detector_flags_a_long_contiguous_run(mod):
    """A failed chunk drops a whole window — that must still be found."""
    end = date(2026, 9, 1)
    start, sessions = _weekday_span(400, end)
    hole = sessions[100:106]
    pruned = [s for s in sessions if s not in hole]
    got = mod._cache_missing_days(pd.DataFrame({"ts": pd.to_datetime(pruned)}),
                                  start, end)
    assert got == hole


def test_cache_gap_detector_ignores_short_holes(mod):
    """Holes shorter than a failed chunk are deliberately left alone.

    A one-to-four session gap self-heals through the ordinary partial-fetch
    path (last_cached < effective_to triggers a top-up), so treating it as a
    hole here would only add false positives around Diwali clusters.
    """
    end = date(2026, 9, 1)
    start, sessions = _weekday_span(400, end)
    for n in (1, 2, 3, 4):
        hole = sessions[100:100 + n]
        pruned = [s for s in sessions if s not in hole]
        got = mod._cache_missing_days(pd.DataFrame({"ts": pd.to_datetime(pruned)}),
                                      start, end)
        assert got == [], "a %d-session hole was flagged" % n


def test_cache_gap_detector_excludes_known_holidays_from_runs(mod):
    """In a covered year a known closure must not extend a run."""
    end = date(2026, 9, 1)
    start, sessions = _weekday_span(400, end)
    known = [h for h in mod.NSE_HOLIDAYS
             if start <= h <= end and h in sessions]
    assert known, "need a covered-year holiday inside the window"
    # Remove the holiday AND the sessions around it; the holiday itself must
    # not be counted towards the run length.
    victim = [d for d in sessions
              if known[0] - timedelta(days=2) <= d <= known[0] + timedelta(days=1)]
    pruned = [s for s in sessions if s not in victim]
    got = mod._cache_missing_days(pd.DataFrame({"ts": pd.to_datetime(pruned)}),
                                  start, end)
    assert known[0] not in got, "a known market closure was reported as a gap"
