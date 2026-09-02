#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║        MASTER SCANNER v8.0 PRO  —  Upstox Edition                  ║
║  Multi-TF | MACD | EMA Cross | SuperTrend | S/R | Fibonacci        ║
║  Candlestick Patterns | RSI Divergence | Composite Score           ║
║  Watchlist | 52W H/L | Risk:Reward | CSV Export | Best Setups      ║
║  Heatmap | Gap Scanner | Volume Profile | Auto-Schedule Scan        ║
║  NEW v8.0: ADX | Williams %%R | CCI | MFI | Momentum Screener      ║
╚══════════════════════════════════════════════════════════════════════╝

SETUP:
  pip install requests pandas numpy pytz

TOKEN SETUP:
  1. Login at https://upstox.com/developer/
  2. Generate your access_token
  3. Create file: upstox_token.json
     Content: { "access_token": "YOUR_TOKEN_HERE" }
  4. Place in same folder as this script

PRO FEATURES (v6.0):
  • Candlestick Pattern Detection (Hammer, Engulfing, Doji, Star, etc.)
  • RSI Divergence (Regular & Hidden, Bullish & Bearish)
  • Fibonacci Retracement / Extension levels
  • Composite Signal Score (0-100) — multi-factor quality rating
  • Watchlist / Favorites system
  • 52-Week High / Low proximity tracking
  • Risk:Reward Ratio on every trade setup
  • CSV Export (machine-readable for spreadsheets)
  • Best Setups view (ranked by composite score)
  • Signal Change Detection (↑↓ marker when signal flipped)
"""

import re
import requests
import pandas as pd
import numpy as np
import json
import os
import sys
import time
import math
import sqlite3
from datetime import datetime, timedelta, date
from urllib.parse import quote
import pytz

# ─────────────────────────────────────────────────────────────
#  ANSI COLOR CODES
# ─────────────────────────────────────────────────────────────

USE_COLOR = sys.stdout.isatty()  # Auto-detect terminal

class C:
    RESET  = "\033[0m"   if USE_COLOR else ""
    BOLD   = "\033[1m"   if USE_COLOR else ""
    DIM    = "\033[2m"   if USE_COLOR else ""
    RED    = "\033[91m"  if USE_COLOR else ""
    GREEN  = "\033[92m"  if USE_COLOR else ""
    YELLOW = "\033[93m"  if USE_COLOR else ""
    BLUE   = "\033[94m"  if USE_COLOR else ""
    CYAN   = "\033[96m"  if USE_COLOR else ""
    WHITE  = "\033[97m"  if USE_COLOR else ""
    MAGENTA= "\033[95m"  if USE_COLOR else ""

def cprint(text, color="", bold=False):
    prefix = (C.BOLD if bold else "") + color
    print(prefix + text + C.RESET)

def signal_color(sig):
    return {
        "BREAKOUT":  C.GREEN,
        "BREAKDOWN": C.RED,
        "SIDEWAYS":  C.YELLOW,
        "NONE":      C.DIM,
    }.get(sig, "")

# ─────────────────────────────────────────────────────────────
#  ANSI-AWARE COLUMN ALIGNMENT HELPERS
#  Python's str.format() counts invisible escape codes as
#  characters → every colored field drifts rightward.
#  These helpers measure *visible* length and pad correctly.
# ─────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r'(?:\x1b|\033)\[[0-9;]*[mKHJABCDfsuhl]')

def _vlen(s):
    """Visible (printable) length — strips all ANSI escape codes.
    Covers both \\x1b and \\033 prefix forms and all common CSI sequences."""
    return len(_ANSI_RE.sub('', str(s)))

def _ljust(s, w):
    """Left-justify s to visible width w."""
    s = str(s)
    return s + ' ' * max(0, w - _vlen(s))

def _rjust(s, w):
    """Right-justify s to visible width w."""
    s = str(s)
    return ' ' * max(0, w - _vlen(s)) + s

def _col(parts, seps=None):
    """Join pre-padded column strings with separators (default single space)."""
    if seps is None:
        return ' '.join(parts)
    result = ''
    for i, p in enumerate(parts):
        result += p + (seps[i] if i < len(seps) else '')
    return result

# ─────────────────────────────────────────────────────────────
#  USER CONFIGURATION
# ─────────────────────────────────────────────────────────────

TOKEN_FILE     = "upstox_token.json"          # ← kept as-is (JSON token file)
DB_FILE        = "master_scanner.db"           # ← SQLite DB (replaces 3 JSON files)
DATA_FILE      = DB_FILE                       # alias used in menu display
IST            = pytz.timezone("Asia/Kolkata")

# ── NSE MARKET HOURS GUARD ───────────────────────────────────
# NSE trades Mon–Fri, 09:15–15:30 IST.
# Intraday scans outside these hours produce signals stamped with
# the current wall-clock time but computed from *yesterday's* closed
# candles — misleading entries in the history log (e.g. "BD at 00:55").

MARKET_OPEN  = (9, 15)   # HH, MM
MARKET_CLOSE = (15, 30)  # HH, MM

# ── NSE TRADING HOLIDAYS ────────────────────────────────────
# Equity-segment closures published by NSE. Without these the scanner treats
# a holiday as a trading day: _last_trading_day() returns the holiday itself,
# the historical call asks for a session that never happened, and every symbol
# silently comes back with "no data" instead of stepping back to the real
# last session.
#
# Add next year's list when NSE publishes it (usually December) — the scanner
# warns at start-up if the current year is missing.
NSE_HOLIDAYS = {
    # 2026 (NSE circular CMTR71775; 15 Jan added by separate circular)
    date(2026, 1, 15),   # Maharashtra municipal elections
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali - Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
}

# Years NSE_HOLIDAYS actually covers. Used to warn on a stale calendar.
_NSE_HOLIDAY_YEARS = {d.year for d in NSE_HOLIDAYS}


def _today_ist():
    """Today's date in IST.

    Bug fix: the codebase used datetime.date.today(), which resolves against
    the *machine's* local timezone. Every other clock here is IST, so on a
    laptop set to (say) US Eastern the scanner was a day out for most of the
    trading day: to_date pointed at a session the exchange had not reached,
    and the daily cache believed T-1 was settled when it was not.
    """
    return datetime.now(IST).date()


def _is_trading_day(d):
    """Mon–Fri and not an NSE holiday."""
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def is_market_open():
    """Return True only during NSE trading hours (Mon–Fri, 09:15–15:30 IST)."""
    now = datetime.now(IST)
    if not _is_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE

def _last_trading_day(d=None):
    """
    Return the most recent NSE trading day on or before date d.
    Defaults to today (IST) if d is None.
    Used to ensure to_date in API calls never falls on a weekend or holiday.
    e.g. Monday→ steps back to Friday so Upstox minutes API doesn't get to_date=Sunday.
    """
    if d is None:
        d = _today_ist()
    while not _is_trading_day(d):
        d -= timedelta(days=1)
    return d

def _next_trading_day(d=None):
    """
    Return the first NSE trading day on or after date d.
    Defaults to today (IST) if d is None.
    Used to ensure from_date in API calls never starts on a weekend or holiday.
    """
    if d is None:
        d = _today_ist()
    while not _is_trading_day(d):
        d += timedelta(days=1)
    return d

# ── FIBONACCI LEVELS ─────────────────────────────────────────
FIBO_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

# ── CANDLESTICK PATTERN LABELS ───────────────────────────────
CANDLE_BULL = ["HAMMER", "BULL_ENGULF", "MORNING_STAR", "DRAGONFLY_DOJI",
               "THREE_WHITE_SOLDIERS", "PIERCING", "BULL_HARAMI", "INV_HAMMER"]
CANDLE_BEAR = ["SHOOTING_STAR", "BEAR_ENGULF", "EVENING_STAR", "GRAVESTONE_DOJI",
               "THREE_BLACK_CROWS", "DARK_CLOUD", "BEAR_HARAMI", "HANGING_MAN"]

# ── SECTOR-GROUPED SYMBOLS  (Nifty 50) ───────────────────────
# Format: 'INSTRUMENT_KEY': ('DISPLAY_NAME', 'SECTOR')
SYMBOL_MAP_FULL = {
    # BANK
    "NSE_EQ|INE238A01034": ("AXISBANK",    "BANK"),
    "NSE_EQ|INE040A01034": ("HDFCBANK",    "BANK"),
    "NSE_EQ|INE090A01021": ("ICICIBANK",   "BANK"),
    "NSE_EQ|INE237A01036": ("KOTAKBANK",   "BANK"),
    "NSE_EQ|INE062A01020": ("SBIN",        "BANK"),
    # FINANCE
    "NSE_EQ|INE296A01032": ("BAJFINANCE",  "FINANCE"),
    "NSE_EQ|INE918I01026": ("BAJAJFINSV",  "FINANCE"),
    "NSE_EQ|INE758E01017": ("JIOFIN",      "FINANCE"),
    "NSE_EQ|INE721A01047": ("SHRIRAMFIN",  "FINANCE"),
    # INSURANCE
    "NSE_EQ|INE795G01014": ("HDFCLIFE",    "INSURANCE"),
    "NSE_EQ|INE123W01016": ("SBILIFE",     "INSURANCE"),
    # IT
    "NSE_EQ|INE860A01027": ("HCLTECH",     "IT"),
    "NSE_EQ|INE009A01021": ("INFY",        "IT"),
    "NSE_EQ|INE467B01029": ("TCS",         "IT"),
    "NSE_EQ|INE669C01036": ("TECHM",       "IT"),
    "NSE_EQ|INE075A01022": ("WIPRO",       "IT"),
    # ENERGY
    "NSE_EQ|INE522F01014": ("COALINDIA",   "ENERGY"),
    "NSE_EQ|INE733E01010": ("NTPC",        "ENERGY"),
    "NSE_EQ|INE213A01029": ("ONGC",        "ENERGY"),
    "NSE_EQ|INE752E01010": ("POWERGRID",   "ENERGY"),
    "NSE_EQ|INE002A01018": ("RELIANCE",    "ENERGY"),
    # AUTO
    "NSE_EQ|INE917I01010": ("BAJAJ-AUTO",  "AUTO"),
    "NSE_EQ|INE066A01021": ("EICHERMOT",   "AUTO"),
    "NSE_EQ|INE101A01026": ("M&M",         "AUTO"),
    "NSE_EQ|INE585B01010": ("MARUTI",      "AUTO"),
    "NSE_EQ|INE155A01022": ("TATAMOTORS",  "AUTO"),
    # METAL
    "NSE_EQ|INE038A01020": ("HINDALCO",    "METAL"),
    "NSE_EQ|INE019A01038": ("JSWSTEEL",    "METAL"),
    "NSE_EQ|INE081A01020": ("TATASTEEL",   "METAL"),
    # HEALTHCARE
    "NSE_EQ|INE437A01024": ("APOLLOHOSP",  "HEALTHCARE"),
    "NSE_EQ|INE059A01026": ("CIPLA",       "HEALTHCARE"),
    "NSE_EQ|INE089A01031": ("DRREDDY",     "HEALTHCARE"),
    "NSE_EQ|INE027H01010": ("MAXHEALTH",   "HEALTHCARE"),
    "NSE_EQ|INE044A01036": ("SUNPHARMA",   "HEALTHCARE"),
    # FMCG
    "NSE_EQ|INE030A01027": ("HINDUNILVR",  "FMCG"),
    "NSE_EQ|INE154A01025": ("ITC",         "FMCG"),
    "NSE_EQ|INE239A01024": ("NESTLEIND",   "FMCG"),
    "NSE_EQ|INE192A01025": ("TATACONSUM",  "FMCG"),
    # INFRA
    "NSE_EQ|INE742F01042": ("ADANIPORTS",  "INFRA"),
    "NSE_EQ|INE018A01030": ("LT",          "INFRA"),
    # DIVERSIFIED
    "NSE_EQ|INE423A01024": ("ADANIENT",    "DIVERSIFIED"),
    "NSE_EQ|INE047A01021": ("GRASIM",      "DIVERSIFIED"),
    # CHEMICALS
    "NSE_EQ|INE021A01026": ("ASIANPAINT",  "CHEMICALS"),
    # DEFENCE
    "NSE_EQ|INE263A01024": ("BEL",         "DEFENCE"),
    # TELECOM
    "NSE_EQ|INE397D01024": ("BHARTIARTL",  "TELECOM"),
    # AVIATION
    "NSE_EQ|INE646L01027": ("INDIGO",      "AVIATION"),
    # RETAIL
    "NSE_EQ|INE758T01015": ("ETERNAL",     "RETAIL"),
    "NSE_EQ|INE849A01020": ("TRENT",       "RETAIL"),
    # CONSUMER_DURABLES
    "NSE_EQ|INE280A01028": ("TITAN",       "CONSUMER_DURABLES"),
    # CEMENT
    "NSE_EQ|INE481G01011": ("ULTRACEMCO",  "CEMENT"),
}

# Active symbols (edit this list — key must exist in SYMBOL_MAP_FULL above)
ACTIVE_KEYS = list(SYMBOL_MAP_FULL.keys())  # All by default; trim as needed

# Build working maps
SYMBOL_MAP  = {k: SYMBOL_MAP_FULL[k][0] for k in ACTIVE_KEYS if k in SYMBOL_MAP_FULL}
SECTOR_MAP  = {SYMBOL_MAP_FULL[k][0]: SYMBOL_MAP_FULL[k][1]
               for k in ACTIVE_KEYS if k in SYMBOL_MAP_FULL}

# ── TIMEFRAME CONFIG ─────────────────────────────────────────
# tf -> (unit, value, lookback_days)
# WEEK and MONTH fetch DAILY candles and resample internally.
# Both share 2500-day lookback — Upstox's effective daily API limit (~7 yrs).
#   2500 days → ~1,750 daily → ~357 weekly → ~116 monthly bars.
TF_CONFIG = {
    # Unit    value  lookback_days
    "5MIN":  ("minutes", "5",   30),  # ~1,800 intraday bars
    "15MIN": ("minutes", "15",  30),  # ~600   intraday bars
    "1HR":   ("hours",   "1",   90),  # ~540   intraday bars
    "DAY":   ("days",    "1", 2500),  # ~1,750 daily bars     — EMA200 solid
    "WEEK":  ("days",    "1", 2500),  # daily→resample ~357 weekly bars — EMA200 solid
    "MONTH": ("days",    "1", 2500),  # daily→resample ~116 monthly bars — EMA60 solid
}

# TFs built by resampling daily candles — never use native weeks/months endpoint.
# "W-MON" → NSE week Mon-Fri, labeled on Monday (period open).
# "MS"    → Month Start anchor; one candle per calendar month.
RESAMPLE_TFS = {
    "WEEK":  "W-MON",
    "MONTH": "MS",
}

# Correct 52W window per TF resolution so df.tail(N) spans exactly 1 year.
#   DAY  : 252 trading days ≈ 1 year
#   WEEK :  52 weeks        ≈ 1 year  (was 252 → 4.8 yrs — wrong)
TF_52W_WINDOW = {
    "DAY":  252,
    "WEEK":  52,
}

TIMEFRAMES_INTRADAY = ["5MIN", "15MIN", "1HR"]
TIMEFRAMES_SWING    = ["DAY", "WEEK", "MONTH"]
TIMEFRAMES_DEFAULT  = ["DAY", "WEEK", "MONTH"]   # default scan set

# Minimum candles needed per TF before indicators are reliable.
# DAY:   2500d lookback → ~1,750 daily bars  — EMA200 fully warmed up.
# WEEK:  2500d daily → resample → ~357 weekly bars — EMA200 fully warmed up.
# MONTH: 2500d daily → resample → ~116 monthly bars — EMA60 fully warmed up.
TF_MIN_CANDLES = {
    "5MIN":  50,
    "15MIN": 50,
    "1HR":   50,
    "DAY":   250,   # ~1,750 available — safe
    "WEEK":  100,   # ~357 available   — safe
    "MONTH": 60,    # ~116 available   — safe
}

# ── SIGNAL THRESHOLDS ────────────────────────────────────────
RSI_OVERBOUGHT  = 65
RSI_OVERSOLD    = 35
STOCH_OB        = 80
STOCH_OS        = 20
VOL_SPIKE       = 1.5

W = 74   # terminal width


# ─────────────────────────────────────────────────────────────
#  UPSTOX DATA FETCHER
# ─────────────────────────────────────────────────────────────

def load_token():
    if not os.path.exists(TOKEN_FILE):
        cprint("  ERROR: " + TOKEN_FILE + " not found.", C.RED)
        cprint("  Create it: { \"access_token\": \"YOUR_TOKEN\" }", C.RED)
        return None
    with open(TOKEN_FILE) as f:
        d = json.load(f)
    tok = d.get("access_token", "")
    if not tok:
        cprint("  ERROR: access_token is empty.", C.RED)
        return None
    return tok

def make_headers(token):
    return {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Authorization": "Bearer " + token
    }

# ── V3 Intraday API unit/interval mapping ────────────────────
# Maps TF name → (v3_unit, v3_interval) used by Upstox Intraday Candle Data V3 API.
# Supported: minutes(1-300), hours(1-5), days(1)
# DAY is included so today's live daily candle is always appended to
# historical data — fixes stale price when viewing after market close.
TF_INTRADAY_V3 = {
    "5MIN":  ("minutes", "5"),
    "15MIN": ("minutes", "15"),
    "1HR":   ("hours",   "1"),
    "DAY":   ("days",    "1"),   # ← today's daily bar (live close price)
}

# ── ERROR TYPES ──────────────────────────────────────────────
class TokenError(Exception):
    """Raised when Upstox rejects the access token (HTTP 401/403).

    Retrying is pointless: every subsequent symbol/timeframe would fail the
    same way, burning the whole rate-limit budget and burying the real cause
    under hundreds of "no data" messages.  Callers abort the scan instead.
    """


class InvalidRangeError(Exception):
    """Raised when Upstox rejects a date range (UDAPI1148 / UDAPI1015).

    Signals the chunker that the requested window is too wide for the
    selected unit, so it can retry with a smaller span instead of
    silently returning an empty DataFrame.
    """


# ── RATE LIMITING ────────────────────────────────────────────
# Upstox "Other Standard APIs" bucket (historical candles included):
#     50 req/sec · 500 req/min · 2000 req/30 min
# An older community circular quotes 25/s · 250/min · 1000/30min, so the
# scanner stays well under the conservative figures.
_API_WINDOW_SECONDS      = 1800     # rolling window the cap applies to
API_MAX_CALLS_PER_WINDOW = 1800     # documented ceiling is 2000 / 30 min
API_MIN_INTERVAL         = 0.05     # min seconds between calls (20/s)
_api_call_times          = []       # monotonic timestamps of recent calls

# One shared TLS session — Upstox keeps connections alive, and re-handshaking
# per request dominated fetch time on a 50-symbol x 6-TF scan.
_session_obj   = None
_session_owner = None


def _get_session():
    """One shared HTTP session (connection reuse), rebuilt if `requests` changes."""
    global _session_obj, _session_owner
    if _session_obj is None or _session_owner is not requests:
        _session_owner = requests
        try:
            _session_obj = requests.Session()
        except AttributeError:
            _session_obj = requests
    return _session_obj


def _throttle():
    """Block until the next API call fits inside Upstox's rate-limit budget."""
    for _ in range(40):
        now = time.monotonic()
        while _api_call_times and now - _api_call_times[0] > _API_WINDOW_SECONDS:
            _api_call_times.pop(0)
        if len(_api_call_times) < API_MAX_CALLS_PER_WINDOW:
            break
        wait = _API_WINDOW_SECONDS - (now - _api_call_times[0]) + 1
        if wait > 0:
            cprint("  Rate-limit guard: pausing " + str(int(wait)) +
                   "s to stay under Upstox's 2000 calls / 30 min.", C.YELLOW)
            time.sleep(wait)
    if _api_call_times:
        gap = time.monotonic() - _api_call_times[-1]
        if 0 <= gap < API_MIN_INTERVAL:
            time.sleep(API_MIN_INTERVAL - gap)
    _api_call_times.append(time.monotonic())


def api_get(url, headers=None, timeout=15):
    """Throttled GET.  Returns the response object (never raises on status)."""
    _throttle()
    return _get_session().get(url, headers=headers, timeout=timeout)


def _to_ist(ts_series):
    """Coerce a parsed timestamp column to IST.

    Bug fix: naive timestamps were localised to UTC and then converted to IST,
    which shifted every NSE candle forward by 5h30m — a 09:15 bar became 14:45
    and, worse, evening/pre-market bars rolled over into the wrong trading day.
    Upstox NSE timestamps are exchange-local (IST), so a naive string is
    localised straight to IST.  Aware values are converted (which also
    normalises any mix of UTC/IST offsets to a single zone).
    """
    try:
        if ts_series.dt.tz is None:
            return ts_series.dt.tz_localize(IST)
        return ts_series.dt.tz_convert(IST)
    except (AttributeError, TypeError):
        return pd.to_datetime(ts_series, utc=True, errors="coerce").dt.tz_convert(IST)


def _parse_candle_df(candles):
    """Convert raw candle list → clean timezone-aware DataFrame.
    Bug fix: Upstox NSE_EQ candles may return 6 columns (no OI).
    Hardcoding 7 columns caused ValueError crash on 6-col responses.
    Now detects column count dynamically and fills missing OI with 0.
    """
    if not candles:
        return pd.DataFrame()
    n_cols = len(candles[0])
    all_cols = ["ts", "open", "high", "low", "close", "vol", "oi"]
    cols = all_cols[:n_cols]
    df   = pd.DataFrame(candles, columns=cols)
    if "oi" not in df.columns:
        df["oi"] = 0
    try:
        ts = pd.to_datetime(df["ts"], format="ISO8601")
    except (ValueError, TypeError):
        ts = pd.to_datetime(df["ts"], errors="coerce")
    df["ts"] = _to_ist(ts)
    df = df.dropna(subset=["ts"])
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def resample_candles(df_day, freq, include_partial=True):
    """
    Resample a daily OHLCV DataFrame to weekly or monthly frequency.

    freq           : "W-MON" (NSE week, labeled Monday) | "MS" (month start).
    include_partial: True  → keep current in-progress period (default).
                     False → drop it (fully closed periods only).

    OHLCV aggregation
    -----------------
      open  → first day's open  (correct: captures opening gap)
      high  → max daily high    (correct: true period high)
      low   → min daily low     (correct: true period low)
      close → last day's close  (correct: settlement price)
      vol   → sum of daily vol  (correct: total period activity)
      oi    → last day's OI     (correct: latest snapshot)

    Note on include_partial=True (the default, used by fetch_candles): the
    input here is cached daily history *plus today's live daily bar*, so the
    final weekly/monthly candle is the current in-progress period and its
    close/high/low move until the session closes. That is deliberate — it lets
    the trader see this week's/month's move so far — but it means the last
    WEEK/MONTH candle is NOT settled, unlike the DAY candles that feed it.
    An earlier version of this comment claimed the opposite; it was wrong.
    """
    if df_day.empty:
        return pd.DataFrame()

    df = df_day.copy().set_index("ts")
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)

    agg_map = {
        "open":  "first",
        "high":  "max",
        "low":   "min",
        "close": "last",
        "vol":   "sum",
    }
    if "oi" in df.columns:
        agg_map["oi"] = "last"

    resampled = (df
                 .resample(freq, closed="left", label="left")
                 .agg(agg_map)
                 .dropna(subset=["close"])
                 .reset_index())

    if not include_partial:
        today = _today_ist()
        if not resampled.empty:
            last_start = pd.Timestamp(resampled["ts"].iloc[-1]).date()
            if freq == "W-MON":
                week_start = today - timedelta(days=today.weekday())
                if last_start >= week_start and today.weekday() < 5:
                    resampled = resampled.iloc[:-1]
            elif freq == "MS":
                if (last_start.month == today.month
                        and last_start.year == today.year):
                    resampled = resampled.iloc[:-1]

    return resampled.reset_index(drop=True)


def _retry_after_seconds(response, default=5):
    """Honour the Retry-After header when Upstox sends one."""
    try:
        val = response.headers.get("Retry-After")
        if val:
            return max(1, min(120, int(float(val))))
    except Exception:
        pass
    return default


def _error_codes(response):
    """Best-effort extraction of Upstox error codes from a 4xx body."""
    try:
        errs = response.json().get("errors") or []
        return (",".join(str(e.get("errorCode", "?")) for e in errs)
                or str(response.status_code))
    except Exception:
        return str(response.status_code)


def _is_range_error(response):
    codes = _error_codes(response)
    return ("UDAPI1148" in codes
            or "UDAPI1015" in codes
            or "UDAPI1147" in codes)


def _fetch_historical_single(instrument_key, unit, value, from_d_str, to_d_str,
                              headers, verbose=True):
    """
    Internal helper: single Upstox Historical Candle V3 API call.
    Both from_d_str and to_d_str must already be valid trading-day strings
    (YYYY-MM-DD).  All retry / rate-limit logic lives here.

    Raises TokenError      — token rejected; caller must abort the scan.
    Raises InvalidRangeError — date window rejected; caller should split it.
    """
    enc = quote(instrument_key, safe="")
    url = ("https://api.upstox.com/v3/historical-candle/"
           + enc + "/" + unit + "/" + value + "/" + to_d_str + "/" + from_d_str)
    if verbose:
        print("    " + C.DIM + "HIST: " + url + C.RESET)
    for attempt in range(3):
        try:
            r = api_get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                candles = as_list(as_dict(r.json().get("data")).get("candles"))
                if candles:
                    return _parse_candle_df(candles)
                # Upstox returns 200 + empty candles when the date range has no
                # data (holiday range, future date, or the most recent daily
                # candle is not published yet).  No point retrying.
                if verbose: print("    No historical candles returned.")
                return pd.DataFrame()

            if r.status_code in (401, 403):
                raise TokenError(
                    "Upstox rejected the access token (HTTP " + str(r.status_code)
                    + "). Generate a new access_token at "
                      "https://upstox.com/developer/ and update "
                    + TOKEN_FILE + ".")

            if r.status_code == 429:
                wait = _retry_after_seconds(r, default=(5, 15, 45)[attempt])
                cprint("    Rate limited (429). Waiting " + str(wait) + "s…",
                       C.YELLOW)
                time.sleep(wait)
                continue

            if r.status_code == 400 and _is_range_error(r):
                raise InvalidRangeError(
                    "Upstox rejected the date range " + from_d_str + "→" + to_d_str
                    + " (" + _error_codes(r) + ")")

            if verbose: cprint("    HTTP " + str(r.status_code) + ": " + r.text, C.RED)
        except (TokenError, InvalidRangeError):
            raise
        except Exception as ex:
            if verbose: cprint("    Error: " + str(ex), C.RED)
        time.sleep(1)
    return pd.DataFrame()


# Upstox Historical Candle API: max calendar days per call by unit type.
# Exceeding this silently returns partial data or HTTP 400.
# Docs: minutes → 1 month for intervals ≤15, 1 quarter above;
#       hours   → 1 quarter;  days → 1 decade.  30d is safely inside both.
_HIST_MAX_DAYS = {"minutes": 30, "hours": 90, "days": 2000}


def _fetch_chunked(instrument_key, unit, value, from_d_obj, to_d_obj,
                    headers, verbose, max_span):
    """Download [from_d_obj, to_d_obj] in <= max_span-day windows.

    If Upstox rejects a window (UDAPI1148 – invalid date range) the span is
    halved and retried, so a too-generous _HIST_MAX_DAYS entry degrades to
    smaller chunks instead of silently dropping the whole range.
    """
    span_days = (to_d_obj - from_d_obj).days

    if span_days <= max_span:
        try:
            return _fetch_historical_single(
                instrument_key,
                unit,
                value,
                from_d_obj.strftime("%Y-%m-%d"),
                to_d_obj.strftime("%Y-%m-%d"),
                headers,
                verbose)
        except InvalidRangeError as ex:
            if max_span <= 1 or from_d_obj >= to_d_obj:
                cprint("    ✗ " + str(ex), C.RED)
                return pd.DataFrame()
            half = max(1, max_span // 2)
            if verbose:
                print("    " + C.DIM + "range rejected → retrying with " +
                      str(half) + "d chunks" + C.RESET)
            mid = _last_trading_day(to_d_obj - timedelta(days=half))
            if mid < from_d_obj:
                mid = from_d_obj
            a = _fetch_chunked(instrument_key, unit, value, from_d_obj, mid,
                               headers, verbose, half)
            b = (_fetch_chunked(instrument_key, unit, value,
                                _next_trading_day(mid + timedelta(days=1)),
                                to_d_obj, headers, verbose, half)
                 if mid < to_d_obj else pd.DataFrame())
            frames = [d for d in (a, b) if not d.empty]
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True) \
                     .drop_duplicates("ts").sort_values("ts") \
                     .reset_index(drop=True)

    if verbose:
        print("    " + C.DIM + "CHUNKED: " + str(span_days) + "d range → " +
              str(max_span) + "d chunks" + C.RESET)

    chunks    = []
    chunk_end = to_d_obj
    while chunk_end >= from_d_obj:
        chunk_start = max(
            from_d_obj,
            chunk_end - timedelta(days=max_span - 1))
        chunk_start = _next_trading_day(chunk_start)
        if chunk_start > chunk_end:
            # Degenerate window (holiday span) — stop before looping forever.
            break
        try:
            df_chunk = _fetch_chunked(
                instrument_key,
                unit,
                value,
                chunk_start,
                chunk_end,
                headers,
                verbose,
                max_span)
        except TokenError:
            raise
        except InvalidRangeError:
            df_chunk = pd.DataFrame()
        if not df_chunk.empty:
            chunks.append(df_chunk)
        chunk_end = _last_trading_day(chunk_start - timedelta(days=1))
        if chunk_end < from_d_obj:
            break

    if not chunks:
        return pd.DataFrame()
    return (pd.concat(chunks, ignore_index=True)
            .drop_duplicates("ts")
            .sort_values("ts")
            .reset_index(drop=True))


def _fetch_range_chunked(instrument_key, unit, value, from_d_obj, to_d_obj,
                          headers, verbose=True):
    """
    Fetch candles for an EXPLICIT date range [from_d_obj, to_d_obj].
    Handles API per-call limits by splitting into _HIST_MAX_DAYS[unit]-day chunks.
    Both dates must already be valid trading days.

    Used by:
      fetch_historical()         — lookback-based entry point
      fetch_historical_cached()  — incremental cache fill (exact range)
    """
    return _fetch_chunked(instrument_key, unit, value, from_d_obj, to_d_obj,
                          headers, verbose, _HIST_MAX_DAYS.get(unit, 2000))


def fetch_historical(instrument_key, unit, value, lookback_days, headers, verbose=True):
    """
    Fetch COMPLETE historical candles up to the last trading day before today.
    Today's candle is appended separately via fetch_intraday_v3().

    Bug fixes (5MIN / 15MIN date-range):
    ─────────────────────────────────────
    Bug 1 — to_date on weekend (critical for minutes/hours):
      Old code: yesterday = date.today() - 1 day (raw).
      On Mondays that produces Sunday → Upstox returns HTTP 400 / empty for
      minute-level data.  Fixed: to_date = _last_trading_day(today - 1),
      i.e. Friday when today is Monday.

    Bug 2 — from_date on weekend:
      Old code: from_d = today - lookback_days (raw calendar arithmetic).
      This can land on Saturday/Sunday.  Fixed: advance to _next_trading_day
      so the API range always starts on a valid trading session.

    Bug 3 — no chunking for long intraday lookbacks:
      Upstox caps minute historical calls at 15 calendar days each, hours at 90 days.
      When lookback_days exceeds _HIST_MAX_DAYS[unit] the function splits the range
      into max_span-day chunks (15 for minutes, 90 for hours), fetches each chunk
      separately, and concatenates the results.
    """
    to_d_obj   = _last_trading_day(_today_ist() - timedelta(days=1))
    from_d_obj = _next_trading_day(_today_ist() - timedelta(days=lookback_days))

    # Clamp: from must not exceed to (e.g. long holiday weekend edge case)
    if from_d_obj > to_d_obj:
        from_d_obj = to_d_obj

    return _fetch_range_chunked(instrument_key, unit, value,
                                from_d_obj, to_d_obj, headers, verbose)

def fetch_intraday_v3(instrument_key, v3_unit, v3_interval, headers, verbose=True):
    """
    Fetch TODAY'S live intraday candles via Upstox Intraday Candle Data V3 API.
    These are the candles for the current trading session only.
    Endpoint: GET /v3/historical-candle/intraday/{key}/{unit}/{interval}
    Supported units: minutes(1-300), hours(1-5), days(1)
    Returns a DataFrame of today's candles (may include the live incomplete candle
    at the end — that is intentional: it gives the most current signal).
    """
    enc = quote(instrument_key, safe="")
    url = ("https://api.upstox.com/v3/historical-candle/intraday/"
           + enc + "/" + v3_unit + "/" + v3_interval)

    if verbose:
        print("    " + C.DIM + "LIVE: " + url + C.RESET)

    for attempt in range(3):
        try:
            r = api_get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                candles = as_list(as_dict(r.json().get("data")).get("candles"))
                if candles:
                    return _parse_candle_df(candles)
                if verbose: print("    No intraday candles returned (market closed?).")
                return pd.DataFrame()
            if r.status_code in (401, 403):
                raise TokenError(
                    "Upstox rejected the access token (HTTP " + str(r.status_code)
                    + "). Generate a new access_token at "
                      "https://upstox.com/developer/ and update "
                    + TOKEN_FILE + ".")
            if r.status_code == 429:
                wait = _retry_after_seconds(r, default=(5, 15, 45)[attempt])
                cprint("    Rate limited (429). Waiting " + str(wait) + "s…",
                       C.YELLOW)
                time.sleep(wait)
                continue
            if verbose: cprint("    HTTP " + str(r.status_code) + ": " + r.text, C.RED)
        except TokenError:
            raise
        except Exception as ex:
            if verbose: cprint("    Error: " + str(ex), C.RED)
        time.sleep(1)
    return pd.DataFrame()

# ─────────────────────────────────────────────────────────────
#  CANDLE CACHE  —  SQLite-backed OHLCV store
#
#  Purpose: avoid re-downloading historical candles that haven't changed.
#  On every scan only the MISSING date range (last_cached → yesterday) is
#  fetched from Upstox.  Today's live bar is always fetched fresh and is
#  NEVER written to the cache (it is still forming).
#
#  Cache key = (instrument_key, cache_tf):
#    5MIN / 15MIN / 1HR / DAY  → stored under their own TF name
#    WEEK / MONTH              → stored under "DAY" (they resample from daily)
#
#  Table: candle_cache  (created in _db_connect)
#    instrument_key TEXT, tf TEXT, ts TEXT → PRIMARY KEY
#    open / high / low / close / vol / oi  REAL
# ─────────────────────────────────────────────────────────────

# Maps each scanner TF → cache storage key.
# WEEK and MONTH share the DAY cache since they resample from daily candles.
CACHE_TF_MAP = {
    "5MIN":  "5MIN",
    "15MIN": "15MIN",
    "1HR":   "1HR",
    "DAY":   "DAY",
    "WEEK":  "DAY",
    "MONTH": "DAY",
}


def _cache_load(instrument_key, cache_tf):
    """
    Load all cached OHLCV candles for (instrument_key, cache_tf).
    Returns a timezone-aware IST DataFrame, or empty DataFrame on miss.
    """
    try:
        con = _db_connect()
        rows = con.execute(
            "SELECT ts, open, high, low, close, vol, oi "
            "FROM candle_cache "
            "WHERE instrument_key=? AND tf=? ORDER BY ts",
            (instrument_key, cache_tf)).fetchall()
        con.close()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows,
                          columns=["ts", "open", "high", "low", "close", "vol", "oi"])
        df["ts"] = pd.to_datetime(df["ts"])
        if df["ts"].dt.tz is None:
            df["ts"] = df["ts"].dt.tz_localize(IST)
        else:
            df["ts"] = df["ts"].dt.tz_convert(IST)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as ex:
        cprint("  Cache load error: " + str(ex), C.RED)
        return pd.DataFrame()


def _cache_save(instrument_key, cache_tf, df):
    """
    Upsert new OHLCV rows into candle_cache.
    Uses INSERT OR IGNORE so existing rows are never overwritten
    (historical candles are immutable once the session closes).
    """
    if df.empty:
        return
    try:
        con = _db_connect()
        # Vectorised build of the parameter rows (was a per-row iterrows loop,
        # which dominated scan time on a 500-row daily series).
        stamps = pd.to_datetime(df["ts"]).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        ts_str = [s[:-2] + ":" + s[-2:] for s in stamps]   # +0530 → +05:30
        # strict=True: a silent length mismatch used to drop or misalign
        # column values instead of failing loudly.
        rows   = list(zip(
            [instrument_key] * len(df),
            [cache_tf] * len(df),
            ts_str,
            df["open"].astype(float).round(4).tolist(),
            df["high"].astype(float).round(4).tolist(),
            df["low"].astype(float).round(4).tolist(),
            df["close"].astype(float).round(4).tolist(),
            df["vol"].astype(float).round(2).tolist(),
            (df["oi"] if "oi" in df.columns
             else pd.Series(0.0, index=df.index)).astype(float).round(2).tolist(),
            strict=True,
        ))
        con.executemany(
            "INSERT OR IGNORE INTO candle_cache "
            "(instrument_key, tf, ts, open, high, low, close, vol, oi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows)
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  Cache save error: " + str(ex), C.RED)


def _cache_last_date(instrument_key, cache_tf):
    """
    Return the date of the most recent candle stored in the cache,
    or None if the cache is empty for this (instrument_key, cache_tf).
    """
    try:
        con = _db_connect()
        row = con.execute(
            "SELECT MAX(ts) FROM candle_cache "
            "WHERE instrument_key=? AND tf=?",
            (instrument_key, cache_tf)).fetchone()
        con.close()
        if row and row[0]:
            return pd.Timestamp(row[0]).date()
        return None
    except Exception:
        return None


#: (instrument_key, cache_tf) pairs we already tried to repair this process.
#: Bounds the cost of a range that genuinely has no data (a suspended or
#: delisted symbol) to one extra fetch per run rather than one per scan.
_GAP_REPAIR_DONE = set()


#: Shortest run of absent consecutive sessions that counts as a hole. Market
#: closures are isolated (or a two/three-day Diwali cluster); a failed chunk in
#: a chunked download drops a whole window at once.
_MIN_GAP_RUN = 5


def _cache_missing_days(cached_df, from_d, to_d):
    """
    Trading sessions in [from_d, to_d] that are absent from `cached_df`.

    Returns [] when the window is fully populated. The cost is one set
    difference over ~1800 dates for a 2500-day lookback — microseconds — and
    it only runs on the cache-hit path.
    """
    if cached_df is None or getattr(cached_df, "empty", True):
        return []
    if from_d is None or to_d is None or from_d > to_d:
        return []
    try:
        have = {pd.Timestamp(t).date() for t in cached_df["ts"]}
    except Exception:
        return []

    # A missing session is only treated as a hole when it forms a LONG
    # CONTIGUOUS RUN. Two things can legitimately be absent from the cache:
    #
    #   • a market closure — isolated, or at most a two/three-day cluster
    #     around Diwali;
    #   • a failed chunk in a chunked download — which drops a whole window
    #     at once, i.e. many consecutive sessions.
    #
    # Run length separates them without needing to know the trading calendar,
    # which matters because NSE_HOLIDAYS only covers the current year. Judging
    # every absent weekday individually produced 73 phantom gaps on a healthy
    # 7-year cache — one repair attempt per symbol, every run, forever, chasing
    # dates the API has no data for.
    #
    # Inside the years the calendar does cover we can do better and drop known
    # holidays before runs are formed, so a run there is unambiguous.
    covered = _NSE_HOLIDAY_YEARS

    def _flush(run):
        if len(run) >= _MIN_GAP_RUN:
            missing.extend(run)

    missing, run = [], []
    d, guard = from_d, 0
    # Hard cap so a broken calendar helper can never spin forever.
    while d <= to_d and guard < 5000:
        if d.weekday() >= 5:
            pass                      # weekend: neither present nor missing
        elif d.year in covered and not _is_trading_day(d):
            _flush(run); run = []     # known holiday — not a gap
        elif d not in have:
            run.append(d)
        else:
            _flush(run); run = []     # present: close any run before it
        d += timedelta(days=1)
        guard += 1
    _flush(run)
    return missing


def _cache_last_attempt(instrument_key, cache_tf):
    """
    Last date we ASKED Upstox for, regardless of whether rows came back.

    Needed because the daily candle for the most recent session may not be
    published yet.  Without this marker the scanner would re-request the same
    window on every scan until the data appears.
    """
    try:
        con = _db_connect()
        row = con.execute(
            "SELECT last_attempt FROM candle_cache_meta "
            "WHERE instrument_key=? AND tf=?",
            (instrument_key, cache_tf)).fetchone()
        con.close()
        if row and row[0]:
            return date.fromisoformat(str(row[0])[:10])
        return None
    except Exception:
        return None


def _cache_mark_attempt(instrument_key, cache_tf, attempted_to):
    """Record that we tried to fill the cache up to `attempted_to`."""
    try:
        con = _db_connect()
        con.execute(
            "INSERT INTO candle_cache_meta (instrument_key, tf, last_attempt) "
            "VALUES (?, ?, ?) ON CONFLICT(instrument_key, tf) "
            "DO UPDATE SET last_attempt=excluded.last_attempt",
            (instrument_key, cache_tf, attempted_to.strftime("%Y-%m-%d")))
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  Cache meta error: " + str(ex), C.RED)


def fetch_historical_cached(instrument_key, unit, value, lookback_days, headers,
                             verbose=True, cache_tf="DAY"):
    """
    Cache-aware replacement for fetch_historical().

    Workflow
    ────────
    1. Load all existing candles from candle_cache for (instrument_key, cache_tf).
    2. Find the last cached date.
    3a. Cache is up-to-date → return cached data, NO API call.
    3b. Cache has partial data → download only the missing gap, merge & save.
    3c. Cache is empty → download full lookback, save everything.
    4. Return data trimmed to lookback_days (enough for all indicators to warm up).

    Daily candle availability
    ─────────────────────────
    Upstox's historical API serves the previous session's daily candle, but NOT
    the current one (confirmed on the Upstox developer forum: "the response
    data only goes up to T-1, and the current trading day's candle is
    missing").  So the cache is filled up to YESTERDAY (T-1) and today's bar is
    always fetched fresh from the intraday endpoint, where it is still forming.

    Bug fix: this used to stop at T-2 to "allow for settlement", which punched
    a permanent one-day hole in every daily series (the cache held up to T-2,
    the live call returned only T-0).  EMAs, RSI, ATR, MACD and the gap
    calculation were all computed with a missing bar.  Requesting T-1 is safe:
    if yesterday's candle is genuinely not published yet the API simply returns
    fewer rows, and the "last attempted" marker stops us re-asking until the
    next scan.

    Today's bar is NEVER written to the cache — it is still forming and would
    freeze a partial OHLC row into the store.
    """
    to_d_obj  = _last_trading_day(_today_ist() - timedelta(days=1))
    full_from = _next_trading_day(_today_ist() - timedelta(days=lookback_days))
    if full_from > to_d_obj:
        full_from = to_d_obj

    # Historical data is published up to and including the last session (T-1).
    effective_to = to_d_obj

    cached_df   = _cache_load(instrument_key, cache_tf)
    last_cached = _cache_last_date(instrument_key, cache_tf)
    last_tried  = _cache_last_attempt(instrument_key, cache_tf)

    if last_cached and last_cached >= effective_to:
        # ── Cache reaches the target date ────────────────────
        #
        # "Reaches" is not the same as "complete". last_cached is MAX(ts),
        # which says nothing about what sits in between: a chunked download
        # that half-failed (a 429 part-way through, a truncated response)
        # leaves the newest bar in place while punching a hole in the middle.
        # Every later scan then took this branch, declared a full cache hit,
        # and never refilled the hole — permanently. Indicators that look back
        # across the gap (EMA, MACD, RSI, ATR) were computed on a series with
        # months missing out of the middle, silently.
        #
        # So scan the window we hold and repair from the first missing session
        # forward. INSERT OR IGNORE means re-fetching refills the hole without
        # disturbing the rows already stored.
        # Only hunt for holes INSIDE the span we actually hold. Sessions
        # before the first cached bar are "never published that far back"
        # (a recent listing), not a failed download — treating them as a gap
        # would make every short-history symbol refetch on every scan.
        try:
            first_cached = pd.Timestamp(cached_df["ts"].min()).date()
        except Exception:
            first_cached = None
        gap_from = full_from if first_cached is None else max(full_from, first_cached)
        missing = _cache_missing_days(cached_df, gap_from, effective_to)
        if missing:
            repair_key = (instrument_key, cache_tf)
            if repair_key not in _GAP_REPAIR_DONE:
                _GAP_REPAIR_DONE.add(repair_key)
                dl_from = missing[0]
                if verbose:
                    print("    " + C.DIM + "CACHE GAP [" + cache_tf + "] "
                          + str(len(missing)) + " session(s) missing from "
                          + str(missing[0]) + " → repairing" + C.RESET)
                if dl_from <= effective_to:
                    new_df = _fetch_range_chunked(instrument_key, unit, value,
                                                  dl_from, effective_to,
                                                  headers, verbose)
                    if not new_df.empty:
                        _cache_save(instrument_key, cache_tf, new_df)
                        cached_df = _cache_load(instrument_key, cache_tf)
            elif verbose:
                print("    " + C.DIM + "CACHE GAP [" + cache_tf + "] "
                      "still incomplete — one repair attempt per run" + C.RESET)
        elif verbose:
            print("    " + C.DIM + "CACHE HIT [" + cache_tf + "] up to "
                  + str(last_cached) + " — skipping API" + C.RESET)
    elif last_tried and last_tried >= effective_to and last_cached:
        # Already asked for this window — the data simply isn't published yet.
        if verbose:
            print("    " + C.DIM + "CACHE [" + cache_tf
                  + "] already fetched up to " + str(last_tried)
                  + " — nothing new yet" + C.RESET)
    else:
        # ── Partial or empty cache: download only the gap ────
        if last_cached:
            dl_from = _next_trading_day(last_cached + timedelta(days=1))
            if verbose:
                print("    " + C.DIM + "CACHE PARTIAL [" + cache_tf + "] last="
                      + str(last_cached) + " → downloading "
                      + str(dl_from) + " → " + str(effective_to) + C.RESET)
        else:
            dl_from = full_from
            if verbose:
                print("    " + C.DIM + "CACHE EMPTY [" + cache_tf
                      + "] → downloading full lookback "
                      + str(dl_from) + " → " + str(effective_to) + C.RESET)

        if dl_from <= effective_to:
            new_df = _fetch_range_chunked(instrument_key, unit, value,
                                          dl_from, effective_to, headers, verbose)
            _cache_mark_attempt(instrument_key, cache_tf, effective_to)
            if not new_df.empty:
                _cache_save(instrument_key, cache_tf, new_df)
                # Reload so we have the full merged dataset from DB
                cached_df = _cache_load(instrument_key, cache_tf)
        else:
            if verbose:
                print("    " + C.DIM + "CACHE [" + cache_tf
                      + "] no gap to download (daily settlement lag)" + C.RESET)

    if cached_df.empty:
        return pd.DataFrame()

    # Trim to lookback window (avoids sending decades of data to indicators)
    cutoff = pd.Timestamp(full_from).tz_localize(IST)
    result = cached_df[cached_df["ts"] >= cutoff].reset_index(drop=True)
    return result if not result.empty else cached_df


_LIVE_BAR_CACHE = {}   # instrument_key -> today's (still forming) bar


def start_scan_pass():
    """Call once at the beginning of a scan pass to clear per-scan memos."""
    _LIVE_BAR_CACHE.clear()


def fetch_live_daily_bar(instrument_key, headers, verbose=False):
    """Today's (still forming) daily candle, fetched at most once per symbol.

    Bug fix: fetch_candles hit the intraday endpoint separately for 5MIN,
    15MIN, 1HR and DAY, so the same bar was downloaded 3-4× per symbol —
    150-200 redundant calls on a full scan, straight out of the rate budget.
    """
    if instrument_key in _LIVE_BAR_CACHE:
        return _LIVE_BAR_CACHE[instrument_key].copy()
    df = fetch_intraday_v3(instrument_key, "days", "1", headers, verbose=verbose)
    _LIVE_BAR_CACHE[instrument_key] = df
    return df.copy()


def fetch_candles(instrument_key, unit, value, lookback_days, headers,
                  verbose=True, tf_name=""):
    """
    Cache-aware fetch dispatcher.

    All historical data (up to yesterday) is served from the SQLite candle_cache
    table via fetch_historical_cached() — the API is only called for the date
    range not yet in the cache (typically just today's new session or a few days
    of missed trading days).

    WEEK / MONTH → cached daily (up to yesterday) + today's live daily bar,
                   then resampled to weekly / monthly.
    DAY          → cached daily (up to yesterday) + today's live daily bar.
    Intraday     → cached intraday (up to yesterday) + today's live intraday bar.

    Today's live bar from fetch_intraday_v3() is NEVER written to the cache —
    it is still forming and would corrupt stored immutable OHLCV rows.
    """
    cache_tf = CACHE_TF_MAP.get(tf_name, tf_name)

    # ── WEEK / MONTH: resample from daily (cached + today's live) ──
    if tf_name in RESAMPLE_TFS:
        freq = RESAMPLE_TFS[tf_name]
        if verbose:
            print("    " + C.DIM + "RESAMPLE: fetching daily → " + freq + C.RESET)
        day_df = fetch_historical_cached(instrument_key, "days", "1",
                                         lookback_days, headers,
                                         verbose=verbose, cache_tf="DAY")
        # Append today's live daily bar so current week/month candle is up-to-date
        live_day_df = fetch_live_daily_bar(instrument_key, headers, verbose)
        if not live_day_df.empty:
            if day_df.empty:
                day_df = live_day_df
            else:
                day_df = (pd.concat([day_df, live_day_df], ignore_index=True)
                          .drop_duplicates("ts")
                          .sort_values("ts")
                          .reset_index(drop=True))
        if day_df.empty:
            cprint("    ✗ " + tf_name + ": No daily data from Upstox — "
                   "cannot resample to " + freq, C.RED)
            return pd.DataFrame()
        resampled = resample_candles(day_df, freq, include_partial=True)
        if resampled.empty:
            cprint("    ✗ " + tf_name + ": Resample produced no candles", C.RED)
        elif verbose:
            print("    " + C.DIM + "  → " + str(len(resampled)) + " " + tf_name
                  + " candles after resample (incl. current period)" + C.RESET)
        return resampled

    # ── Defensive fallback: TF in TF_CONFIG but missing from TF_INTRADAY_V3 ──
    # Built-in TFs (DAY/5MIN/15MIN/1HR) are all in TF_INTRADAY_V3 so this
    # branch is never reached normally.  If a new TF is added to TF_CONFIG
    # without a matching TF_INTRADAY_V3 entry it falls here — live bar is
    # skipped but the caller gets a visible warning instead of silent data loss.
    if tf_name not in TF_INTRADAY_V3:
        cprint("    ⚠ " + tf_name + ": not in TF_INTRADAY_V3 — "
               "live bar skipped. Add entry to TF_INTRADAY_V3 to fix.", C.YELLOW)
        hist_df = fetch_historical_cached(instrument_key, unit, value,
                                          lookback_days, headers,
                                          verbose=verbose, cache_tf=cache_tf)
        if hist_df.empty:
            cprint("    ✗ " + tf_name + ": No data from Upstox API", C.RED)
        return hist_df

    # ── DAY / Intraday: cached historical + today's live bar ────
    hist_df = fetch_historical_cached(instrument_key, unit, value,
                                      lookback_days, headers,
                                      verbose=verbose, cache_tf=cache_tf)
    v3_unit, v3_interval = TF_INTRADAY_V3[tf_name]
    if (v3_unit, v3_interval) == ("days", "1"):
        # Today's daily bar is the same for every TF that needs it — memoised.
        live_df = fetch_live_daily_bar(instrument_key, headers, verbose)
    else:
        live_df = fetch_intraday_v3(instrument_key, v3_unit, v3_interval,
                                    headers, verbose=verbose)
    if live_df.empty:
        if hist_df.empty:
            cprint("    ✗ " + tf_name + ": No data from historical "
                   "or intraday API", C.RED)
        return hist_df
    if hist_df.empty:
        return live_df
    combined = (pd.concat([hist_df, live_df], ignore_index=True)
                .drop_duplicates("ts")
                .sort_values("ts")
                .reset_index(drop=True))
    return combined


# ─────────────────────────────────────────────────────────────
#  INDICATOR CALCULATIONS
# ─────────────────────────────────────────────────────────────

def as_dict(val):
    """Return `val` if it is a dict, else {}.

    Bug fix: `as_dict(e.get("targets"))` only supplies the default when the key is
    MISSING.  A row written by an older build (or a half-written one) can hold
    0.0 / None / a string there, and the next `.get(...)` raised
    AttributeError — killing the whole view.
    """
    return val if isinstance(val, dict) else {}


def as_list(val):
    """Return `val` if it is a list/tuple, else []."""
    if isinstance(val, list):
        return val
    if isinstance(val, tuple):
        return list(val)
    return []


def is_scanned_entry(e):
    """
    True when a timeframe entry holds the output of a real scan.

    `empty_entry()` is what every symbol carries before it has ever been
    scanned (and for timeframes the user has switched off). Its indicator
    fields are zero-filled — rsi=0.0, volume=0, price=0.0, st_direction=0 —
    which are *absent* values, not measured ones. Comparing them against
    thresholds (e.g. `rsi < 20`) manufactures signals out of nothing, so any
    code that scores or alerts on an entry must check this first.

    A scanned entry always has a non-empty `updated` stamp and a live price.
    """
    e = as_dict(e)
    return bool(e.get("updated")) and safe_float(e.get("price", 0)) > 0


def safe_float(val, default=0.0):
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except Exception:
        return default

def calc_rsi(series, period=14):
    """
    RSI using Wilder's smoothing: alpha = 1/period.
    Bug fix: was ewm(span=period) → alpha = 2/(period+1) ≈ 0.133.
    Wilder's correct formula is alpha = 1/period ≈ 0.071 (1.87× slower).
    Old formula diverged ~8 pts from TradingView/Bloomberg, making
    the 65/35 thresholds effectively miscalibrated.

    Bug fix (division by zero): avg_loss == 0 means the window had no down
    bars at all, i.e. RSI = 100 — but dividing by zero produced NaN, which
    .fillna(50) then reported as a neutral 50.  A stock making higher highs
    on every single bar was scored "neutral" and lost its RSI points.
    """
    delta    = series.diff()
    gain     = delta.clip(lower=0).fillna(0.0)
    loss     = (-delta).clip(lower=0).fillna(0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rsi      = pd.Series(50.0, index=series.index, dtype=float)

    no_loss  = (avg_loss == 0) & (avg_gain > 0)
    no_gain  = (avg_gain == 0) & (avg_loss > 0)
    normal   = (avg_loss > 0) & (avg_gain > 0)

    rsi[no_loss] = 100.0
    rsi[no_gain] = 0.0
    rs       = avg_gain[normal] / avg_loss[normal]
    rsi[normal]  = 100 - (100 / (1 + rs))
    return rsi

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast   = series.ewm(span=fast,   adjust=False).mean()
    ema_slow   = series.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line= macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_stochastic(df, k=14, d=3):
    low_min  = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    diff     = (high_max - low_min).replace(0, np.nan)
    stoch_k  = ((df["close"] - low_min) / diff * 100).fillna(50)
    # Bug fix: %D leaked NaN for the first d-1 bars and that NaN reached
    # detect_signal, where `stoch_k > stoch_d` is silently False — a fresh
    # breakout lost its stochastic points for no visible reason.
    stoch_d  = stoch_k.rolling(d).mean().fillna(50)
    return stoch_k, stoch_d

def calc_supertrend(df, period=10, multiplier=3.0):
    atr = calc_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    basic_upper = (hl2 + multiplier * atr).values
    basic_lower = (hl2 - multiplier * atr).values
    close_vals  = df["close"].values
    n = len(df)

    upper     = basic_upper.copy()
    lower     = basic_lower.copy()
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        # Bug fix: use numpy arrays + plain indexing — avoids pandas .iloc chained-assignment
        #          issues (SettingWithCopyWarning / silently dropped in pandas 2.x)
        upper[i] = (min(basic_upper[i], upper[i-1])
                    if close_vals[i-1] <= upper[i-1] else basic_upper[i])
        lower[i] = (max(basic_lower[i], lower[i-1])
                    if close_vals[i-1] >= lower[i-1] else basic_lower[i])

    for i in range(1, n):
        if close_vals[i] > upper[i-1]:
            direction[i] = 1
        elif close_vals[i] < lower[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

    supertrend_vals = np.where(direction == 1, lower, upper)

    supertrend = pd.Series(supertrend_vals, index=df.index, dtype=float)
    dir_series = pd.Series(direction,       index=df.index, dtype=int)
    return supertrend, dir_series

def calc_atr(df, period=14):
    prev_close = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs()
    ], axis=1).max(axis=1)
    # Bug fix: use Wilder's smoothing (EWM, alpha=1/period) — required by SuperTrend standard
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()

# ─────────────────────────────────────────────────────────────
#  NEW INDICATORS: ADX, Williams %R, CCI, MFI
# ─────────────────────────────────────────────────────────────

def calc_adx(df, period=14):
    """
    Average Directional Index (ADX) with +DI and -DI.
    ADX measures trend STRENGTH (not direction):
      < 20  → ranging / weak trend (avoid BREAKOUT signals)
      20-40 → developing trend (moderate confidence)
      40-60 → strong trend (high confidence)
      > 60  → very strong / potentially exhausted trend
    +DI > -DI = bullish pressure; -DI > +DI = bearish pressure.
    Uses Wilder's smoothing (alpha=1/period) — same as RSI/ATR.
    """
    if len(df) < period + 5:
        n = len(df)
        # Bug fix: returning 0 claimed "maximally range-bound", the exact
        # opposite of the truth for a series too short to measure.  ADX 20 is
        # the neutral reading — it neither suppresses breakouts nor invents a
        # trend the way 0 (never > 25) did.
        neutral = pd.Series(np.full(n, 20.0), index=df.index)
        return neutral, neutral, neutral

    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    move_up   = high - high.shift(1)
    move_down = low.shift(1) - low
    plus_dm  = np.where((move_up > move_down) & (move_up > 0), move_up,  0.0)
    minus_dm = np.where((move_down > move_up) & (move_down > 0), move_down, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    alpha = 1.0 / period
    atr_w    = tr.ewm(alpha=alpha,   adjust=False).mean()
    plus_di  = 100 * plus_dm_s.ewm(alpha=alpha,  adjust=False).mean() / atr_w.replace(0, np.nan)
    minus_di = 100 * minus_dm_s.ewm(alpha=alpha, adjust=False).mean() / atr_w.replace(0, np.nan)

    dx = (100 * (plus_di - minus_di).abs() /
          (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx.round(1), plus_di.round(1), minus_di.round(1)


def calc_williams_r(df, period=14):
    """
    Williams %R: momentum oscillator, range 0 to -100.
      0   to -20  → overbought (strong momentum, potential reversal)
      -20 to -50  → bullish momentum zone
      -50 to -80  → bearish momentum zone
      -80 to -100 → oversold (exhaustion, potential reversal)
    Fast-reacting compared to Stochastic — ideal for timing entries.
    """
    if len(df) < period:
        return pd.Series(np.full(len(df), -50.0), index=df.index)
    roll_high = df["high"].rolling(period).max()
    roll_low  = df["low"].rolling(period).min()
    wr = -100 * (roll_high - df["close"]) / (roll_high - roll_low).replace(0, np.nan)
    return wr.fillna(-50).round(1)


def calc_cci(df, period=20):
    """
    Commodity Channel Index: identifies cyclical price extremes.
      > +100 → overbought / start of strong uptrend
      +100 to 0 → normal bullish range
      0 to -100 → normal bearish range
      < -100 → oversold / start of strong downtrend
    Mean-reverts well on liquid equities; trend-following on breakout.
    """
    if len(df) < period:
        return pd.Series(np.zeros(len(df)), index=df.index)
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    cci = (tp - sma) / (0.015 * mad.replace(0, np.nan))
    return cci.fillna(0).round(1)


def calc_mfi(df, period=14):
    """
    Money Flow Index: volume-weighted RSI (0-100).
    Captures ACCUMULATION (smart money buying) vs DISTRIBUTION (smart money selling).
      > 80 → overbought / distribution zone
      50-80 → bullish (positive money flow)
      20-50 → bearish (negative money flow)
      < 20 → oversold / accumulation zone
    Key advantage over RSI: volume confirms price — MFI divergence with price
    signals institutional reversals before RSI does.
    """
    if len(df) < period + 1 or "vol" not in df.columns:
        return pd.Series(np.full(len(df), 50.0), index=df.index)
    tp      = (df["high"] + df["low"] + df["close"]) / 3
    raw_mf  = tp * df["vol"]
    prev_tp = tp.shift(1)
    # Bug fix: an unchanged typical price is neither accumulation nor
    # distribution.  Booking it as negative flow (tp <= prev_tp) made a flat
    # series read 0 — "maximum distribution" on a stock doing nothing.
    pos_mf  = raw_mf.where(tp > prev_tp, 0.0)
    neg_mf  = raw_mf.where(tp < prev_tp, 0.0)
    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()
    # Bug fix: the old single-ratio path divided by zero whenever one side of
    # the flow was empty, so a window of pure buying produced NaN and .fillna(50)
    # reported it as neutral instead of MFI 100.
    mfi     = pd.Series(50.0, index=df.index, dtype=float)
    valid   = pos_sum.notna() & neg_sum.notna()
    no_neg  = valid & (neg_sum == 0) & (pos_sum > 0)
    no_pos  = valid & (pos_sum == 0) & (neg_sum > 0)
    normal  = valid & (pos_sum > 0) & (neg_sum > 0)
    mfi[no_neg]  = 100.0
    mfi[no_pos]  = 0.0
    mfi[normal]  = 100 - (100 / (1 + pos_sum[normal] / neg_sum[normal]))
    return mfi.round(1)


# Per-TF lookback bars for S/R and Fibonacci level detection.
# Fix: previously both functions used fixed lookbacks (30 and 50 bars) regardless
# of timeframe.  On WEEK/MONTH bars a 30-bar window spans months of history — levels
# from that far back are stale and rarely actionable.  Intraday charts needed a
# shorter window to avoid using pre-market swing highs.
_SR_LOOKBACK   = {"5MIN": 20, "15MIN": 20, "1HR": 25, "DAY": 30, "WEEK": 15, "MONTH": 10}
_FIBO_LOOKBACK = {"5MIN": 30, "15MIN": 30, "1HR": 35, "DAY": 50, "WEEK": 26, "MONTH": 18}

def calc_support_resistance(df, lookback=30):
    """
    Detect swing highs/lows as S/R levels.
    Resistance levels are only returned if they are ABOVE current price —
    a swing high that price has already closed above is no longer resistance,
    it becomes a potential support. Vice-versa for support below current price.
    """
    if len(df) < lookback + 2:
        return [], []
    recent       = df.tail(lookback)
    current_price = float(df.iloc[-1]["close"])
    highs        = recent["high"]
    lows         = recent["low"]

    # Swing highs above current price only → resistance
    resistance = sorted(set(
        round(h, 2) for i, h in enumerate(highs)
        if i > 0 and i < len(highs)-1
        and h == highs.iloc[max(0,i-2):i+3].max()
        and h > current_price           # must be above price to be resistance
    ), reverse=True)[:3]

    # Swing lows below current price only → support
    support = sorted(set(
        round(l, 2) for i, l in enumerate(lows)
        if i > 0 and i < len(lows)-1
        and l == lows.iloc[max(0,i-2):i+3].min()
        and l < current_price           # must be below price to be support
    ))[:3]

    return support, resistance


# ─────────────────────────────────────────────────────────────
#  NEW: VOLUME PROFILE
# ─────────────────────────────────────────────────────────────

def calc_volume_profile(df, n_bins=24):
    """
    Divide price range into n_bins buckets and accumulate volume.
    Volume from each candle is distributed proportionally across
    the candle's high-low range — the standard TPO/market-profile approach.

    Returns:
      profile : list of (mid_price, volume, pct_of_max) ordered low→high
      hvn     : top-3 High Volume Nodes  (price magnet / strong S/R)
      lvn     : bottom-3 Low Volume Nodes (thin zone — fast moves)
      poc     : Point of Control — price with most total volume
    """
    if df.empty or len(df) < 5:
        return [], [], [], 0.0
    lo = float(df["low"].min())
    hi = float(df["high"].max())
    if hi <= lo:
        return [], [], [], 0.0
    bin_size = (hi - lo) / n_bins

    # Vectorised implementation — avoids O(n_candles × n_bins) Python loops.
    # For each candle we compute how much of its volume falls in each price bin
    # using NumPy broadcasting: shapes (n_candles,) vs (n_bins,).
    c_lo  = df["low"].to_numpy(dtype=float)
    c_hi  = df["high"].to_numpy(dtype=float)
    vols  = df["vol"].to_numpy(dtype=float)
    spans = c_hi - c_lo                          # (n_candles,)

    # Bin edges (n_bins+1,)
    edges  = lo + np.arange(n_bins + 1) * bin_size
    b_lo_v = edges[:-1]   # (n_bins,)
    b_hi_v = edges[1:]    # (n_bins,)

    # Overlap matrix: (n_candles, n_bins)
    overlap = np.maximum(
        0.0,
        np.minimum(c_hi[:, None], b_hi_v[None, :])
        - np.maximum(c_lo[:, None], b_lo_v[None, :])
    )

    # Candles with zero span — assign entire volume to their bin
    zero_mask = spans <= 0
    if zero_mask.any():
        idx = np.clip(
            ((c_lo[zero_mask] - lo) / bin_size).astype(int), 0, n_bins - 1
        )
        overlap[zero_mask] = 0.0
        for _i, _b in zip(np.where(zero_mask)[0], idx, strict=True):
            overlap[_i, _b] = 1.0   # weight = 1.0; multiplied by vol below
        spans[zero_mask] = 1.0       # avoid division by zero

    bins_arr = (overlap / spans[:, None] * vols[:, None]).sum(axis=0)
    max_vol  = float(bins_arr.max()) if bins_arr.max() > 0 else 1.0
    profile = []
    for i, v in enumerate(bins_arr):
        mid = round(lo + (i + 0.5) * bin_size, 2)
        pct = round(v / max_vol * 100, 1) if max_vol > 0 else 0.0
        profile.append((mid, round(v, 0), pct))
    sorted_by_vol = sorted(profile, key=lambda x: x[1], reverse=True)
    poc  = sorted_by_vol[0][0] if sorted_by_vol else 0.0
    hvn  = sorted_by_vol[:3]
    nonz = [p for p in sorted_by_vol if p[1] > 0]
    lvn  = nonz[-3:] if len(nonz) >= 3 else nonz
    return profile, hvn, lvn, poc


# ─────────────────────────────────────────────────────────────
#  NEW: GAP DETECTION
# ─────────────────────────────────────────────────────────────

def calc_gap(df):
    """
    Detect gap between the last candle's open and the prior candle's close.
    For DAY data: today's open vs yesterday's close (the classic gap).
    Threshold: >0.3% = meaningful gap (filters micro-gaps from bid/ask spread).

    gap_filled:
      Gap-up filled  → today's LOW retraced back to or below prev_close
      Gap-down filled → today's HIGH recovered back to or above prev_close
    """
    if len(df) < 2:
        return {}
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev_close  = safe_float(prev.get("close", 0))
    today_open  = safe_float(last.get("open",  0))
    today_high  = safe_float(last.get("high",  0))
    today_low   = safe_float(last.get("low",   0))
    if prev_close <= 0:
        return {}
    gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
    if   gap_pct >  0.3: gap_type = "GAP_UP"
    elif gap_pct < -0.3: gap_type = "GAP_DOWN"
    else:                gap_type = "NO_GAP"
    gap_filled = False
    if gap_type == "GAP_UP":
        gap_filled = today_low <= prev_close
    elif gap_type == "GAP_DOWN":
        gap_filled = today_high >= prev_close
    return {
        "gap_pct":    gap_pct,
        "gap_type":   gap_type,
        "gap_filled": gap_filled,
        "today_open": round(today_open, 2),
        "prev_close": round(prev_close, 2),
        "gap_rs":     round(abs(today_open - prev_close), 2),
    }



def detect_retest_breakout(df, lookback=40):
    """
    Detect a CONFIRMED RETEST breakout/breakdown through R1 or S1.

    Bullish pattern (3 phases must all occur in order, left→right):
        Phase 1 — First touch  : any candle's HIGH crosses above R1
        Phase 2 — Pullback     : at least one candle AFTER phase-1 closes BELOW R1
        Phase 3 — Confirmed BO : current (last) candle closes ABOVE R1

    Bearish mirror:
        Phase 1 — First break  : any candle's LOW drops below S1
        Phase 2 — Bounce       : at least one candle AFTER phase-1 closes ABOVE S1
        Phase 3 — Confirmed BD : current (last) candle closes BELOW S1

    Why this matters vs a first-touch breakout:
      • First touch may be a fakeout — weak hands chase, then get trapped.
      • The pullback below R1 flushes those trapped longs.
      • The second cross with close above R1 shows real buying conviction.
      • R1 has now been tested twice → higher probability it holds as new support.

    ATR tolerance:
      Levels within 0.3 × ATR of the current close are considered "at the level".
      This prevents noise from filtering out genuine retests on volatile days.

    Returns:
        dict with keys:
          retest_bo   (bool)  — confirmed bullish retest breakout
          retest_bd   (bool)  — confirmed bearish retest breakdown
          retest_bo_level (float) — the R1 level that was retested (0 if none)
          retest_bd_level (float) — the S1 level that was retested (0 if none)
    """
    result = {
        "retest_bo":       False,
        "retest_bd":       False,
        "retest_bo_level": 0.0,
        "retest_bd_level": 0.0,
    }

    if len(df) < 10:
        return result

    # ── Compute S/R on a wider window for stable levels ──────
    support, resistance = calc_support_resistance(df, lookback=max(lookback, 50))
    if not resistance and not support:
        return result

    atr = safe_float(df.iloc[-1].get("atr", 0))
    tol = atr * 0.3   # price tolerance for "near level"

    # Max distance a R1/S1 level can be from current price to be considered
    # "active". 5×ATR filters out levels that price has already left far behind
    # (e.g. DAY retest at ₹1055 when price is ₹1216 — that's ancient history).
    # On 5MIN this is ~₹10, on DAY ~₹130, on WEEK ~₹236 — all sensible.
    max_dist = atr * 5 if atr > 0 else float("inf")

    # Work on the recent lookback window only (not the full history)
    recent  = df.tail(lookback).reset_index(drop=True)
    n       = len(recent)
    closes  = recent["close"].values
    highs   = recent["high"].values
    lows    = recent["low"].values
    current_close = closes[-1]

    # ── Bullish retest breakout ───────────────────────────────
    # Bug fix: resistance comes back sorted high→low, so `break` used to stop
    # on the FARTHEST level above price even though the docstring (and the
    # trading logic) call for the NEAREST one.  Iterate low→high instead.
    for r1 in sorted(resistance):
        # Phase 3: current close must be above R1
        if current_close <= r1:
            continue

        # Staleness guard: R1 must be within 5×ATR of current price.
        # Prevents DAY/WEEK/MONTH retests firing on levels from months ago.
        if abs(current_close - r1) > max_dist:
            continue

        # Search backwards for phase 2 then phase 1
        # phase2_idx: most recent candle (before last) that closed BELOW R1
        phase2_idx = None
        for i in range(n - 2, -1, -1):
            if closes[i] < r1 - tol:
                phase2_idx = i
                break

        if phase2_idx is None:
            continue   # no pullback found → first touch, not a retest

        # phase1_idx: any candle BEFORE phase2_idx whose HIGH crossed above R1
        phase1_found = False
        for i in range(0, phase2_idx):
            if highs[i] > r1 + tol:
                phase1_found = True
                break

        if phase1_found:
            result["retest_bo"]       = True
            result["retest_bo_level"] = r1
            break   # nearest R1 match wins

    # ── Bearish retest breakdown ──────────────────────────────
    for s1 in sorted(support, reverse=True):   # nearest support first
        # Phase 3: current close must be below S1
        if current_close >= s1:
            continue

        # Staleness guard
        if abs(current_close - s1) > max_dist:
            continue

        # phase2_idx: most recent candle (before last) that closed ABOVE S1
        phase2_idx = None
        for i in range(n - 2, -1, -1):
            if closes[i] > s1 + tol:
                phase2_idx = i
                break

        if phase2_idx is None:
            continue

        # phase1_idx: any candle BEFORE phase2_idx whose LOW broke below S1
        phase1_found = False
        for i in range(0, phase2_idx):
            if lows[i] < s1 - tol:
                phase1_found = True
                break

        if phase1_found:
            result["retest_bd"]       = True
            result["retest_bd_level"] = s1
            break

    return result   # ← was missing → caused TypeError: 'NoneType' object is not subscriptable


# ─────────────────────────────────────────────────────────────
#  PRO: FIBONACCI RETRACEMENT LEVELS
# ─────────────────────────────────────────────────────────────

def calc_fibonacci_levels(df, lookback=50):
    """Auto-detect swing high/low and return Fib retracement levels."""
    if len(df) < 10:
        return {}
    recent   = df.tail(min(lookback, len(df)))
    sw_high  = float(recent["high"].max())
    sw_low   = float(recent["low"].min())
    rng      = sw_high - sw_low
    if rng <= 0:
        return {}
    levels = {}
    for pct in FIBO_LEVELS:
        # Bug fix: .replace(".0","") was too aggressive — turned "fib_0.0"→"fib_"
        # and "fib_100.0"→"fib_1", making 0% and 100% levels permanently inaccessible.
        # Use explicit integer conversion instead: 0→"fib_0", 100→"fib_100".
        raw = pct * 100
        label = "fib_{:.1f}".format(raw) if raw % 1 else "fib_{:d}".format(int(raw))
        levels[label] = round(sw_high - pct * rng, 2)
    levels["sw_high"] = round(sw_high, 2)
    levels["sw_low"]  = round(sw_low,  2)
    return levels


# ─────────────────────────────────────────────────────────────
#  PRO: CANDLESTICK PATTERN DETECTION
# ─────────────────────────────────────────────────────────────

def detect_candlestick_patterns(df):
    """
    Detect classic 1-, 2-, and 3-candle patterns.
    Returns list of pattern name strings (bullish/bearish labeled).
    """
    patterns = []
    if len(df) < 3:
        return patterns

    # Last 3 candles
    c0 = df.iloc[-1]   # current
    c1 = df.iloc[-2]   # prev
    c2 = df.iloc[-3]   # 2-back

    def _body(c):     return abs(float(c["close"]) - float(c["open"]))
    def _range(c):    return float(c["high"]) - float(c["low"])
    def _upper_sh(c): return float(c["high"]) - max(float(c["close"]), float(c["open"]))
    def _lower_sh(c): return min(float(c["close"]), float(c["open"])) - float(c["low"])
    def _bull(c):     return float(c["close"]) > float(c["open"])
    def _bear(c):     return float(c["close"]) < float(c["open"])

    def _prior_trend_down(n=10):
        """
        True when price was FALLING into the current candle, False when it was
        rising, None when there is not enough history to tell.

        Measured on closes strictly before the pattern candle (index -2 versus
        index -2-n) so the pattern's own body cannot influence the trend it is
        being read against.
        """
        cl = df["close"].values
        if len(cl) < n + 2:
            return None
        try:
            return float(cl[-2]) < float(cl[-2 - n])
        except (TypeError, ValueError):
            return None

    b0, r0, us0, ls0 = _body(c0), _range(c0), _upper_sh(c0), _lower_sh(c0)
    b1, r1 = _body(c1), _range(c1)   # us1/ls1 not needed — c1 shadows unused in patterns
    b2                = _body(c2)

    # ── 1-candle patterns ─────────────────────────────────────
    # Doji
    if r0 > 0 and b0 <= 0.1 * r0:
        if us0 >= 2 * ls0 and ls0 < 0.05 * r0:
            patterns.append("GRAVESTONE_DOJI")   # bearish
        elif ls0 >= 2 * us0 and us0 < 0.05 * r0:
            patterns.append("DRAGONFLY_DOJI")    # bullish
        else:
            patterns.append("DOJI")

    #
    # Hammer / Hanging Man — the SAME shape, told apart by the preceding trend.
    #
    # Bug fix: this used to split on body colour (`if _bull(c0)`), but colour is
    # not the discriminator and never was. Per Nison, the identical shape is a
    # bullish reversal after a decline and a bearish warning after an advance.
    # Splitting on colour meant:
    #   • a green hammer at the top of an advance  → labelled HAMMER, so the
    #     scanner said "bullish reversal" at a high (it is a Hanging Man);
    #   • a red hammer at the bottom of a decline  → labelled HANGING_MAN, so
    #     the scanner said "bearish reversal" at a low (it is a Hammer).
    # Both are sign errors placed exactly at the turning points, where a
    # reversal read matters most. Verified before the fix: the classification
    # was byte-identical for a prior uptrend and a prior downtrend.
    #
    # When the trend cannot be determined (too little history) we emit nothing
    # rather than guess: a coin-flip signal is worse than no signal.
    _trend_down = _prior_trend_down()

    if r0 > 0 and b0 > 0 and ls0 >= 2 * b0 and us0 <= 0.3 * b0:
        if _trend_down is True:
            patterns.append("HAMMER")            # bullish reversal
        elif _trend_down is False:
            patterns.append("HANGING_MAN")       # bearish reversal

    # Shooting Star / Inverted Hammer — same shape, same rule, mirrored.
    if r0 > 0 and b0 > 0 and us0 >= 2 * b0 and ls0 <= 0.3 * b0:
        if _trend_down is True:
            patterns.append("INV_HAMMER")        # bullish reversal
        elif _trend_down is False:
            patterns.append("SHOOTING_STAR")     # bearish reversal

    # ── 2-candle patterns ─────────────────────────────────────
    if r1 > 0 and r0 > 0:
        # Bullish Engulfing
        if (_bear(c1) and _bull(c0) and
                float(c0["open"])  < float(c1["close"]) and
                float(c0["close"]) > float(c1["open"])):
            patterns.append("BULL_ENGULF")

        # Bearish Engulfing
        if (_bull(c1) and _bear(c0) and
                float(c0["open"])  > float(c1["close"]) and
                float(c0["close"]) < float(c1["open"])):
            patterns.append("BEAR_ENGULF")

        # Bullish Harami
        if (_bear(c1) and _bull(c0) and
                float(c0["open"])  > float(c1["close"]) and
                float(c0["close"]) < float(c1["open"]) and
                b0 < b1 * 0.6):
            patterns.append("BULL_HARAMI")

        # Bearish Harami
        if (_bull(c1) and _bear(c0) and
                float(c0["open"])  < float(c1["close"]) and
                float(c0["close"]) > float(c1["open"]) and
                b0 < b1 * 0.6):
            patterns.append("BEAR_HARAMI")

        # Piercing Line (bullish)
        # c1 is bearish: body runs from c1["open"] (top) down to c1["close"] (bottom).
        # midpoint = c1["open"] - b1*0.5.  c0 must close ABOVE midpoint but BELOW c1["open"].
        if (_bear(c1) and _bull(c0) and
                float(c0["open"])  < float(c1["low"]) and
                float(c0["close"]) > float(c1["open"]) - b1 * 0.5 and   # Bug fix: was + (impossible)
                float(c0["close"]) < float(c1["open"])):
            patterns.append("PIERCING")

        # Dark Cloud Cover (bearish)
        # c1 is bullish: body runs from c1["open"] (bottom) up to c1["close"] (top).
        # midpoint = c1["open"] + b1*0.5.  c0 must close BELOW midpoint but ABOVE c1["open"].
        if (_bull(c1) and _bear(c0) and
                float(c0["open"])  > float(c1["high"]) and
                float(c0["close"]) < float(c1["open"]) + b1 * 0.5 and   # Bug fix: was - (impossible)
                float(c0["close"]) > float(c1["open"])):
            patterns.append("DARK_CLOUD")

    # ── 3-candle patterns ─────────────────────────────────────
    if b2 > 0 and b1 > 0 and b0 > 0:
        # Morning Star (bullish)
        if (_bear(c2) and b1 < b2 * 0.5 and _bull(c0) and
                float(c0["close"]) > float(c2["open"]) - b2 * 0.5):
            patterns.append("MORNING_STAR")

        # Evening Star (bearish)
        if (_bull(c2) and b1 < b2 * 0.5 and _bear(c0) and
                float(c0["close"]) < float(c2["open"]) + b2 * 0.5):
            patterns.append("EVENING_STAR")

        # Three White Soldiers (bullish)
        # Bug fix: standard definition requires each candle to open WITHIN
        # the prior candle's body. Without this, three gap-up candles
        # (exhaustion pattern) would falsely trigger THREE_WHITE_SOLDIERS.
        if (_bull(c2) and _bull(c1) and _bull(c0) and
                float(c1["close"]) > float(c2["close"]) and
                float(c0["close"]) > float(c1["close"]) and
                b0 > 0.5 * r0 and b1 > 0.5 * r1 and
                float(c2["close"]) >= float(c1["open"]) >= float(c2["open"]) and
                float(c1["close"]) >= float(c0["open"]) >= float(c1["open"])):
            patterns.append("THREE_WHITE_SOLDIERS")

        # Three Black Crows (bearish)
        # Bug fix: same opens-within-body requirement as Three White Soldiers.
        if (_bear(c2) and _bear(c1) and _bear(c0) and
                float(c1["close"]) < float(c2["close"]) and
                float(c0["close"]) < float(c1["close"]) and
                b0 > 0.5 * r0 and b1 > 0.5 * r1 and
                float(c2["open"]) >= float(c1["open"]) >= float(c2["close"]) and
                float(c1["open"]) >= float(c0["open"]) >= float(c1["close"])):
            patterns.append("THREE_BLACK_CROWS")

    return patterns


# ─────────────────────────────────────────────────────────────
#  PRO: RSI DIVERGENCE DETECTION
# ─────────────────────────────────────────────────────────────

def calc_rsi_divergence(df, lookback=30, tf="DAY"):
    """
    Detect regular and hidden RSI divergences over the last `lookback` candles.
    Returns dict with keys: regular_bull, regular_bear, hidden_bull, hidden_bear (bool).

    Fix: previously any two adjacent pivots could trigger a divergence — a 1-bar
    "trough" separated by just one candle from the prior trough was enough.  Also the
    RSI delta threshold of 2 pts is within typical intraday noise.
    Changes:
      • Require a minimum bar gap between the two pivot pairs (TF-aware).
      • Raise RSI delta threshold to 4 pts to filter noise.
    """
    result = {"regular_bull": False, "regular_bear": False,
              "hidden_bull":  False, "hidden_bear":  False}
    if len(df) < lookback + 5 or "rsi" not in df.columns:
        return result

    # Minimum candle separation between two pivot instances to count as distinct.
    _MIN_GAP = {"5MIN": 5, "15MIN": 5, "1HR": 6, "DAY": 5, "WEEK": 4, "MONTH": 3}
    min_gap  = _MIN_GAP.get(tf, 5)
    rsi_delta = 4   # was 2 — raised to filter noise

    sub    = df.tail(lookback).copy()
    prices = sub["close"].values
    rsis   = sub["rsi"].values
    n      = len(prices)

    # Find local lows (price troughs)
    low_idx  = [i for i in range(1, n-1) if prices[i] < prices[i-1] and prices[i] < prices[i+1]]
    # Find local highs (price peaks)
    high_idx = [i for i in range(1, n-1) if prices[i] > prices[i-1] and prices[i] > prices[i+1]]

    if len(low_idx) >= 2:
        i1, i2 = low_idx[-2], low_idx[-1]
        if (i2 - i1) >= min_gap:               # enforce minimum separation
            # Regular Bullish: price lower low, RSI higher low
            if prices[i2] < prices[i1] and rsis[i2] > rsis[i1] + rsi_delta:
                result["regular_bull"] = True
            # Hidden Bullish: price higher low, RSI lower low
            if prices[i2] > prices[i1] and rsis[i2] < rsis[i1] - rsi_delta:
                result["hidden_bull"] = True

    if len(high_idx) >= 2:
        i1, i2 = high_idx[-2], high_idx[-1]
        if (i2 - i1) >= min_gap:               # enforce minimum separation
            # Regular Bearish: price higher high, RSI lower high
            if prices[i2] > prices[i1] and rsis[i2] < rsis[i1] - rsi_delta:
                result["regular_bear"] = True
            # Hidden Bearish: price lower high, RSI higher high
            if prices[i2] < prices[i1] and rsis[i2] > rsis[i1] + rsi_delta:
                result["hidden_bear"] = True

    return result


# ─────────────────────────────────────────────────────────────
#  PRO: 52-WEEK HIGH / LOW
# ─────────────────────────────────────────────────────────────

def calc_52w_levels(df, window_bars=252):
    """
    Return 52-week high, low, and % distance from current price.

    window_bars must match the candle resolution:
        252 → daily bars   (252 trading days ≈ 1 calendar year)   [default]
         52 → weekly bars  (52 weeks         ≈ 1 calendar year)
    Using 252 on weekly data would look back ~4.8 years — far too wide.
    """
    window = df.tail(window_bars) if len(df) >= window_bars else df
    if window.empty:
        return {}
    h52  = float(window["high"].max())
    l52  = float(window["low"].min())
    last = float(df.iloc[-1]["close"])
    pct_from_high = round((last / h52 - 1) * 100, 1) if h52 > 0 else 0
    pct_from_low  = round((last / l52 - 1) * 100, 1) if l52 > 0 else 0
    return {
        "high_52w":      round(h52,  2),
        "low_52w":       round(l52,  2),
        "pct_from_high": pct_from_high,
        "pct_from_low":  pct_from_low,
    }


# ─────────────────────────────────────────────────────────────
#  PRO: COMPOSITE SIGNAL SCORE (0-100)
# ─────────────────────────────────────────────────────────────

def calc_composite_score(entry, tfs=None, target_tf=None):
    """
    Weighted composite score (0-100) measuring overall signal quality.

    Components (max pts):
      Confluence  25 -- fraction of active TFs agreeing on direction
      RSI zone    15 -- RSI position relative to thresholds
      Volume      20 -- relative volume vs 20-period average
      Trend str   20 -- trend strength indicator (-100 to +100)
      SuperTrend   8 -- SuperTrend direction
      MACD cross   6 -- recent MACD crossover event
      Candle pat   6 -- confirming candlestick pattern
      RSI div      5 -- RSI divergence alignment
      Retest BO    5 -- confirmed R1/S1 retest (high conviction)
      ---------- ---
      Max         110 -> capped at 100

    Bug fix: target_tf parameter added so each TF is scored using ITS OWN
    indicator values (RSI, volume, trend_strength, SuperTrend, etc.).
    Previously tf_key always resolved to the first TF with a signal
    (e.g. 5MIN), causing all 6 TFs to get identical scores — because
    5MIN's RSI/vol/trend were used for DAY, WEEK, MONTH scoring too.

    Confluence is still computed across ALL tfs (it's TF-agnostic by design).
    """
    tfs = tfs or TIMEFRAMES_SWING
    n   = max(len(tfs), 1)
    score = 0.0

    # Bug fix: use target_tf's own indicators if provided.
    # Fallback chain: target_tf → first TF with a signal → DAY → tfs[0]
    if target_tf and target_tf in entry:
        tf_key = target_tf
    else:
        tf_key = None
        for tf in tfs:
            if tf in entry and as_dict(entry[tf]).get("signal", "NONE") != "NONE":
                tf_key = tf
                break
        if tf_key is None:
            tf_key = "DAY" if "DAY" in entry else (tfs[0] if tfs else None)
    e = as_dict(entry.get(tf_key)) if tf_key else {}

    # Confluence (0-25): normalized to TF count
    # Bug fix: (conf_raw + n) / (2n) maps a fully BEARISH confluence (-n) to
    # 0, so a perfectly aligned BREAKDOWN scored 0 of 25 while the identical
    # BREAKOUT scored 25.  Strength is |agreement| / n, direction-neutral.
    conf_raw  = confluence_score(entry, tfs)     # sum of +1/-1 per TF
    conf_norm = min(1.0, abs(conf_raw) / max(n, 1))
    score += conf_norm * 25

    # RSI zone (0-15) — symmetric: award points when RSI confirms signal direction.
    # Fix: previously bullish-only; BREAKDOWN signals received 0 RSI points regardless
    # of how bearish RSI was.  Also removed the contradictory oversold-bounce +10 bonus
    # (detect_signal() deliberately gives 0 rsi_bull_pts at RSI<35, so awarding score
    # points there was inconsistent with the signal engine's own exclusion).
    rsi = safe_float(e.get("rsi", 50), 50.0)
    sig = e.get("signal", "NONE")
    if sig == "BREAKDOWN":
        if   rsi < 35: score += 15
        elif rsi < 45: score += 10
        elif rsi < 55: score += 5
    else:                               # BREAKOUT / SIDEWAYS / NONE
        if   rsi > 65: score += 15
        elif rsi > 55: score += 10
        elif rsi > 45: score += 5

    # Volume quality (0-20)
    vol = safe_float(e.get("volume", 0))
    score += min(20, vol * 0.2)

    # Trend strength (0-20)
    # Bug fix: max(0, (ts+100)/200*20) gave an equally strong bearish reading
    # (-96) just 0.4 of 20 points while +96 got 19.2.  Strength, not sign, is
    # what this term measures — direction is already priced in by confluence.
    ts = safe_float(e.get("trend_strength", 0))
    score += min(20, abs(ts) / 100 * 20)

    # SuperTrend direction (0-8) — symmetric fix: previously only +8 for bullish ST;
    # bearish ST awarded 0, creating a 14-pt structural gap vs BREAKDOWN setups.
    st_dir = safe_float(e.get("st_direction", 0))
    if   st_dir ==  1 and sig == "BREAKOUT":  score += 8
    elif st_dir == -1 and sig == "BREAKDOWN": score += 8

    # MACD crossover bonus (0-6) — symmetric fix: previously only BULL_CROSS rewarded.
    mc = e.get("macd_cross", "")
    if   mc == "BULL_CROSS" and sig == "BREAKOUT":  score += 6
    elif mc == "BEAR_CROSS" and sig == "BREAKDOWN": score += 6

    # Candlestick pattern bonus (0-6)
    cps     = as_list(e.get("candle_patterns"))
    bull_cp = sum(1 for p in cps if p in CANDLE_BULL)
    bear_cp = sum(1 for p in cps if p in CANDLE_BEAR)
    if sig == "BREAKOUT"  and bull_cp > 0: score += min(6, bull_cp * 3)
    if sig == "BREAKDOWN" and bear_cp > 0: score += min(6, bear_cp * 3)

    # RSI Divergence bonus (0-5)
    div_ = as_dict(e.get("rsi_divergence"))
    if div_.get("regular_bull") and sig == "BREAKOUT":  score += 5
    if div_.get("hidden_bull")  and sig == "BREAKOUT":  score += 3
    if div_.get("regular_bear") and sig == "BREAKDOWN": score += 5
    if div_.get("hidden_bear")  and sig == "BREAKDOWN": score += 3

    # Retest breakout bonus (0-5)
    if e.get("retest_bo") and sig == "BREAKOUT":  score += 5
    if e.get("retest_bd") and sig == "BREAKDOWN": score += 5

    return min(100, max(0, int(score)))



# ─────────────────────────────────────────────────────────────
#  PRO: WATCHLIST
# ─────────────────────────────────────────────────────────────

def load_watchlist():
    """Return set of starred symbol names from SQLite watchlist table."""
    try:
        con = _db_connect()
        rows = con.execute("SELECT symbol FROM watchlist").fetchall()
        con.close()
        return set(r[0] for r in rows)
    except Exception as ex:
        cprint("  DB load_watchlist error: " + str(ex), C.RED)
        return set()

def save_watchlist(wl):
    """Overwrite watchlist table with the given set of symbols."""
    try:
        con = _db_connect()
        con.execute("DELETE FROM watchlist")
        con.executemany("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)",
                        [(sym,) for sym in wl])
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB save_watchlist error: " + str(ex), C.RED)

def toggle_watchlist(sym):
    try:
        con = _db_connect()
        row = con.execute("SELECT 1 FROM watchlist WHERE symbol=?", (sym,)).fetchone()
        if row:
            con.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
            cprint("  ★ Removed " + sym + " from Watchlist", C.YELLOW)
        else:
            con.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (sym,))
            cprint("  ★ Added " + sym + " to Watchlist", C.GREEN)
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB toggle_watchlist error: " + str(ex), C.RED)


def load_note(sym):
    """Return the saved note string for sym, or '' if none."""
    try:
        con = _db_connect()
        row = con.execute("SELECT note FROM notes WHERE symbol=?", (sym,)).fetchone()
        con.close()
        return row[0] if row else ""
    except Exception as ex:
        cprint("  DB load_note error: " + str(ex), C.RED)
        return ""


def save_note(sym, note):
    """Upsert note for sym into the notes table."""
    try:
        con = _db_connect()
        con.execute(
            "INSERT INTO notes (symbol, note) VALUES (?, ?)"
            " ON CONFLICT(symbol) DO UPDATE SET note=excluded.note",
            (sym, note))
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB save_note error: " + str(ex), C.RED)


def calculate_indicators(df, tf="DAY"):
    # Use per-TF minimum so MONTH (only ~240 candles) isn't blocked by DAY's 250 limit
    min_candles = TF_MIN_CANDLES.get(tf, 60)
    if len(df) < min_candles:
        return df

    # Fix: always work on a copy so caller's DataFrame is never mutated and
    # pandas 2.x SettingWithCopyWarning / silent-drop bugs are avoided.
    df = df.copy()

    close = df["close"]

    # RSI
    df["rsi"]      = calc_rsi(close)

    # EMAs
    df["ema9"]     = close.ewm(span=9,   adjust=False).mean()
    df["ema21"]    = close.ewm(span=21,  adjust=False).mean()
    df["ema50"]    = close.ewm(span=50,  adjust=False).mean()
    df["ema200"]   = close.ewm(span=200, adjust=False).mean()

    # SMAs
    df["sma20"]    = close.rolling(20).mean()
    df["sma50"]    = close.rolling(50).mean()

    # ATR
    df["atr"]      = calc_atr(df)

    # Volume
    df["vol_ma20"] = df["vol"].rolling(20).mean()
    df["rel_vol"]  = (df["vol"] / df["vol_ma20"].replace(0, np.nan)).fillna(1.0)

    # VWAP: intraday only (5MIN / 15MIN / 1HR).
    # DAY is in TF_INTRADAY_V3 (for live-bar fetching) but must NOT get VWAP:
    # a daily bar is a single data point — cumsum/cumsum = typical price of
    # that bar, which has zero analytical value as "VWAP".
    # TIMEFRAMES_INTRADAY = ["5MIN", "15MIN", "1HR"] — excludes DAY correctly.
    if tf in TIMEFRAMES_INTRADAY:
        _tp       = (df["high"] + df["low"] + df["close"]) / 3
        _date_grp = df["ts"].dt.date.astype(str)   # group by calendar date
        _tpvol_cs = (_tp * df["vol"]).groupby(_date_grp).transform("cumsum")
        _vol_cs   = df["vol"].groupby(_date_grp).transform("cumsum").replace(0, np.nan)
        df["vwap"] = _tpvol_cs / _vol_cs
    else:
        df["vwap"] = np.nan   # not meaningful for DAY / WEEK / MONTH bars

    # Bollinger Bands
    df["bb_mid"]   = close.rolling(20).mean()
    bb_std         = close.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_width"] = ((df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]).fillna(0)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = calc_macd(close)

    # Stochastic
    df["stoch_k"], df["stoch_d"] = calc_stochastic(df)

    # SuperTrend
    if len(df) >= 10:
        df["supertrend"], df["st_dir"] = calc_supertrend(df)
    else:
        df["supertrend"] = np.nan
        df["st_dir"]     = 0

    # OI relative %
    if "oi" in df.columns:
        oi_max     = df["oi"].rolling(20).max().replace(0, np.nan)
        df["oi_pct"] = ((df["oi"] / oi_max) * 100).clip(0, 100).fillna(0)
    else:
        df["oi_pct"] = 0

    # ADX (trend strength + directional indicators)
    df["adx"], df["plus_di"], df["minus_di"] = calc_adx(df)

    # Williams %R (fast momentum -100 to 0)
    df["williams_r"] = calc_williams_r(df)

    # CCI (Commodity Channel Index, cyclical extremes)
    df["cci"] = calc_cci(df)

    # MFI (Money Flow Index, volume-weighted RSI)
    df["mfi"] = calc_mfi(df)

    return df


# ─────────────────────────────────────────────────────────────
#  SIGNAL DETECTION  (multi-factor)
# ─────────────────────────────────────────────────────────────

def detect_signal(df, tf="DAY"):
    min_candles = TF_MIN_CANDLES.get(tf, 60)
    if len(df) < min_candles or "rsi" not in df.columns:
        return "NONE", 0, 0, 0, "insufficient data (" + str(len(df)) + " candles)", {}, []

    last  = df.iloc[-1]
    prev  = df.iloc[-2] if len(df) > 1 else last

    # ── Core values ──────────────────────────────────────────
    close    = safe_float(last.get("close",     0))
    rsi      = safe_float(last.get("rsi",       50))
    rel_vol  = safe_float(last.get("rel_vol",   1.0), 1.0)
    atr      = safe_float(last.get("atr",       0))
    oi_pct   = safe_float(last.get("oi_pct",    0))
    sma20    = safe_float(last.get("sma20",     close), close)
    ema9     = safe_float(last.get("ema9",      close), close)
    ema21    = safe_float(last.get("ema21",     close), close)
    ema50    = safe_float(last.get("ema50",     close), close)
    ema200   = safe_float(last.get("ema200",    close), close)
    bb_up    = safe_float(last.get("bb_upper",  0))
    bb_lo    = safe_float(last.get("bb_lower",  0))
    bb_wid   = safe_float(last.get("bb_width",  0))
    macd     = safe_float(last.get("macd",      0))
    macd_sig = safe_float(last.get("macd_signal", 0))
    macd_h   = safe_float(last.get("macd_hist", 0))
    p_macd_h = safe_float(prev.get("macd_hist", 0))
    stoch_k  = safe_float(last.get("stoch_k",  50))
    stoch_d  = safe_float(last.get("stoch_d",  50))
    st_dir   = int(safe_float(last.get("st_dir", 0)))
    p_st_dir = int(safe_float(prev.get("st_dir", 0)))
    adx      = safe_float(last.get("adx",       20))
    plus_di  = safe_float(last.get("plus_di",   25))
    minus_di = safe_float(last.get("minus_di",  25))
    williams_r = safe_float(last.get("williams_r", -50))
    cci      = safe_float(last.get("cci",        0))
    mfi      = safe_float(last.get("mfi",       50))

    # ── Scores ───────────────────────────────────────────────
    # VOL score: relative volume vs 20-day avg. 100% = 3x avg volume.
    vol_score = int(min(100, (rel_vol / 3) * 100))

    # ATR%: ATR as raw % of price — e.g. 2.3 means stock moves 2.3% per candle.
    # Stored as actual float, NOT scaled to 0-100 (scaling breaks across TFs).
    # DAY: typically 1-4%  |  WEEK: 3-8%  |  MONTH: 6-15%
    atr_pct_val = round((atr / max(close, 0.0001)) * 100, 1)

    # OI: equity stocks (NSE_EQ) have no OI — always 0. Keep for F&O compatibility.
    oi_score  = int(min(100, oi_pct))

    # ── Individual signal components ─────────────────────────
    components = {}

    # Price vs EMAs
    components["above_ema9"]   = close > ema9
    components["above_ema21"]  = close > ema21
    components["above_ema50"]  = close > ema50
    components["above_ema200"] = close > ema200
    components["ema9_above21"] = ema9  > ema21   # golden cross short
    components["ema50_above200"]= ema50 > ema200  # golden cross long

    # RSI zones
    # Bug fix: rsi_bull was RSI > RSI_OVERBOUGHT (65) — awarded +1 bull point in the
    # overbought zone, exactly where reversal risk is highest. The correct approach
    # for trend-following is the MOMENTUM ZONE:
    #   rsi_bull = 50 < RSI ≤ 65  (above midline, not yet overbought — confirms uptrend)
    #   rsi_bear = 35 ≤ RSI < 50  (below midline, not yet oversold — confirms downtrend)
    # Stocks with RSI > 65 (overbought) or RSI < 35 (oversold) score 0 on RSI —
    # they are in extreme zones where mean-reversion risk is elevated.
    components["rsi_bull"]   = RSI_OVERSOLD < rsi <= RSI_OVERBOUGHT and rsi > 50
    components["rsi_bear"]   = RSI_OVERSOLD <= rsi < 50 and rsi < RSI_OVERBOUGHT
    components["rsi_neutral"]= RSI_OVERSOLD <= rsi <= RSI_OVERBOUGHT

    # BB
    components["bb_breakout"]  = bb_up > 0 and close > bb_up
    components["bb_breakdown"] = bb_lo > 0 and close < bb_lo
    components["bb_squeeze"]   = bb_wid < 0.05

    # MACD
    components["macd_bull"]    = macd > macd_sig and macd_h > 0
    components["macd_bear"]    = macd < macd_sig and macd_h < 0
    components["macd_cross_up"]= macd_h > 0 and p_macd_h <= 0
    components["macd_cross_dn"]= macd_h < 0 and p_macd_h >= 0

    # Stochastic
    components["stoch_bull"]   = stoch_k > stoch_d and stoch_k > STOCH_OS
    # Bug fix: was K<D and K<STOCH_OS(20) — fired only 18% of bars vs stoch_bull 48%.
    # Symmetric definition: K<D and K>STOCH_OS(20) — descending momentum, not bottomed.
    # Old version only triggered in extreme oversold zone (rare); this makes both
    # conditions equally likely to fire under random market conditions.
    components["stoch_bear"]   = stoch_k < stoch_d and stoch_k < STOCH_OB
    components["stoch_ob"]     = stoch_k > STOCH_OB
    components["stoch_os"]     = stoch_k < STOCH_OS

    # SuperTrend
    components["st_bull"]      = st_dir == 1
    components["st_cross_up"]  = st_dir == 1 and p_st_dir == -1
    components["st_cross_dn"]  = st_dir == -1 and p_st_dir == 1

    # Volume
    components["vol_spike"]    = rel_vol > VOL_SPIKE

    # ADX: trend strength confirmation
    # ADX > 25 = trending market (signal is more reliable)
    # ADX > 40 = strong trend (high conviction)
    # +DI > -DI = bullish directional pressure
    components["adx_trending"] = adx > 25
    components["adx_strong"]   = adx > 40
    components["adx_bull_di"]  = plus_di > minus_di    # bullish directional bias
    components["adx_bear_di"]  = minus_di > plus_di    # bearish directional bias

    # Williams %R momentum zones
    # Bullish momentum: -50 to -20 (above midpoint, not yet overbought)
    # Bearish momentum: -80 to -50 (below midpoint, not yet oversold)
    components["wr_bull"]      = -50 < williams_r <= -20
    components["wr_bear"]      = -80 <= williams_r < -50

    # MFI: volume-confirmed accumulation/distribution
    # Bullish: 50-80 (positive money flow, not overbought)
    # Bearish: 20-50 (negative money flow, not oversold)
    components["mfi_bull"]     = 50 < mfi <= 80
    components["mfi_bear"]     = 20 <= mfi < 50

    # CCI: trend confirmation
    # Bull: > 0 (above centreline); Bear: < 0 (below centreline)
    components["cci_bull"]     = cci > 0
    components["cci_bear"]     = cci < 0

    # ── Retest breakout / breakdown ───────────────────────────
    # Detect confirmed second-attempt breakout through R1 or breakdown through S1.
    # This is stronger than a first-touch signal: the pullback and re-break
    # flushes weak hands and confirms institutional interest at the level.
    _rtbo = detect_retest_breakout(df)
    components["retest_bo"]       = _rtbo["retest_bo"]
    components["retest_bd"]       = _rtbo["retest_bd"]
    components["retest_bo_level"] = _rtbo["retest_bo_level"]
    components["retest_bd_level"] = _rtbo["retest_bd_level"]

    # ── BULL score (0-15) ────────────────────────────────────
    bull_pts = sum([
        components["above_ema21"],      # price above short EMA
        components["above_ema50"],      # price above mid EMA
        components["above_ema200"],     # price above long-term trend filter (was missing)
        components["ema9_above21"],     # short EMA > mid EMA (golden cross short)
        components["ema50_above200"],   # mid EMA > long EMA  (golden cross long, was missing)
        components["rsi_bull"],         # RSI in bullish momentum zone (50-65)
        components["macd_bull"],        # MACD above signal
        components["st_bull"],          # SuperTrend bullish
        components["stoch_bull"],       # Stochastic bullish
        components["vol_spike"],        # volume confirming move
        components["retest_bo"],        # confirmed R1 retest breakout (+1 bonus)
        # New indicators
        components["adx_bull_di"],      # ADX +DI > -DI (bullish directional bias)
        components["wr_bull"],          # Williams %R bullish momentum zone
        components["mfi_bull"],         # MFI positive money flow (smart money buying)
        components["cci_bull"],         # CCI above centreline
    ])

    # ── BEAR score (0-15) ────────────────────────────────────
    bear_pts = sum([
        not components["above_ema21"],       # price below short EMA
        not components["above_ema50"],       # price below mid EMA
        not components["above_ema200"],      # price below long-term trend filter (was missing)
        not components["ema9_above21"],      # short EMA < mid EMA (death cross short)
        not components["ema50_above200"],    # mid EMA < long EMA  (death cross long, was missing)
        components["rsi_bear"],              # RSI in bearish momentum zone (35-50)
        components["macd_bear"],             # MACD below signal
        not components["st_bull"],           # SuperTrend bearish
        components["stoch_bear"],            # descending stochastic momentum
        components["vol_spike"],             # high-volume breakdown = conviction (was missing)
        components["retest_bd"],             # confirmed S1 retest breakdown (+1 bonus)
        # New indicators
        components["adx_bear_di"],           # ADX -DI > +DI (bearish directional bias)
        components["wr_bear"],               # Williams %R bearish momentum zone
        components["mfi_bear"],              # MFI negative money flow (smart money selling)
        components["cci_bear"],              # CCI below centreline
    ])

    # ── Determine signal ─────────────────────────────────────
    reasons = []

    if components["bb_breakout"]:     reasons.append("BB_breakout")
    if components["macd_cross_up"]:   reasons.append("MACD_cross_up")
    if components["st_cross_up"]:     reasons.append("ST_flip_bull")
    if components["ema9_above21"]:    reasons.append("EMA9>21")
    if components["above_ema50"]:     reasons.append("above_EMA50")
    if components["rsi_bull"]:        reasons.append("RSI_momentum_bull")
    if components["vol_spike"]:       reasons.append("Vol_spike")
    if components["adx_trending"] and components["adx_bull_di"]:
        reasons.append("ADX_trend_bull(" + str(round(adx, 1)) + ")")
    if components["mfi_bull"]:        reasons.append("MFI_accumulation")
    if components["wr_bull"]:         reasons.append("WR_bull_momentum")
    if components["retest_bo"]:
        reasons.append("R1_RETEST_BO@₹" + str(components["retest_bo_level"]))

    if components["bb_breakdown"]:    reasons.append("BB_breakdown")
    if components["macd_cross_dn"]:   reasons.append("MACD_cross_dn")
    if components["st_cross_dn"]:     reasons.append("ST_flip_bear")
    if components["adx_trending"] and components["adx_bear_di"]:
        reasons.append("ADX_trend_bear(" + str(round(adx, 1)) + ")")
    if components["mfi_bear"]:        reasons.append("MFI_distribution")
    if components["wr_bear"]:         reasons.append("WR_bear_momentum")
    if components["retest_bd"]:
        reasons.append("S1_RETEST_BD@₹" + str(components["retest_bd_level"]))

    # ── Retest breakout: high-conviction trigger ─────────────
    # Bug fix: previously fired BREAKOUT/BREAKDOWN unconditionally (only
    # checked the opposite BB as an anti-condition). This meant a maximally
    # bearish stock (bear_pts=11, bull_pts=0) still got BREAKOUT if a retest
    # pattern existed. Now requires minimum directional context (>= 3 pts)
    # so the pattern must be supported by at least some indicator agreement.
    if components["retest_bo"] and bull_pts >= 4 and not components["bb_breakdown"]:
        signal = "BREAKOUT"
    elif components["retest_bd"] and bear_pts >= 4 and not components["bb_breakout"]:
        signal = "BREAKDOWN"
    elif (components["bb_breakout"] and bull_pts >= 3) or bull_pts >= 7:
        signal = "BREAKOUT"
    elif (components["bb_breakdown"] and bear_pts >= 3) or bear_pts >= 7:
        signal = "BREAKDOWN"
    # Sideways (squeeze or tight range)
    elif components["bb_squeeze"] or (abs(close - sma20) < atr * 0.5 and rel_vol < 1.2):
        signal = "SIDEWAYS"
        reasons = ["BB_squeeze" if components["bb_squeeze"] else "tight_ATR+low_vol"]
    else:
        signal = "NONE"
        reasons = ["no_clear_signal"]

    # Bug fix: reasons were silently truncated to 4, hiding valid signal factors (e.g. Vol_spike).
    return signal, atr_pct_val, vol_score, oi_score, " | ".join(reasons), components, []


# ─────────────────────────────────────────────────────────────
#  EXTRA ANALYSIS: Price Targets, S/R, Trend Strength
# ─────────────────────────────────────────────────────────────

def get_price_targets(df, signal, tf="DAY"):
    """
    Price targets and stop-loss using ATR + swing S/R levels.

    Bug fix (R:R on WEEK/MONTH): the old code always used lookback=30 bars
    regardless of timeframe. On a WEEK chart that spans 30 weeks (7 months),
    the nearest swing low could be ₹239 away — a completely unusable stop.
    The R:R would come out at 0.3:1 even on a strong breakout setup.

    Fix — two changes:
      1. TF-aware lookback: scan only a short recent window so S/R levels
         are local and actionable, not ancient history.
         5MIN/15MIN → 20 bars   1HR → 20 bars
         DAY        → 25 bars   WEEK → 12 bars   MONTH → 8 bars

      2. ATR cap on SL distance: if the nearest swing support/resistance
         found is further than max_sl_atr × ATR from current price, it is
         too far to be a practical stop — discard it and use the ATR
         fallback instead. This prevents runaway risk figures.
         5MIN/15MIN/1HR → 3× ATR cap
         DAY            → 4× ATR cap
         WEEK           → 5× ATR cap
         MONTH          → 6× ATR cap

    T1/T2 targets use a symmetric cap: swing level must be reachable within
    max_t_atr × ATR, otherwise the ATR-multiple fallback is used.
    """
    if df.empty or "atr" not in df.columns:
        return {}
    last  = df.iloc[-1]
    close = safe_float(last.get("close", 0))
    atr   = safe_float(last.get("atr",   0))
    if close <= 0 or atr <= 0:
        return {}

    # ── Per-TF parameters ────────────────────────────────────
    _TF_PARAMS = {
        #  tf        lookback  max_sl_atr  max_t_atr  min_sl_atr
        "5MIN":   (  20,        3.0,         6.0,        0.3 ),
        "15MIN":  (  20,        3.0,         6.0,        0.3 ),
        "1HR":    (  20,        3.0,         6.0,        0.4 ),
        "DAY":    (  25,        4.0,         8.0,        0.5 ),  # ≥0.5×ATR away
        "WEEK":   (  12,        5.0,        10.0,        0.7 ),
        "MONTH":  (   8,        6.0,        12.0,        1.0 ),
    }
    lookback_n, max_sl_atr, max_t_atr, min_sl_atr = _TF_PARAMS.get(
        tf, _TF_PARAMS["DAY"])  # safe default

    # Minimum separation between T1 and T2, as a fraction of ATR. Two swing
    # levels closer than this are one level for trading purposes.
    _MIN_T2_GAP_ATR = 0.5

    lookback = min(lookback_n, len(df))
    recent   = df.tail(lookback)
    highs    = recent["high"]
    lows     = recent["low"]

    max_sl_dist = max_sl_atr * atr   # absolute price distance cap for SL
    min_sl_dist = min_sl_atr * atr   # minimum stop distance — rejects micro-levels
    max_t_dist  = max_t_atr  * atr   # absolute price distance cap for targets

    # Swing highs above price AND within target cap → resistance / BO targets
    res_levels = sorted(set(
        round(float(h), 2) for i, h in enumerate(highs)
        if i > 0 and i < len(highs) - 1
        and h == highs.iloc[max(0, i-2):i+3].max()
        and float(h) > close
        and float(h) - close <= max_t_dist          # ← target cap
        and float(h) - close >= min_sl_dist          # ← min distance (same floor for symmetry)
    ))

    # Swing lows below price AND within SL cap AND far enough to be meaningful.
    # Bug fix: no minimum distance meant a swing low ₹0.9 below a ₹1200 DAY close
    # (ATR=₹24.86) was used as stop, producing 28.2:1 R:R. min_sl_dist rejects
    # micro-supports that are closer than min_sl_atr×ATR; the ATR fallback then fires.
    sup_levels = sorted(set(
        round(float(l), 2) for i, l in enumerate(lows)
        if i > 0 and i < len(lows) - 1
        and l == lows.iloc[max(0, i-2):i+3].min()
        and float(l) < close
        and close - float(l) <= max_sl_dist         # ← SL cap
        and close - float(l) >= min_sl_dist         # ← SL floor: reject micro-supports
    ), reverse=True)

    if signal == "BREAKOUT":
        t1 = res_levels[0] if res_levels          else round(close + 1.5 * atr, 2)
        t2 = res_levels[1] if len(res_levels) > 1 else round(close + 3.0 * atr, 2)
        sl = sup_levels[0] if sup_levels           else round(close - 1.0 * atr, 2)
        # Bug fix: the T2 fallback is a fixed 3×ATR measured from `close`,
        # but T1 is the nearest qualifying swing level — which the caps allow
        # to sit anywhere up to max_t_atr (8×ATR on DAY) away. When exactly
        # one level qualified and it was further out than 3×ATR, T2 landed
        # BELOW T1, so the trade plan read "T1 ₹1100 / T2 ₹1060" and told the
        # user to book the second half at a worse price than the first.
        #
        # Second half of the same bug: two swing highs 0.5×ATR or less apart
        # are the *same* level as far as a trader is concerned — well inside
        # one session's noise. That printed "T1 ₹969.23 / T2 ₹970.02", i.e.
        # "book half at 969 and the rest at 970", which is not a ladder.
        # In both cases rebuild T2 as an ATR-scaled extension of T1.
        if t2 - t1 < _MIN_T2_GAP_ATR * atr:
            t2 = round(t1 + 1.5 * atr, 2)
        return {"target1": _floor_price(t1, close),
                "target2": _floor_price(t2, close),
                "stop":    _floor_price(sl, close)}

    elif signal == "BREAKDOWN":
        t1 = sup_levels[0] if sup_levels           else round(close - 1.5 * atr, 2)
        t2 = sup_levels[1] if len(sup_levels) > 1  else round(close - 3.0 * atr, 2)
        sl = res_levels[0] if res_levels           else round(close + 1.0 * atr, 2)
        # Mirror of the fix above: on a short, T2 must sit BELOW T1 by a
        # meaningful margin. A support 3.5×ATR down (inside the 4×ATR SL cap)
        # produced "T1 ₹930 / T2 ₹940" — cover the second half at a worse
        # price than the first.
        if t1 - t2 < _MIN_T2_GAP_ATR * atr:
            t2 = round(t1 - 1.5 * atr, 2)
        return {"target1": _floor_price(t1, close),
                "target2": _floor_price(t2, close),
                "stop":    _floor_price(sl, close)}

    return {}


def _floor_price(level, close):
    """Keep a computed price level strictly positive.

    Bug fix: with a very large ATR (circuit-breaker gap, bad tick, suspension
    resumption) the ATR fallback `close - 3 × ATR` goes negative and the report
    showed targets like "T1: ₹-84.81".  A price can never be <= 0, so levels
    are floored at 1% of the current price.
    """
    try:
        v = float(level)
    except (TypeError, ValueError):
        return round(close, 2)
    if not np.isfinite(v):
        return round(close, 2)
    return round(max(v, close * 0.01), 2)

def trend_strength(components):
    """Returns bull/bear score as % strength (-100 to +100).

    v8.0 update: Added 4 new indicators (ADX DI, Williams %R, MFI, CCI) to
    both bull and bear sides so trend bar reflects ALL v8.0 signals.
    Previously only 8 old indicators were used — new indicators had zero weight
    in the trend bar despite contributing to BREAKOUT/BREAKDOWN detection.

    12 keys each side × multiplier 8 → max ±96 ≈ ±100 (clamped).
    Old: 8 keys × 14 = ±112 → clamped to ±100.  Same effective range.
    """
    if not components:
        return 0
    # 12 bull keys (8 original + 4 v8.0)
    bull_keys = ["above_ema21", "above_ema50", "ema9_above21", "rsi_bull",
                 "macd_bull", "st_bull", "stoch_bull", "vol_spike",
                 "adx_bull_di", "wr_bull", "mfi_bull", "cci_bull"]
    bull = sum(1 for k in bull_keys if components.get(k))
    # 12 bear keys (8 original + 4 v8.0)
    # Bug fix: the bear list was NOT the mirror of the bull list — it mixed in
    # `bb_breakdown` and `ema50_above200`, which have no bull-side counterpart.
    # On geometrically mirrored data that asymmetry scored +56 bull vs -72 bear
    # for the same move.  Every key below is now the exact negation of its
    # bull-side counterpart.
    # Bug fix: the bear list was NOT the mirror of the bull list — it mixed in
    # `bb_breakdown` and `ema50_above200`, neither of which has a bull-side
    # counterpart.  On geometrically mirrored data that asymmetry scored the
    # same move +56 bull vs -72 bear.  Every key below is now the exact
    # negation of its bull-side counterpart.
    bear = sum([
        not components.get("above_ema21",  True),
        not components.get("above_ema50",  True),
        not components.get("ema9_above21", True),
        components.get("rsi_bear",    False),
        components.get("macd_bear",   False),
        not components.get("st_bull", True),
        components.get("stoch_bear",  False),
        components.get("vol_spike",   False),
        components.get("adx_bear_di", False),
        components.get("wr_bear",     False),
        components.get("mfi_bear",    False),
        components.get("cci_bear",    False),
    ])
    net = bull - bear
    # Multiplier 8: 12 keys × 8 = 96 ≈ 100 max; "moderate" = 6 × 8 = 48
    return max(-100, min(100, net * 8))


# ─────────────────────────────────────────────────────────────
#  DATA STORE  —  SQLite backend
#
#  Tables
#  ──────
#  scan_data   : one row per (symbol, tf); tf entry stored as JSON blob
#  history     : signal history rows, one per (symbol, tf, candle_date)
#  watchlist   : one row per starred symbol
# ─────────────────────────────────────────────────────────────

_SCHEMAS_READY = set()   # DB_FILEs whose DDL has already been applied


def _db_connect():
    """Return a connection to the SQLite database, creating tables if needed.

    The DDL runs once per database file instead of once per connection —
    a full scan opens hundreds of short-lived connections.
    """
    con = sqlite3.connect(DB_FILE)
    con.execute("PRAGMA journal_mode=WAL")
    if DB_FILE in _SCHEMAS_READY:
        return con
    con.execute("""
        CREATE TABLE IF NOT EXISTS scan_data (
            symbol  TEXT NOT NULL,
            tf      TEXT NOT NULL,
            data    TEXT NOT NULL,
            PRIMARY KEY (symbol, tf)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            tf          TEXT NOT NULL,
            logged_at   TEXT NOT NULL,
            data        TEXT NOT NULL
        )""")
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_hist_sym_tf
            ON history (symbol, tf)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            symbol TEXT PRIMARY KEY,
            note   TEXT NOT NULL DEFAULT ''
        )""")
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_hist_logged_at
            ON history (symbol, tf, logged_at)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS candle_cache (
            instrument_key TEXT NOT NULL,
            tf             TEXT NOT NULL,
            ts             TEXT NOT NULL,
            open           REAL,
            high           REAL,
            low            REAL,
            close          REAL,
            vol            REAL,
            oi             REAL DEFAULT 0,
            PRIMARY KEY (instrument_key, tf, ts)
        )""")
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_candle_cache_key_tf_ts
            ON candle_cache (instrument_key, tf, ts)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS candle_cache_meta (
            instrument_key TEXT NOT NULL,
            tf             TEXT NOT NULL,
            last_attempt   TEXT NOT NULL,
            PRIMARY KEY (instrument_key, tf)
        )""")
    con.commit()
    _SCHEMAS_READY.add(DB_FILE)
    return con


_corrupt = [0]      # rows that failed to decode during the last load_data()


def load_data():
    """Load all scan_data rows into the nested dict {symbol: {tf: entry}}."""
    data = {}
    try:
        con = _db_connect()
        for row in con.execute("SELECT symbol, tf, data FROM scan_data"):
            sym, tf, blob = row
            if sym not in data:
                data[sym] = {"symbol": sym}
            try:
                data[sym][tf] = json.loads(blob)
            except Exception:
                # Bug fix: a corrupt row used to be dropped silently, so the
                # symbol/TF simply disappeared from every view.  Substitute a
                # blank entry (all readers use .get) and tell the user.
                data[sym][tf] = empty_entry()
                _corrupt[0] += 1
        con.close()
        if _corrupt[0]:
            cprint("  ⚠ " + str(_corrupt[0]) + " corrupt scan row(s) were "
                   "reset — re-scan to rebuild them", C.YELLOW)
    except Exception as ex:
        cprint("  DB load_data error: " + str(ex), C.RED)
    return data


def save_data(data):
    """Upsert all (symbol, tf) entries from data dict into scan_data table."""
    try:
        con = _db_connect()
        rows = []
        for sym, sym_data in data.items():
            for tf in TF_CONFIG:
                if tf in sym_data:
                    rows.append((sym, tf, json.dumps(sym_data[tf])))
        con.executemany(
            "INSERT OR REPLACE INTO scan_data (symbol, tf, data) VALUES (?, ?, ?)",
            rows)
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB save_data error: " + str(ex), C.RED)


def load_history(limit_per_key=None):
    """Return history dict {symbol_tf: [entry, ...]} (last 100 per key).

    Bug fix: this read the ENTIRE history table on every call, and it is called
    by view_detail (per symbol) and history_view.  With 50 symbols x 6 TFs x
    100 rows that is 30,000 JSON blobs parsed per keypress.  `limit_per_key`
    lets callers ask only for what they display.
    """
    h = {}
    try:
        con = _db_connect()
        if limit_per_key:
            rows = con.execute(
                "SELECT symbol, tf, logged_at, data FROM ("
                "  SELECT symbol, tf, logged_at, data, id,"
                "         ROW_NUMBER() OVER (PARTITION BY symbol, tf "
                "         ORDER BY id DESC) AS rn FROM history"
                ") WHERE rn <= ? ORDER BY id", (int(limit_per_key),))
        else:
            rows = con.execute(
                "SELECT symbol, tf, logged_at, data FROM history ORDER BY id")
        for row in rows:
            sym, tf, logged_at, blob = row
            key = sym + "_" + tf
            if key not in h:
                h[key] = []
            try:
                entry = json.loads(blob)
                entry["logged_at"] = logged_at
                h[key].append(entry)
            except Exception:
                pass
        con.close()
    except Exception as ex:
        cprint("  DB load_history error: " + str(ex), C.RED)
    return h


def save_history(h):
    """Bulk-import history dict into the DB (migration utility only — not called at runtime).
    Normal incremental logging goes through log_history().
    Existing rows with the same (symbol, tf, logged_at, data) are left intact;
    new rows are appended.
    """
    try:
        con = _db_connect()
        rows = []
        for key, entries in h.items():
            parts = key.split("_", 1)
            if len(parts) != 2:
                continue
            sym, tf = parts
            for e in entries:
                logged_at = e.get("logged_at", "")
                rows.append((sym, tf, logged_at, json.dumps(e)))
        if rows:
            con.executemany(
                "INSERT INTO history (symbol, tf, logged_at, data) VALUES (?, ?, ?, ?)",
                rows)
            # Trim each (symbol, tf) to latest 100 rows after bulk import
            keys = set((r[0], r[1]) for r in rows)
            for sym, tf in keys:
                con.execute("""
                    DELETE FROM history WHERE id IN (
                        SELECT id FROM history
                        WHERE symbol=? AND tf=?
                        ORDER BY id DESC
                        LIMIT -1 OFFSET 100
                    )""", (sym, tf))
        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB save_history error: " + str(ex), C.RED)


def log_history(symbol, tf, entry):
    """Write one history snapshot directly to SQLite (no full reload needed)."""
    snap = {k: v for k, v in entry.items() if isinstance(v, (str, int, float, bool))}
    # Keep the trade levels: history rows used to lose them entirely because
    # the filter above drops dicts, so you could see "BREAKOUT @ ₹120" but not
    # the stop/target that went with it.
    tgt = entry.get("targets") or {}
    if isinstance(tgt, dict):
        for k in ("target1", "target2", "stop"):
            if k in tgt and isinstance(tgt[k], (int, float)):
                snap["targets_" + k] = tgt[k]
    # ── Use candle_date (data timestamp) not wall-clock scan time ──
    # Wall-clock time is misleading: a DAY scan at 18:09 IST would show
    # "2026-02-20 18:09" but the data is yesterday's (2026-02-19) close.
    # candle_date is set in _scan_one_symbol from the last candle's ts.
    candle_date = entry.get("candle_date") or datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    snap["logged_at"] = candle_date

    try:
        con = _db_connect()
        # ── Deduplication guard ───────────────────────────────────
        # Don't log if the last entry for this (symbol, tf) has the same
        # candle_date AND same signal — avoids duplicate rows on re-scans.
        row = con.execute(
            "SELECT logged_at, data FROM history "
            "WHERE symbol=? AND tf=? ORDER BY id DESC LIMIT 1",
            (symbol, tf)).fetchone()
        if row:
            last_logged_at = row[0]
            try:
                last_sig = json.loads(row[1]).get("signal", "")
            except Exception:
                last_sig = ""
            if last_logged_at == candle_date and last_sig == snap.get("signal"):
                con.close()
                return   # identical — skip

        con.execute(
            "INSERT INTO history (symbol, tf, logged_at, data) VALUES (?, ?, ?, ?)",
            (symbol, tf, candle_date, json.dumps(snap)))

        # ── Keep only the latest 100 rows per (symbol, tf) ───────
        con.execute("""
            DELETE FROM history WHERE id IN (
                SELECT id FROM history
                WHERE symbol=? AND tf=?
                ORDER BY id DESC
                LIMIT -1 OFFSET 100
            )""", (symbol, tf))

        con.commit()
        con.close()
    except Exception as ex:
        cprint("  DB log_history error: " + str(ex), C.RED)

def empty_entry():
    return {
        "signal": "NONE", "atr_pct": 0, "volume": 0, "oi": 0,
        "rsi": 0.0, "rel_vol": 0.0, "reason": "", "note": "", "updated": "",
        "price": 0.0, "atr": 0.0, "trend_strength": 0,
        "day_open": 0.0, "day_high": 0.0, "day_low": 0.0,
        "targets": {}, "support": [], "resistance": [],
        "macd_cross": "", "st_direction": 0,
        "ema_alignment": "",
        # PRO fields
        "composite_score": 0,
        "candle_patterns": [],
        "rsi_divergence":  {},
        "fibonacci":       {},
        "52w":             {},
        "risk_reward":     0.0,
        "prev_signal":     "NONE",
        "signal_changed":  False,
        "gap":             {},
        "macd_hist_val":   0.0,   # actual MACD histogram value (for momentum screener)
        # v8 fields — included so every entry has the SAME key set whether it
        # came from a full scan or from the short-data early-return path.  A
        # heterogeneous shape is what made old data.json files blow up in views
        # that assumed a key existed.
        "adx":             0.0,
        "plus_di":         0.0,
        "minus_di":        0.0,
        "mfi":             50.0,
        "williams_r":     -50.0,
        "cci":             0.0,
        "bb_width":        0.0,
        "ema9":            0.0,
        "ema21":           0.0,
        "ema50":           0.0,
        "ema200":          0.0,
        "stoch_k":         50.0,
        "stoch_d":         50.0,
        "price_change":    0.0,
        "st_direction_prev": 0,
        "candle_date":     "",
        "retest_bo":       False,
        "retest_bd":       False,
        "retest_bo_level": 0.0,
        "retest_bd_level": 0.0,
    }

def empty_symbol(sym):
    return {"symbol": sym,
            **{tf: empty_entry() for tf in list(TF_CONFIG.keys())}}

def ensure_symbol(data, sym):
    if sym not in data:
        data[sym] = empty_symbol(sym)
    # Ensure all TF keys exist
    for tf in TF_CONFIG:
        if tf not in data[sym]:
            data[sym][tf] = empty_entry()


# ─────────────────────────────────────────────────────────────
#  ANALYSIS HELPERS
# ─────────────────────────────────────────────────────────────

SIGNAL_SHORT = {"BREAKOUT": "BO", "BREAKDOWN": "BD", "SIDEWAYS": "SW", "NONE": "--"}
SIGNAL_LABEL = {
    "BREAKOUT":  "↑  BREAKOUT",
    "BREAKDOWN": "↓  BREAKDOWN",
    "SIDEWAYS":  "→  SIDEWAYS",
    "NONE":      "—  NONE"
}
SIGNAL_BIAS = {"BREAKOUT": 1, "BREAKDOWN": -1, "SIDEWAYS": 0, "NONE": 0}

def confluence_score(sym_data, tfs=None):
    tfs = tfs or TIMEFRAMES_SWING
    return sum(SIGNAL_BIAS.get(as_dict(sym_data.get(tf)).get("signal", "NONE"), 0) for tf in tfs if tf in sym_data)

def confluence_label(score):
    # Bug fix: was a dict lookup that silently returned "NEUTRAL [0]" for any |score| > 3.
    # When the user enables >3 TFs (e.g. all 6), scores can reach ±6 and the label
    # must still reflect the correct direction — not misleadingly show neutral.
    if   score >= 3:  return C.GREEN  + C.BOLD + "STRONG BULL [+++]" + C.RESET
    elif score == 2:  return C.GREEN  + "BULL        [++ ]" + C.RESET
    elif score == 1:  return C.GREEN  + "WEAK BULL   [+  ]" + C.RESET
    elif score == 0:  return C.YELLOW + "NEUTRAL     [ 0 ]" + C.RESET
    elif score == -1: return C.RED    + "WEAK BEAR   [-  ]" + C.RESET
    elif score == -2: return C.RED    + "BEAR        [-- ]" + C.RESET
    else:             return C.RED    + C.BOLD + "STRONG BEAR [---]" + C.RESET

def conflict_status(sym_data, tfs=None):
    tfs    = tfs or TIMEFRAMES_SWING
    active = [as_dict(sym_data[tf]).get("signal", "NONE") for tf in tfs
              if tf in sym_data]
    active = [sig for sig in active if sig != "NONE"]
    if not active:              return C.DIM    + "NO SIGNAL"  + C.RESET
    if len(set(active)) == 1:  return C.GREEN  + "ALIGNED  → " + active[0] + C.RESET
    return                             C.YELLOW + "CONFLICT → mixed signals" + C.RESET

def ema_alignment_label(c):
    if not c:
        return "—"
    if c.get("above_ema200") and c.get("ema50_above200") and c.get("ema9_above21"):
        return C.GREEN + "FULL BULL STACK" + C.RESET
    elif not c.get("above_ema200") and not c.get("ema50_above200") and not c.get("ema9_above21"):
        return C.RED   + "FULL BEAR STACK" + C.RESET
    elif c.get("ema9_above21") and c.get("above_ema21"):
        return C.GREEN + "BULLISH" + C.RESET
    elif not c.get("ema9_above21") and not c.get("above_ema21"):
        return C.RED   + "BEARISH" + C.RESET
    return C.YELLOW + "MIXED" + C.RESET

def gather_alerts(sym, sym_data, tfs=None):
    tfs    = tfs or TIMEFRAMES_SWING
    alerts = []
    # Coerce everything: sym_data[tf] is whatever was in the DB blob, and a
    # stale or hand-edited row can hold a non-dict. `sym_data[tf]["signal"]`
    # raised TypeError/KeyError and took the whole alert screen down.
    active = [as_dict(sym_data[tf]).get("signal", "NONE") for tf in tfs
              if tf in sym_data]
    active = [sig for sig in active if sig != "NONE"]

    # Conflict
    if len(set(active)) > 1:
        alerts.append((sym, "CONFLICT",       "Mixed signals across TFs"))

    # Confluence
    score = confluence_score(sym_data, tfs)
    n_all = str(len([tf for tf in tfs if tf in sym_data]))
    if score >= 3:  alerts.append((sym, C.GREEN + "STRONG BULL" + C.RESET,
                                   "All " + n_all + " TFs BREAKOUT"))
    if score <= -3: alerts.append((sym, C.RED   + "STRONG BEAR" + C.RESET,
                                   "All " + n_all + " TFs BREAKDOWN"))

    for tf in tfs:
        if tf not in sym_data:
            continue
        e = as_dict(sym_data[tf])
        # Skip timeframes that have never been scanned.
        #
        # Bug fix: empty_entry() carries rsi=0.0, so every unscanned TF tripped
        # the `rsi < 20` branch below and reported "RSI EXTREME oversold" for
        # every symbol on the watchlist — three phantom alerts per symbol on a
        # fresh install, and six per symbol on a partial scan run. The value is
        # not "measured as 0", it is simply absent, so no alert can be drawn
        # from it.
        if not is_scanned_entry(e):
            continue
        sig = e.get("signal", "NONE")
        vol = safe_float(e.get("volume", 0))
        oi  = safe_float(e.get("oi",     0))
        rsi = safe_float(e.get("rsi",   50))
        mc  = e.get("macd_cross", "")
        std = safe_float(e.get("st_direction", 0))

        if sig == "BREAKOUT"  and vol >= 75:
            alerts.append((sym, "HI-VOL BO  [" + tf + "]", "Vol=" + str(vol) + "%"))
        if sig == "BREAKDOWN" and vol >= 75:
            alerts.append((sym, "HI-VOL BD  [" + tf + "]", "Vol=" + str(vol) + "%"))
        if sig == "BREAKOUT"  and vol < 35:
            alerts.append((sym, "WEAK BO    [" + tf + "]", "Vol=" + str(vol) + "% low conviction"))
        # Bug fix: NSE_EQ stocks have no OI data (always 0). Guard with oi > 0
        # so this alert only fires for F&O instruments where OI is meaningful.
        # Previously fired on EVERY equity BREAKOUT, flooding the alert screen.
        if sig == "BREAKOUT" and oi > 0 and oi < 30:
            alerts.append((sym, "OI WARN    [" + tf + "]", "OI=" + str(oi) + "% low"))
        if rsi > 80:
            alerts.append((sym, "RSI EXTREME[" + tf + "]", "RSI=" + str(rsi) + " overbought"))
        if rsi < 20:
            alerts.append((sym, "RSI EXTREME[" + tf + "]", "RSI=" + str(rsi) + " oversold"))
        if mc == "BULL_CROSS":
            alerts.append((sym, C.GREEN + "MACD CROSS↑" + C.RESET + "[" + tf + "]", "Bullish MACD crossover"))
        if mc == "BEAR_CROSS":
            alerts.append((sym, C.RED   + "MACD CROSS↓" + C.RESET + "[" + tf + "]", "Bearish MACD crossover"))
        if std == 1 and e.get("st_direction_prev", 0) == -1:
            alerts.append((sym, C.GREEN + "ST FLIP↑   " + C.RESET + "[" + tf + "]", "SuperTrend turned bullish"))
        if std == -1 and e.get("st_direction_prev", 0) == 1:
            alerts.append((sym, C.RED   + "ST FLIP↓   " + C.RESET + "[" + tf + "]", "SuperTrend turned bearish"))

    return alerts


# ─────────────────────────────────────────────────────────────
#  DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────

def bar(value, width=10, color=""):
    v = max(0, min(100, int(safe_float(value))))
    f = int(v / 100 * width)
    filled = "█" * f
    empty  = "░" * (width - f)
    if color:
        return color + filled + C.DIM + empty + C.RESET
    return filled + empty

def trend_bar(strength, width=10):
    """±100 strength -> colored bar."""
    strength = safe_float(strength, 0.0)     # never let NaN reach int()
    if strength >= 0:
        f = int((strength / 100) * width)
        return C.GREEN + "█" * f + C.DIM + "░" * (width - f) + C.RESET
    else:
        f = int((-strength / 100) * width)
        return C.RED + "█" * f + C.DIM + "░" * (width - f) + C.RESET

def sig_str(sig):
    col = signal_color(sig)
    short = SIGNAL_SHORT.get(sig, "--")
    return col + C.BOLD + short + C.RESET


def fmt_price(value, dash="—"):
    """Format a price for display without ever raising.

    Bug fix: every view did `"₹" + str(int(price)) if price else "—"`, which
    crashes on a NaN or non-numeric price (ValueError: cannot convert float NaN
    to integer) — reachable via a corrupt/stale DB row or a null field in an
    API response.  Non-finite and non-positive values render as the dash.
    """
    v = safe_float(value, 0.0)
    if not math.isfinite(v) or v <= 0:
        return dash
    return "₹" + str(int(v))


def fmt_pct(value, dash="0.0%"):
    """Format a percentage without ever raising (see fmt_price)."""
    v = safe_float(value, 0.0)
    if not math.isfinite(v):
        return dash
    return "{:.1f}%".format(v)

def clr():
    os.system("cls" if os.name == "nt" else "clear")

def div(c="─", w=W):
    print(C.DIM + c * w + C.RESET)

def header(title="MASTER SCANNER v8.0 PRO"):
    clr()
    print()
    cprint("═" * W, C.CYAN)
    cprint("  " + title, C.WHITE, bold=True)
    cprint("  " + datetime.now(IST).strftime("%A %d %b %Y  │  %H:%M:%S IST"), C.DIM)
    cprint("═" * W, C.CYAN)
    print()


# ─────────────────────────────────────────────────────────────
#  CORE: SCAN A SINGLE INSTRUMENT KEY / SYMBOL
#  All scan paths (auto_scan, scan_single_symbol) funnel here.
# ─────────────────────────────────────────────────────────────

def _scan_one_symbol(inst_key, sym, data, use_tfs, hdrs):
    """
    Download candles, compute indicators, detect signals, and store
    results for ONE symbol across the given timeframes.
    Returns the (mutated) data dict.
    """
    ensure_symbol(data, sym)
    cprint("  [" + sym + "]  " + SECTOR_MAP.get(sym, ""), C.WHITE, bold=True)

    # Bug fix: load_note called once per symbol, not once per TF.
    # Previously load_note(sym) was called inside the TF loop → 6 DB queries
    # per symbol (one per TF) even though the note never changes mid-scan.
    note = load_note(sym)

    for tf in use_tfs:
        if tf not in TF_CONFIG:
            continue

        unit, value, lookback = TF_CONFIG[tf]
        resample_tag = (" (daily→" + RESAMPLE_TFS[tf] + ")"
                        if tf in RESAMPLE_TFS else
                        " (" + value + " " + unit + ", " + str(lookback) + "d lookback)")
        print("    " + C.YELLOW + tf + C.RESET + resample_tag)

        df = fetch_candles(inst_key, unit, value, lookback, hdrs, verbose=True, tf_name=tf)

        if df.empty:
            cprint("    ✗ " + tf + ": Skipping — no usable data. "
                   "Check token, instrument key, and API connectivity.", C.RED)
            continue

        df = calculate_indicators(df, tf)
        signal, liq, vol, oi, reason, comps, _ = detect_signal(df, tf)
        support, resistance = calc_support_resistance(df, lookback=_SR_LOOKBACK.get(tf, 30))
        targets     = get_price_targets(df, signal, tf)
        strength    = trend_strength(comps)

        # ── PRO: extra analytics ──────────────────────────────
        candle_pats = detect_candlestick_patterns(df)
        rsi_div     = calc_rsi_divergence(df, tf=tf)
        fib_levels  = calc_fibonacci_levels(df, lookback=_FIBO_LOOKBACK.get(tf, 50))
        # 52W: DAY uses 252-bar window (trading days), WEEK uses 52-bar window
        # (calendar weeks). Using 252 on weekly bars looks back ~4.8 years —
        # wrong. MONTH skipped: 252 monthly bars = 21 years, meaningless.
        if tf in TF_52W_WINDOW:
            w52 = calc_52w_levels(df, window_bars=TF_52W_WINDOW[tf])
        else:
            w52 = {}

        last     = df.iloc[-1]
        prev     = df.iloc[-2] if len(df) > 1 else last
        rsi_val  = round(safe_float(last.get("rsi",      0)), 1)
        rv_val   = round(safe_float(last.get("rel_vol",  0)), 2)
        price    = round(safe_float(last.get("close",    0)), 2)
        atr_val  = round(safe_float(last.get("atr",      0)), 2)
        macd_h   = safe_float(last.get("macd_hist",      0))
        p_macd_h = safe_float(prev.get("macd_hist",      0))
        st_dir   = int(safe_float(last.get("st_dir",     0)))
        p_st_dir = int(safe_float(prev.get("st_dir",     0)))
        adx_val  = round(safe_float(last.get("adx",      0)), 1)
        plus_di  = round(safe_float(last.get("plus_di",  0)), 1)
        minus_di = round(safe_float(last.get("minus_di", 0)), 1)
        wr_val   = round(safe_float(last.get("williams_r", -50)), 1)
        cci_val  = round(safe_float(last.get("cci",       0)), 1)
        mfi_val  = round(safe_float(last.get("mfi",      50)), 1)

        # ── Candle date: what period the DATA represents ──────
        # Intraday → HH:MM  |  DAY → date  |  WEEK → "Wk YYYY-MM-DD"  |  MONTH → "Mo YYYY-MM"
        try:
            ts_raw = last["ts"]
            if tf in TF_INTRADAY_V3:
                candle_date = pd.Timestamp(ts_raw).strftime("%Y-%m-%d %H:%M")
            elif tf == "WEEK":
                candle_date = "Wk " + pd.Timestamp(ts_raw).strftime("%Y-%m-%d")
            elif tf == "MONTH":
                candle_date = "Mo " + pd.Timestamp(ts_raw).strftime("%Y-%m")
            else:
                candle_date = pd.Timestamp(ts_raw).strftime("%Y-%m-%d")
        except Exception:
            candle_date = datetime.now(IST).strftime("%Y-%m-%d %H:%M")

        macd_cross = ""
        if   macd_h > 0 and p_macd_h <= 0: macd_cross = "BULL_CROSS"
        elif macd_h < 0 and p_macd_h >= 0: macd_cross = "BEAR_CROSS"

        # Risk:Reward ratio
        rr = 0.0
        if targets and atr_val > 0:
            t1   = targets.get("target1", price)
            sl   = targets.get("stop",    price)
            risk = abs(price - sl)
            rew  = abs(t1 - price)
            rr   = round(rew / risk, 1) if risk > 0 else 0.0

        # Signal change detection
        prev_sig    = as_dict(data[sym][tf]).get("signal", "NONE")
        sig_changed = (prev_sig != signal and prev_sig != "NONE")

        entry = {
            "signal":            signal,
            "atr_pct":           liq,
            "volume":            vol,
            "oi":                oi,
            "rsi":               rsi_val,
            "rel_vol":           rv_val,
            "reason":            reason,
            "note":              note,
            # OHLC of the last candle. Needed by calc_next_day_gap_score to
            # measure where the close sits inside the period's range; the
            # stored entry previously carried "price" only, so that factor
            # silently fell back to trend_strength and double-counted it.
            "day_open":          round(safe_float(last.get("open",  0)), 2),
            "day_high":          round(safe_float(last.get("high",  0)), 2),
            "day_low":           round(safe_float(last.get("low",   0)), 2),
            "updated":           datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            "candle_date":       candle_date,   # actual date of last candle's data
            "price":             price,
            "atr":               atr_val,
            "trend_strength":    strength,
            "targets":           targets,
            "support":           support,
            "resistance":        resistance,
            "macd_cross":        macd_cross,
            "st_direction":      st_dir,
            "st_direction_prev": p_st_dir,
            "ema_alignment":    (ema_alignment_label(comps)
                                 .replace(C.GREEN,"").replace(C.RED,"")
                                 .replace(C.YELLOW,"").replace(C.RESET,"").replace(C.BOLD,"")
                                 .strip()),
            # PRO fields
            "candle_patterns":   candle_pats,
            "rsi_divergence":    rsi_div,
            "fibonacci":         fib_levels,
            "52w":               w52,
            "risk_reward":       rr,
            "prev_signal":       prev_sig,
            "signal_changed":    sig_changed,
            "composite_score":   0,   # computed after entry stored
            # Retest breakout fields (from detect_signal components)
            "retest_bo":         comps.get("retest_bo",       False),
            "retest_bd":         comps.get("retest_bd",       False),
            "retest_bo_level":   comps.get("retest_bo_level", 0.0),
            "retest_bd_level":   comps.get("retest_bd_level", 0.0),
            # Gap: computed for DAY tf only (today's open vs yesterday's close)
            "gap":               calc_gap(df) if tf == "DAY" else {},
            # MACD histogram value (actual float, not just cross direction)
            # Stored so momentum_screener can use real MACD values instead of proxy.
            "macd_hist_val":     round(safe_float(last.get("macd_hist", 0)), 4),
            # New indicators (v8.0)
            "adx":               adx_val,
            "plus_di":           plus_di,
            "minus_di":          minus_di,
            "williams_r":        wr_val,
            "cci":               cci_val,
            "mfi":               mfi_val,
        }

        data[sym][tf] = entry
        # Bug fix: pass target_tf=tf so the score uses THIS TF's own RSI/vol/trend,
        # not always the first TF in the list. Previously all TFs got identical scores.
        # NOTE: score computed here has incomplete confluence (later TFs not yet scanned).
        # The post-scan recalculation below overwrites this with the correct final score.
        # log_history is intentionally deferred to after the loop — see below.

        col     = signal_color(signal)
        chg_tag = (C.YELLOW + " ⚡CHANGED" + C.RESET) if sig_changed else ""
        candle_info = ""
        if tf in RESAMPLE_TFS:
            candle_info = ("  [" + str(len(df)) + " " + tf.lower()
                           + " bars, period: " + candle_date + "]")
        print("    => " +
              col + C.BOLD + "{:<10}".format(signal) + C.RESET +
              chg_tag +
              "  ATR%:" + str(liq) + "%" +
              "  Vol:"  + str(vol) + "%" +
              "  RSI:"  + str(rsi_val) +
              "  R:R "  + str(rr) +
              "  Score:--" +   # Bug fix: score=0 here (recalculated after all TFs scanned)
              "  ₹"     + str(price) +
              C.DIM + candle_info + C.RESET)
        if targets:
            t1 = targets.get("target1", "—")
            t2 = targets.get("target2", "—")
            sl = targets.get("stop",    "—")
            print("       T1: ₹" + str(t1) + "  T2: ₹" + str(t2) + "  SL: ₹" + str(sl))
        if candle_pats:
            print("       Candle: " + C.CYAN + " | ".join(candle_pats) + C.RESET)
        if rsi_div.get("regular_bull"): print("       " + C.GREEN + "RSI Regular Bull Divergence" + C.RESET)
        if rsi_div.get("regular_bear"): print("       " + C.RED   + "RSI Regular Bear Divergence" + C.RESET)
        if rsi_div.get("hidden_bull"):  print("       " + C.GREEN + "RSI Hidden Bull Divergence"  + C.RESET)
        if rsi_div.get("hidden_bear"):  print("       " + C.RED   + "RSI Hidden Bear Divergence"  + C.RESET)
        if comps.get("retest_bo"):
            print("       " + C.GREEN + C.BOLD + "★ RETEST BREAKOUT  — R1=₹" +
                  str(comps.get("retest_bo_level", "")) + "  (break → pullback → confirmed BO)" + C.RESET)
        if comps.get("retest_bd"):
            print("       " + C.RED   + C.BOLD + "★ RETEST BREAKDOWN — S1=₹" +
                  str(comps.get("retest_bd_level", "")) + "  (break → bounce → confirmed BD)" + C.RESET)
        if w52:
            print("       52W H: ₹{} ({:+.1f}%)  52W L: ₹{} ({:+.1f}%)".format(
                w52.get("high_52w","—"), w52.get("pct_from_high",0),
                w52.get("low_52w","—"),  w52.get("pct_from_low",0)))

    # ── Post-loop: recalculate composite scores with FULL confluence ──
    # Scores computed inside the TF loop above have incomplete confluence:
    # when 5MIN is scored, 15MIN/1HR/DAY/WEEK/MONTH haven't been scanned yet
    # so conf_raw=1 instead of 6. Now all TFs are in data[sym] — recompute
    # with the full picture. Also log_history here so it records the FINAL
    # accurate score, not the intermediate one from inside the loop.
    for tf in use_tfs:
        if tf not in data[sym]:
            continue
        e = as_dict(data[sym][tf])
        # NONE signals are intentionally skipped here:
        # - Composite score is meaningless with no directional signal (conf=0, RSI pts=0)
        # - History table is kept signal-only so the history view stays actionable
        # NOTE: if a symbol flips FROM a signal TO NONE, signal_changed=True is
        # recorded in data[sym][tf] in memory and shown in the dashboard ⚡ marker,
        # but the flip itself is NOT written to the history table by design.
        if e.get("signal", "NONE") == "NONE":
            continue
        final_score = calc_composite_score(data[sym], use_tfs, target_tf=tf)
        data[sym][tf]["composite_score"] = final_score
        log_history(sym, tf, data[sym][tf])

    print()
    return data


# ─────────────────────────────────────────────────────────────
#  AUTO SCAN  (all symbols)
# ─────────────────────────────────────────────────────────────

def auto_scan(data, selected_tfs=None):
    header("AUTO SCAN  ─  Downloading Candles + Detecting Signals")

    token = load_token()
    if not token:
        input("  Press ENTER to continue...")
        return data

    hdrs    = make_headers(token)
    use_tfs = selected_tfs or TIMEFRAMES_DEFAULT

    cprint("  Symbols   : " + str(len(SYMBOL_MAP)), C.CYAN)
    cprint("  Timeframes: " + ", ".join(use_tfs),   C.CYAN)
    print()

    start_scan_pass()
    try:
        for inst_key, sym in SYMBOL_MAP.items():
            _t0  = time.time()
            data = _scan_one_symbol(inst_key, sym, data, use_tfs, hdrs)
            # Adaptive throttle: ensure at least 1 s between symbols to respect
            # Upstox rate limits, but don't sleep longer than necessary.
            elapsed = time.time() - _t0
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
    except TokenError as exc:
        # A dead token fails identically for every remaining symbol, so
        # continuing only burns the rate budget and buries the real cause.
        cprint("\n  ✗ SCAN ABORTED: " + str(exc), C.RED, bold=True)
        cprint("  No further symbols were scanned.", C.RED)
        save_data(data)
        input("  Press ENTER to continue...")
        return data
    except KeyboardInterrupt:
        cprint("\n  Scan interrupted — saving what we have…", C.YELLOW)
        save_data(data)
        input("  Press ENTER to continue...")
        return data

    save_data(data)
    cprint("  ✓ Scan complete. Saved to " + DATA_FILE, C.GREEN)
    input("  Press ENTER to continue...")
    return data


# ─────────────────────────────────────────────────────────────
#  INDIVIDUAL SYMBOL SCAN
# ─────────────────────────────────────────────────────────────

def scan_single_symbol(data, selected_tfs=None):
    """
    Scan ONE user-chosen symbol across selected timeframes.
    Shows autocomplete list of known symbols for convenience.
    After scanning, immediately drops into view_detail so the
    trader can see the full analysis without extra keypresses.
    """
    header("INDIVIDUAL SYMBOL SCAN")

    # ── Show available symbols ────────────────────────────────
    all_syms = sorted(SYMBOL_MAP.values())
    cols = 6
    cprint("  Available symbols:", C.DIM)
    for i in range(0, len(all_syms), cols):
        row = all_syms[i:i + cols]
        print("    " + "  ".join(_ljust(s, 12) for s in row))
    print()

    sym = input("  Enter symbol (e.g. TCS): ").strip().upper()
    if not sym:
        cprint("  Cancelled.", C.YELLOW)
        input("  Press ENTER...")
        return data

    # Find the instrument key for this symbol
    inst_key = next((k for k, v in SYMBOL_MAP.items() if v == sym), None)
    if not inst_key:
        cprint("  ✗ Symbol not found: " + sym, C.RED)
        cprint("  Tip: use [A] to add a custom symbol first.", C.DIM)
        input("  Press ENTER...")
        return data

    # ── TF selection ──────────────────────────────────────────
    use_tfs = selected_tfs or ACTIVE_TFS
    cprint("  Current active TFs: " + ", ".join(use_tfs), C.DIM)
    cprint("  Available         : " + ", ".join(TF_CONFIG.keys()), C.DIM)
    raw = input("  Override TFs (ENTER to keep current): ").strip().upper()
    if raw:
        parsed = [t.strip() for t in raw.split(",") if t.strip() in TF_CONFIG]
        if parsed:
            use_tfs = parsed
        else:
            cprint("  Invalid TF(s) — keeping current.", C.YELLOW)

    # ── Load token & scan ─────────────────────────────────────
    token = load_token()
    if not token:
        input("  Press ENTER to continue...")
        return data

    hdrs = make_headers(token)
    cprint("\n  Scanning " + sym + " on: " + ", ".join(use_tfs) + "\n", C.CYAN)

    start_scan_pass()
    try:
        data = _scan_one_symbol(inst_key, sym, data, use_tfs, hdrs)
    except TokenError as exc:
        cprint("\n  ✗ SCAN ABORTED: " + str(exc), C.RED, bold=True)
        input("  Press ENTER to continue...")
        return data

    save_data(data)
    cprint("  ✓ Done. Saved to " + DATA_FILE, C.GREEN)

    # ── Auto-show full detail immediately ─────────────────────
    see = input("\n  Show full detail for " + sym + "? [Y/n]: ").strip().upper()
    if see != "N":
        view_detail(sym, data)

    input("  Press ENTER...")
    return data


# ─────────────────────────────────────────────────────────────
#  VIEWS
# ─────────────────────────────────────────────────────────────

def _score_tf_key(entry, tfs):
    """Best TF for scalar metrics — picks the highest-priority TF that
    is actually present in entry AND has valid price data.

    Bug fix: previously returned "DAY" if "DAY" in entry, but "DAY" can
    exist as a key with an empty/zero entry (e.g. symbol scanned on WEEK+MONTH
    only). This caused best_setups_view, candle_pattern_view, heatmap_view etc.
    to get {} back from entry.get(tf_key, {}) and display zeros for all metrics.

    Priority: DAY → WEEK → MONTH → 1HR → 15MIN → 5MIN → first key in tfs.
    """
    preferred = ["DAY", "WEEK", "MONTH", "1HR", "15MIN", "5MIN"]
    for tf in preferred:
        if tf in entry and safe_float(as_dict(entry[tf]).get("price", 0)):
            return tf
    # Fallback: first tf in tfs that exists in entry (even if price=0)
    for tf in tfs:
        if tf in entry:
            return tf
    return tfs[0] if tfs else "DAY"


def dashboard(data, tfs=None):
    tfs = tfs or TIMEFRAMES_SWING
    header()
    if not data:
        cprint("  No data yet. Run AUTO SCAN (S) to download candles.\n", C.YELLOW)
        return

    total = len(data)
    bo = bd = sw = no = 0
    for e in data.values():
        for tf in tfs:
            s = as_dict(e.get(tf)).get("signal", "NONE")
            if s == "BREAKOUT":  bo += 1
            if s == "BREAKDOWN": bd += 1
            if s == "SIDEWAYS":  sw += 1
            if s == "NONE":      no += 1

    print("  " +
          C.CYAN + "Symbols: " + str(total) + C.RESET + "  │  " +
          C.GREEN + "BO: " + str(bo) + C.RESET + "  │  " +
          C.RED   + "BD: " + str(bd) + C.RESET + "  │  " +
          C.YELLOW+ "SW: " + str(sw) + C.RESET + "  │  " +
          C.DIM   + "NO: " + str(no) + C.RESET +
          C.DIM   + "  (TF-signal counts across " + str(len(tfs)) + " TFs)" + C.RESET)
    print("  TFs: " + ", ".join(tfs))
    print()

    # ── Column widths (visible chars) ────────────────────────
    # Bug fix: previously hardcoded to exactly 3 TF columns (_C[3..5]).
    # When ACTIVE_TFS has 4+ entries, signals beyond index 2 were built
    # but never printed — silently invisible to the user.
    # Now supports up to 5 TF columns dynamically.
    MAX_TF_COLS = 5
    tf_show = tfs[:MAX_TF_COLS]
    n_tf    = len(tf_show)

    # Fixed column widths: ★ SYM SEC [TFx...] VOL ATR% PRICE SCR TREND CONF
    _CW = {"star": 2, "sym": 12, "sec": 7, "tf": 5, "vol": 4,
           "atr": 6, "price": 9, "scr": 5, "trend": 8}

    wl = load_watchlist()

    hdr = ("  " +
           _ljust("★",       _CW["star"]) + " " +
           _ljust("SYMBOL",  _CW["sym"])  + "  " +
           _ljust("SECTOR",  _CW["sec"])  + "  " +
           "  ".join(_rjust(t[:4], _CW["tf"]) for t in tf_show) + "  " +
           _rjust("VOL",     _CW["vol"])   + "  " +
           _rjust("ATR%",    _CW["atr"])   + "  " +
           _rjust("PRICE",   _CW["price"]) + "  " +
           _rjust("SCR",     _CW["scr"])   + "  " +
           _ljust("TREND",   _CW["trend"]) + "  " +
           "CONFLUENCE")
    cprint(hdr, C.DIM)
    div()

    # Sort by composite score descending
    ranked = sorted(data.items(),
                    key=lambda x: x[1].get(_score_tf_key(x[1], tfs), {}).get("composite_score", 0),
                    reverse=True)

    for sym, entry in ranked:
        signals = []
        for tf in tf_show:
            s  = as_dict(entry.get(tf)).get("signal", "NONE")
            sc = as_dict(entry.get(tf)).get("signal_changed", False)
            tag = sig_str(s) + (C.YELLOW + "⚡" + C.RESET if sc else "")
            signals.append(tag)

        _stf   = _score_tf_key(entry, tfs)
        # Coerce: a stale/corrupt row can hold a non-numeric value, and a bare
        # `cscore >= 65` comparison then raises TypeError mid-render.
        vol    = int(safe_float(as_dict(entry.get(_stf)).get("volume",          0)))
        atr_p  = safe_float(as_dict(entry.get(_stf)).get("atr_pct",         0))
        price  = safe_float(as_dict(entry.get(_stf)).get("price",           0))
        str_v  = safe_float(as_dict(entry.get(_stf)).get("trend_strength",  0))
        cscore = safe_float(as_dict(entry.get(_stf)).get("composite_score", 0))
        score  = confluence_score(entry, tfs)
        conf   = confluence_label(score)
        sector = SECTOR_MAP.get(sym, "")[:7]

        star      = (C.YELLOW + "★" + C.RESET) if sym in wl else " "
        price_str = fmt_price(price)
        atr_str   = fmt_pct(atr_p)
        scr_col   = C.GREEN if cscore >= 65 else (C.YELLOW if cscore >= 40 else C.DIM)
        scr_str   = scr_col + str(cscore) + C.RESET

        print("  " +
              _ljust(star,   _CW["star"]) + " " +
              _ljust(sym,    _CW["sym"])  + "  " +
              _ljust(sector, _CW["sec"])  + "  " +
              "  ".join(_rjust(signals[i], _CW["tf"]) for i in range(n_tf)) + "  " +
              _rjust(str(vol) + "%", _CW["vol"])   + "  " +
              _rjust(atr_str,        _CW["atr"])   + "  " +
              _rjust(price_str,      _CW["price"]) + "  " +
              _rjust(scr_str,        _CW["scr"])   + "  " +
              _ljust(trend_bar(str_v, 6), _CW["trend"]) + "  " +
              conf)

    div()
    print()


def view_detail(sym, data):
    """
    ONE-TAP FULL DETAIL VIEW
    Everything about a symbol on a single screen — no sub-menus needed.
    Sections:
      1. Header — symbol, sector, price, watchlist, confluence
      2. Signal summary strip — all TFs at a glance
      3. Next-day gap prediction (inline)
      4. Per-TF full detail block — signal, reason, indicators, targets
      5. Gap data (DAY only)
      6. Volume profile mini-summary (POC / HVN / LVN from stored data)
      7. Fibonacci levels
      8. 52W High / Low
      9. Candlestick patterns
     10. RSI divergence
     11. Retest breakout/breakdown
     12. Recent history (last 5 entries inline)
     13. Trade plan recommendation
    """
    wl       = load_watchlist()
    sym_data = data[sym]
    # Coerce every TF entry to a dict. Rows come from a JSON blob in SQLite,
    # and a stale or hand-edited one can hold a scalar — sym_data[tf].get(...)
    # then raised AttributeError and killed the whole detail screen.
    sym_data = {k: (as_dict(v) if k in TF_CONFIG else v)
                for k, v in sym_data.items()}
    sector   = SECTOR_MAP.get(sym, "")
    star_str = (C.YELLOW + " ★ WATCHLIST" + C.RESET) if sym in wl else ""
    # Bug fix: was hardcoded TIMEFRAMES_SWING — intraday BREAKDOWN signals
    # (5MIN/15MIN/1HR) were invisible to confluence, producing STRONG BULL [+++]
    # even when all intraday TFs showed BREAKDOWN.
    # Fix: compute confluence over every TF that was actually scanned
    # (has a non-NONE signal), giving an honest all-TF picture.
    scanned_tfs = [tf for tf in list(TF_CONFIG.keys())
                   if tf in sym_data and sym_data[tf].get("signal", "NONE") != "NONE"]
    conf_tfs = scanned_tfs if scanned_tfs else TIMEFRAMES_SWING
    conf     = confluence_score(sym_data, conf_tfs)
    conf_lbl = confluence_label(conf)
    day_e    = as_dict(sym_data.get("DAY"))
    price    = day_e.get("price", 0)

    header("◉ " + sym + "  [" + sector + "]" + star_str)

    # ══ 1. QUICK SUMMARY BAR ═════════════════════════════════════
    div("═")
    cprint("  SIGNAL SNAPSHOT", C.WHITE, bold=True)
    div("─")
    tf_order = [tf for tf in list(TF_CONFIG.keys()) if tf in sym_data]
    for tf in tf_order:
        e   = as_dict(sym_data[tf])
        sig = e.get("signal", "NONE")
        col = signal_color(sig)
        chg = C.YELLOW + " ⚡CHANGED" + C.RESET if e.get("signal_changed") else ""
        cs  = safe_float(e.get("composite_score", 0))
        sc_col = C.GREEN if cs >= 65 else (C.YELLOW if cs >= 40 else C.DIM)
        rsi = safe_float(e.get("rsi", 0))
        rv  = safe_float(e.get("rel_vol", 0))
        p   = safe_float(e.get("price", price))
        print("  " +
              C.BOLD + _ljust(tf, 6) + C.RESET + "  " +
              col + C.BOLD + _ljust(SIGNAL_LABEL.get(sig,"—"), 16) + C.RESET +
              chg +
              "  Score:" + sc_col + str(cs) + C.RESET +
              "  RSI:" + str(rsi) +
              "  RVol:" + str(rv) +
              "  ₹" + str(p) +
              C.DIM + "  [" + e.get("candle_date","—") + "]" + C.RESET)
    div("─")
    print("  Confluence : " + str(conf) + "  " + conf_lbl +
          "    Status: " + conflict_status(sym_data, conf_tfs))
    div("═")
    print()

    # ══ 2. NEXT DAY GAP PREDICTION ═══════════════════════════════
    gap_score, gap_bias, gap_facts = calc_next_day_gap_score(sym_data)
    if   gap_bias == "GAP_UP":   gb_col = C.GREEN;  gb_str = "▲ GAP UP LIKELY"
    elif gap_bias == "GAP_DOWN": gb_col = C.RED;    gb_str = "▼ GAP DOWN LIKELY"
    else:                        gb_col = C.YELLOW;  gb_str = "— NEUTRAL / WAIT"
    bar_len = 22
    filled  = int(safe_float(gap_score) / 100 * bar_len)
    bar_col = C.GREEN if gap_score >= 62 else (C.RED if gap_score <= 38 else C.YELLOW)
    bar_str = "[" + bar_col + "█" * filled + C.DIM + "░" * (bar_len - filled) + C.RESET + "]"
    cprint("  NEXT DAY GAP PREDICTION", C.WHITE, bold=True)
    div("─")
    print("  Score  : " + gb_col + C.BOLD + str(gap_score) + "%" + C.RESET +
          "  " + bar_str +
          "  " + gb_col + C.BOLD + gb_str + C.RESET)
    # Top 3 factors
    sorted_facts = sorted(gap_facts, key=lambda x: abs(x[1]), reverse=True)[:3]
    for lbl, pts in sorted_facts:
        col = C.GREEN if pts > 0 else (C.RED if pts < 0 else C.DIM)
        print("  " + col + ("+" if pts >= 0 else "") + str(pts) + "pts" + C.RESET +
              "  " + lbl)
    div("═")
    print()

    # ══ 3. TODAY'S GAP (DAY only) ════════════════════════════════
    gap_d = as_dict(day_e.get("gap"))
    if gap_d and gap_d.get("gap_type","") != "NO_GAP":
        gt  = gap_d["gap_type"]
        gc  = C.GREEN if gt == "GAP_UP" else C.RED
        cprint("  TODAY'S GAP", C.WHITE, bold=True)
        div("─")
        filled_str = C.YELLOW + "FILLED" + C.RESET if gap_d.get("gap_filled") else gc + "OPEN" + C.RESET
        print("  Type   : " + gc + C.BOLD + gt + C.RESET +
              "  " + "{:+.2f}%".format(gap_d["gap_pct"]) +
              "  ₹" + str(gap_d["gap_rs"]) +
              "  Status: " + filled_str)
        print("  Prev Close : ₹" + str(gap_d["prev_close"]) +
              "  Today Open : ₹" + str(gap_d["today_open"]))
        div("═")
        print()

    # ══ 4. PER-TF DETAIL BLOCKS ══════════════════════════════════
    cprint("  TIMEFRAME ANALYSIS", C.WHITE, bold=True)
    print()
    for tf in tf_order:
        e   = as_dict(sym_data[tf])
        sig = e.get("signal", "NONE")
        col = signal_color(sig)

        print(col + C.BOLD + "  ┌── " + tf + " " + "─" * (W - 8 - len(tf)) + C.RESET)
        print("  │  Signal      : " + col + C.BOLD + SIGNAL_LABEL.get(sig, "—") + C.RESET +
              C.DIM + "  [" + e.get("reason","—") + "]" + C.RESET)
        print("  │  Price/ATR   : ₹" + str(e.get("price","—")) +
              "  ATR: ₹" + str(e.get("atr","—")) +
              "  ATR%: " + str(e.get("atr_pct",0)) + "%")
        print("  │  Volume      : [" + bar(e.get("volume",0), color=C.BLUE) + "] " +
              str(e.get("volume",0)) + "%  RelVol: " + str(e.get("rel_vol","—")))
        rsi_v   = safe_float(e.get("rsi", 0))
        rsi_col = C.RED if rsi_v > 75 else C.GREEN if rsi_v > 55 else C.YELLOW if rsi_v > 40 else C.RED
        print("  │  RSI(14)     : " + rsi_col + str(rsi_v) + C.RESET +
              "  Trend: " + trend_bar(e.get("trend_strength", 0), 10) +
              " " + str(e.get("trend_strength", 0)))
        print("  │  EMA Align   : " + str(e.get("ema_alignment","—")))

        mc = e.get("macd_cross","")
        st = e.get("st_direction", 0)
        row2 = ""
        if mc:
            mc_col = C.GREEN if mc == "BULL_CROSS" else C.RED
            row2 += "  MACD: " + mc_col + mc + C.RESET
        if st:
            st_col = C.GREEN if st == 1 else C.RED
            row2 += "  SuperTrend: " + st_col + ("BULL↑" if st==1 else "BEAR↓") + C.RESET
        if row2:
            print("  │  " + row2)   # Bug fix: was "│ " (1 space), should be "│  " (2 spaces)

        # New indicator row: ADX, Williams %R, MFI, CCI
        adx_v = safe_float(e.get("adx",        0))
        pdi   = safe_float(e.get("plus_di",    0))
        mdi   = safe_float(e.get("minus_di",   0))
        wr_v  = safe_float(e.get("williams_r", -50))
        mfi_v = safe_float(e.get("mfi",        50))
        cci_v = safe_float(e.get("cci",        0))
        adx_col = C.GREEN if adx_v > 40 else (C.YELLOW if adx_v > 25 else C.DIM)
        adx_lbl = "STRONG" if adx_v > 40 else ("TRENDING" if adx_v > 25 else "RANGING")
        wr_col  = C.GREEN if wr_v > -30 else (C.YELLOW if wr_v > -50 else (C.RED if wr_v < -70 else C.DIM))
        mfi_col = C.GREEN if mfi_v > 60 else (C.RED if mfi_v < 40 else C.YELLOW)
        cci_col = C.GREEN if cci_v > 100 else (C.RED if cci_v < -100 else C.YELLOW)
        print("  │  ADX/Momen  : " +
              adx_col + "ADX=" + str(adx_v) + " " + adx_lbl + C.RESET +
              "  +DI=" + str(pdi) + "/-DI=" + str(mdi) +
              "  " + wr_col + "W%R=" + str(wr_v) + C.RESET +
              "  " + mfi_col + "MFI=" + str(mfi_v) + C.RESET +
              "  " + cci_col + "CCI=" + str(cci_v) + C.RESET)

        cs = safe_float(e.get("composite_score", 0))
        rr = safe_float(e.get("risk_reward", 0))
        sc_col = C.GREEN if cs >= 65 else (C.YELLOW if cs >= 40 else C.DIM)
        rr_col = C.GREEN if rr >= 2  else (C.YELLOW if rr >= 1.5 else C.RED)
        print("  │  Comp.Score  : " + sc_col + str(cs) + "/100" + C.RESET +
              "  R:R: " + rr_col + str(rr) + ":1" + C.RESET)

        tgt = as_dict(e.get("targets"))
        if tgt:
            print("  │  Targets     : " + C.GREEN +
                  "T1=₹" + str(tgt.get("target1","—")) +
                  "  T2=₹" + str(tgt.get("target2","—")) + C.RESET +
                  "  " + C.RED + "SL=₹" + str(tgt.get("stop","—")) + C.RESET)

        supp = as_list(e.get("support"))
        res  = as_list(e.get("resistance"))
        if supp: print("  │  Support     : " + C.GREEN +
                       "  ".join("₹" + str(s) for s in supp) + C.RESET)
        if res:  print("  │  Resistance  : " + C.RED +
                       "  ".join("₹" + str(r) for r in res)  + C.RESET)

        cps = as_list(e.get("candle_patterns"))
        if cps:
            bull_cp = [p for p in cps if p in CANDLE_BULL]
            bear_cp = [p for p in cps if p in CANDLE_BEAR]
            if bull_cp: print("  │  Candle ↑    : " + C.GREEN + " | ".join(bull_cp) + C.RESET)
            if bear_cp: print("  │  Candle ↓    : " + C.RED   + " | ".join(bear_cp) + C.RESET)

        div_ = as_dict(e.get("rsi_divergence"))
        divs = []
        if div_.get("regular_bull"): divs.append(C.GREEN + "Reg.Bull▲" + C.RESET)
        if div_.get("hidden_bull"):  divs.append(C.GREEN + "Hid.Bull▲" + C.RESET)
        if div_.get("regular_bear"): divs.append(C.RED   + "Reg.Bear▼" + C.RESET)
        if div_.get("hidden_bear"):  divs.append(C.RED   + "Hid.Bear▼" + C.RESET)
        if divs: print("  │  RSI Div     : " + "  ".join(divs))

        if e.get("retest_bo"):
            print("  │  " + C.GREEN + C.BOLD + "★ RETEST BREAKOUT  R1=₹" +
                  str(e.get("retest_bo_level",0)) + C.RESET)
        if e.get("retest_bd"):
            print("  │  " + C.RED + C.BOLD + "★ RETEST BREAKDOWN S1=₹" +
                  str(e.get("retest_bd_level",0)) + C.RESET)

        fib = as_dict(e.get("fibonacci"))
        if fib:
            sw_h = fib.get("sw_high",0); sw_l = fib.get("sw_low",0)
            print("  │  Fib Swing   : H ₹" + str(sw_h) + " — L ₹" + str(sw_l))
            fib_pairs = [("38.2%", fib.get("fib_38.2","")),
                         ("50.0%", fib.get("fib_50","")),
                         ("61.8%", fib.get("fib_61.8",""))]
            fib_line = "  │  Fib Key Lvl : "
            p_now = e.get("price", 0)
            for lbl, lvl in fib_pairs:
                if not lvl: continue
                near = p_now and abs(float(lvl) - p_now) / max(p_now,0.01) < 0.025
                fib_line += (C.YELLOW if near else C.DIM) + lbl + "=₹" + str(lvl) + C.RESET + "  "
            print(fib_line)

        w52 = as_dict(e.get("52w"))
        if w52:
            ph = w52.get("pct_from_high",0); pl = w52.get("pct_from_low",0)
            ph_col = C.GREEN if ph >= -5 else (C.YELLOW if ph >= -15 else C.RED)
            pl_col = C.GREEN if pl >= 50 else (C.YELLOW if pl >= 20  else C.DIM)
            print("  │  52W          : High " + ph_col + "₹" + str(w52.get("high_52w","—")) +
                  " (" + str(ph) + "%)" + C.RESET +
                  "  Low " + pl_col + "₹" + str(w52.get("low_52w","—")) +
                  " (+" + str(pl) + "%)" + C.RESET)

        if e.get("signal_changed"):
            print("  │  " + C.YELLOW + C.BOLD + "⚡ Signal changed from " +
                  e.get("prev_signal","—") + C.RESET)
        if e.get("note"):
            print("  │  Note         : " + C.YELLOW + e["note"] + C.RESET)
        print("  │  Last update  : " + C.DIM + (e.get("updated") or "never") + C.RESET)
        print("  └" + "─" * (W - 3))
        print()

    # ══ 5. RECENT HISTORY (last 5 inline) ════════════════════════
    h     = load_history(limit_per_key=20)
    pri   = "DAY" if "DAY" in sym_data else tf_order[0] if tf_order else "DAY"
    h_key = sym + "_" + pri
    if h.get(h_key):
        cprint("  RECENT HISTORY  (" + pri + " — last 5 entries)", C.WHITE, bold=True)
        div("─")
        for he in reversed(h[h_key][-5:]):
            hs   = he.get("signal","NONE")
            hpre = he.get("prev_signal","NONE")
            hcol = signal_color(hs)
            if hs == "BREAKOUT":
                htype = C.CYAN + "REVERSAL" + C.RESET if hpre == "BREAKDOWN" else C.GREEN + "FRESH/CONT" + C.RESET
            elif hs == "BREAKDOWN":
                htype = C.RED + "REVERSAL" + C.RESET if hpre == "BREAKOUT" else C.RED + "FRESH/CONT" + C.RESET
            else:
                htype = C.DIM + hs + C.RESET
            print("  " + _ljust(he.get("logged_at","—"), 18) + "  " +
                  hcol + C.BOLD + SIGNAL_SHORT.get(hs,"--") + C.RESET +
                  "  " + _ljust(htype, 12) +
                  "  RSI:" + str(he.get("rsi",0)) +
                  "  RVol:" + str(he.get("rel_vol",0)))
        div("═")
        print()

    # ══ 6. TRADE PLAN ════════════════════════════════════════════
    cprint("  TRADE PLAN RECOMMENDATION", C.WHITE, bold=True)
    div("─")
    # Find best TF setup (highest scoring breakout or breakdown)
    best_tf = None; best_score = -1
    for tf in tf_order:
        e   = as_dict(sym_data[tf])
        sig = e.get("signal","NONE")
        cs  = e.get("composite_score",0)
        if sig in ("BREAKOUT","BREAKDOWN") and cs > best_score:
            best_score = cs; best_tf = tf
    if best_tf:
        be   = sym_data[best_tf]
        bsig = be.get("signal","NONE")
        bcol = signal_color(bsig)
        btgt = as_dict(be.get("targets"))
        print("  Best Setup    : " + bcol + C.BOLD + bsig + C.RESET +
              " on " + C.YELLOW + best_tf + C.RESET +
              "  Score: " + str(best_score) + "/100")
        if btgt:
            print("  Entry         : " + C.CYAN + "₹" + str(be.get("price","—")) + C.RESET)
            print("  Target 1      : " + C.GREEN + "₹" + str(btgt.get("target1","—")) + C.RESET)
            print("  Target 2      : " + C.GREEN + "₹" + str(btgt.get("target2","—")) + C.RESET)
            print("  Stop Loss     : " + C.RED   + "₹" + str(btgt.get("stop","—"))    + C.RESET)
            rr  = be.get("risk_reward",0)
            rrc = C.GREEN if rr >= 2 else (C.YELLOW if rr >= 1.5 else C.RED)
            print("  Risk:Reward   : " + rrc + str(rr) + ":1" + C.RESET)
        # Next day context
        print("  Tomorrow bias : " + gb_col + C.BOLD + gb_str + C.RESET +
              "  (" + str(gap_score) + "% probability)")
        if bsig == "BREAKOUT" and gap_bias == "GAP_UP":
            cprint("  ✓ ALIGNED — Breakout signal + Gap-Up bias = high conviction setup", C.GREEN, bold=True)
        elif bsig == "BREAKDOWN" and gap_bias == "GAP_DOWN":
            cprint("  ✓ ALIGNED — Breakdown signal + Gap-Down bias = high conviction setup", C.RED, bold=True)
        elif gap_bias == "NEUTRAL":
            cprint("  → Wait for open direction before entering", C.YELLOW)
        else:
            cprint("  ⚠ CONFLICT — Signal and gap bias disagree. Extra caution needed.", C.YELLOW)
    else:
        cprint("  No clear setup detected across any timeframe.", C.DIM)
        cprint("  Run a fresh scan (S or I) to update data.", C.DIM)
    div("═")
    print()

    # ══ 7. STOCK SUMMARY  (Buy / Sell reasons) ═══════════════════
    s      = generate_stock_summary(sym, sym_data, conf_tfs)
    verdict    = s["verdict"]
    confidence = s["confidence"]
    v_col  = (C.GREEN  if verdict == "BUY"  else
              C.RED    if verdict == "SELL" else
              C.YELLOW if verdict == "HOLD" else C.DIM)

    cprint("  STOCK SUMMARY  —  WHY BUY / WHY SELL", C.WHITE, bold=True)
    div("─")
    print("  " + v_col + C.BOLD + "VERDICT: " + verdict +
          "   (" + confidence + " CONFIDENCE)" + C.RESET)
    print("  " + s["headline"])
    print("  " + v_col + C.BOLD + "➤ " + s["action"] + C.RESET)
    print()

    if s["buy_reasons"]:
        cprint("  BUY FACTORS (" + str(len(s["buy_reasons"])) + ")", C.GREEN)
        for r in s["buy_reasons"]:
            print("  " + C.GREEN + "✔" + C.RESET + "  " + r)
        print()
    if s["sell_reasons"]:
        cprint("  SELL FACTORS (" + str(len(s["sell_reasons"])) + ")", C.RED)
        for r in s["sell_reasons"]:
            print("  " + C.RED + "✘" + C.RESET + "  " + r)
        print()
    if s["cautions"]:
        cprint("  CAUTIONS (" + str(len(s["cautions"])) + ")", C.YELLOW)
        for r in s["cautions"]:
            print("  " + C.YELLOW + "⚠" + C.RESET + "  " + r)
        print()
    div("═")
    print()


# ─────────────────────────────────────────────────────────────
#  STOCK SUMMARY  —  Plain-English Buy / Sell / Hold reasoning
# ─────────────────────────────────────────────────────────────

def generate_stock_summary(sym, sym_data, tfs=None):
    """
    Build a structured plain-English summary explaining WHY to buy or sell.

    Returns a dict:
      verdict     : "BUY" | "SELL" | "HOLD" | "AVOID"
      confidence  : "HIGH" | "MEDIUM" | "LOW"
      headline    : one-line verdict sentence
      buy_reasons : list of str — bullish arguments
      sell_reasons: list of str — bearish arguments
      cautions    : list of str — risk warnings regardless of direction
      action      : str — what to do right now

    tfs: timeframes to score confluence over (defaults to TIMEFRAMES_SWING).
         Callers pass the user's ACTIVE_TFS so the verdict follows the
         timeframes actually on screen instead of a hardcoded DAY/WEEK/MONTH.
    """
    tfs = tfs or TIMEFRAMES_SWING
    buy_reasons  = []
    sell_reasons = []
    cautions     = []

    # ── Pick primary TF (DAY preferred, else first available) ─
    pri_tf  = "DAY" if "DAY" in sym_data else next(
        (tf for tf in TF_CONFIG if tf in sym_data), None)
    e       = sym_data.get(pri_tf, {}) if pri_tf else {}
    price   = safe_float(e.get("price",  0))
    sig     = e.get("signal",         "NONE")
    rsi     = safe_float(e.get("rsi",  50))
    rv      = safe_float(e.get("rel_vol", 1))
    cs      = safe_float(e.get("composite_score", 0))
    rr      = safe_float(e.get("risk_reward", 0))
    ema_aln = e.get("ema_alignment", "")
    mc      = e.get("macd_cross", "")
    st      = int(safe_float(e.get("st_direction", 0)))
    cps     = as_list(e.get("candle_patterns"))
    div_    = as_dict(e.get("rsi_divergence"))
    fib     = as_dict(e.get("fibonacci"))
    w52     = as_dict(e.get("52w"))
    tgt     = as_dict(e.get("targets"))
    conf    = confluence_score(sym_data, tfs)

    # ── SIGNAL ────────────────────────────────────────────────
    if sig == "BREAKOUT":
        buy_reasons.append(
            "Price has broken out on the " + (pri_tf or "DAY") +
            " chart — momentum is pointing upward")
        if e.get("retest_bo"):
            lvl = e.get("retest_bo_level", "")
            buy_reasons.append(
                "RETEST BREAKOUT confirmed: resistance ₹" + str(lvl) +
                " was tested twice and held as new support — very high conviction")
    elif sig == "BREAKDOWN":
        sell_reasons.append(
            "Price has broken down on the " + (pri_tf or "DAY") +
            " chart — momentum is pointing downward")
        if e.get("retest_bd"):
            lvl = e.get("retest_bd_level", "")
            sell_reasons.append(
                "RETEST BREAKDOWN confirmed: support ₹" + str(lvl) +
                " failed twice — sellers are in control")
    elif sig == "SIDEWAYS":
        cautions.append("Price is consolidating — no directional edge yet; wait for a breakout or breakdown")
    else:
        cautions.append("No clear signal on " + (pri_tf or "DAY") + " timeframe")

    if conf >= max(2, len(tfs) - 1):
        buy_reasons.append("ALL " + str(len(tfs)) + " active timeframes agree bullish")
    elif conf <= -max(2, len(tfs) - 1):
        sell_reasons.append("ALL " + str(len(tfs)) + " active timeframes agree bearish")

    # ── MULTI-TF CONFLUENCE ───────────────────────────────────
    bo_tfs = [tf for tf in tfs
              if as_dict(sym_data.get(tf)).get("signal") == "BREAKOUT"]
    bd_tfs = [tf for tf in tfs
              if as_dict(sym_data.get(tf)).get("signal") == "BREAKDOWN"]

    if len(bo_tfs) >= 2:
        buy_reasons.append(
            "Bullish signal aligns across multiple timeframes (" +
            ", ".join(bo_tfs) + ") — trend is consistent at all horizons")
    elif len(bd_tfs) >= 2:
        sell_reasons.append(
            "Bearish signal aligns across multiple timeframes (" +
            ", ".join(bd_tfs) + ") — downtrend is confirmed at all horizons")
    elif bo_tfs and bd_tfs:
        cautions.append(
            "Mixed signals: bullish on " + ", ".join(bo_tfs) +
            " but bearish on " + ", ".join(bd_tfs) +
            " — conflicting timeframes reduce confidence")

    # ── RSI ───────────────────────────────────────────────────
    if rsi >= 70:
        cautions.append(
            "RSI is " + str(rsi) + " — overbought zone; chasing here risks "
            "entering near a short-term top")
    elif rsi >= 55:
        buy_reasons.append(
            "RSI at " + str(rsi) + " — in bullish territory, not yet overbought; "
            "there is room to run")
    elif rsi <= 30:
        cautions.append(
            "RSI is " + str(rsi) + " — deeply oversold; potential bounce "
            "but wait for a reversal candle before buying")
    elif rsi <= 45:
        sell_reasons.append(
            "RSI at " + str(rsi) + " — weak/bearish zone; buyers are not in control")

    # ── RSI DIVERGENCE ────────────────────────────────────────
    if div_.get("regular_bull"):
        buy_reasons.append(
            "Regular Bullish RSI Divergence: price made a lower low but RSI made "
            "a higher low — selling pressure is weakening, reversal likely")
    if div_.get("hidden_bull"):
        buy_reasons.append(
            "Hidden Bullish RSI Divergence: uptrend is intact and this is likely "
            "a healthy pullback before the next leg higher")
    if div_.get("regular_bear"):
        sell_reasons.append(
            "Regular Bearish RSI Divergence: price made a higher high but RSI made "
            "a lower high — buying pressure is fading, reversal likely")
    if div_.get("hidden_bear"):
        sell_reasons.append(
            "Hidden Bearish RSI Divergence: downtrend is intact; current bounce "
            "is likely a trap before the next leg lower")

    # ── EMA ALIGNMENT ─────────────────────────────────────────
    if "FULL BULL" in ema_aln:
        buy_reasons.append(
            "EMA stack is fully bullish (EMA9 > EMA21 > EMA50 > EMA200) — "
            "price is above all key moving averages; the trend is unambiguously up")
    elif "FULL BEAR" in ema_aln:
        sell_reasons.append(
            "EMA stack is fully bearish (price below all key EMAs) — "
            "every moving average is acting as resistance above price")
    elif "BULLISH" in ema_aln:
        buy_reasons.append(
            "EMA alignment is bullish — short-term average is above medium-term; "
            "near-term momentum favours buyers")
    elif "BEARISH" in ema_aln:
        sell_reasons.append(
            "EMA alignment is bearish — short-term average is below medium-term; "
            "near-term momentum favours sellers")
    elif "MIXED" in ema_aln:
        cautions.append(
            "EMA alignment is mixed — EMAs are tangled; no clear trend direction")

    # ── MACD ─────────────────────────────────────────────────
    if mc == "BULL_CROSS":
        buy_reasons.append(
            "MACD just crossed bullishly (MACD line crossed above signal line) — "
            "a classic momentum buy signal")
    elif mc == "BEAR_CROSS":
        sell_reasons.append(
            "MACD just crossed bearishly (MACD line crossed below signal line) — "
            "a classic momentum sell signal")

    # ── SUPERTREND ────────────────────────────────────────────
    if st == 1:
        if e.get("st_direction_prev", 0) == -1:
            buy_reasons.append(
                "SuperTrend just flipped BULLISH — a fresh trend change; "
                "this is one of the cleanest buy signals in trend-following")
        else:
            buy_reasons.append(
                "SuperTrend is BULLISH — price is above the SuperTrend line; "
                "the trend-following system says stay long")
    elif st == -1:
        if e.get("st_direction_prev", 0) == 1:
            sell_reasons.append(
                "SuperTrend just flipped BEARISH — a fresh trend change; "
                "this is a clear exit / short signal")
        else:
            sell_reasons.append(
                "SuperTrend is BEARISH — price is below the SuperTrend line; "
                "trend-following says avoid or short")

    # ── VOLUME ────────────────────────────────────────────────
    # Bug fix: vol_score was computed but never used (rv is used for all volume logic).
    if rv >= 2.0 and sig == "BREAKOUT":
        buy_reasons.append(
            "Volume is " + str(round(rv, 1)) + "x the 20-day average — "
            "breakout is backed by strong institutional participation")
    elif rv >= 2.0 and sig == "BREAKDOWN":
        sell_reasons.append(
            "Volume is " + str(round(rv, 1)) + "x the 20-day average — "
            "breakdown is backed by heavy selling; not a fake-out")
    elif rv < 0.8 and sig in ("BREAKOUT", "BREAKDOWN"):
        cautions.append(
            "Volume is only " + str(round(rv, 1)) + "x average — "
            "low conviction; breakout/breakdown may be a false move")
    elif rv >= 1.5:
        buy_reasons.append(
            "Above-average volume (" + str(round(rv, 1)) + "x) confirms "
            "genuine interest in the current move")

    # ── CANDLESTICK PATTERNS ──────────────────────────────────
    bull_cp = [p for p in cps if p in CANDLE_BULL]
    bear_cp = [p for p in cps if p in CANDLE_BEAR]
    if bull_cp:
        buy_reasons.append(
            "Bullish candlestick pattern(s) detected: " + ", ".join(bull_cp) +
            " — price action itself is showing reversal/continuation to the upside")
    if bear_cp:
        sell_reasons.append(
            "Bearish candlestick pattern(s) detected: " + ", ".join(bear_cp) +
            " — price action is signalling distribution or reversal to the downside")

    # ── FIBONACCI ─────────────────────────────────────────────
    if fib and price:
        f382 = safe_float(fib.get("fib_38.2", 0))
        f500 = safe_float(fib.get("fib_50",   0))
        f618 = safe_float(fib.get("fib_61.8", 0))
        sw_h = safe_float(fib.get("sw_high",  0))
        sw_l = safe_float(fib.get("sw_low",   0))
        for pct_lbl, lvl in [("38.2%", f382), ("50.0%", f500), ("61.8%", f618)]:
            if lvl and abs(price - lvl) / max(price, 0.01) < 0.025:
                if sig == "BREAKOUT":
                    buy_reasons.append(
                        "Price is bouncing off the " + pct_lbl +
                        " Fibonacci retracement (₹" + str(lvl) +
                        ") — a textbook pullback-to-fib buy zone")
                else:
                    cautions.append(
                        "Price is near the " + pct_lbl +
                        " Fibonacci level (₹" + str(lvl) +
                        ") — key decision zone; watch for rejection or breakout")
        if sw_h and price >= sw_h * 0.99:
            cautions.append(
                "Price is near the swing high (₹" + str(sw_h) +
                ") — may face resistance at this level")
        if sw_l and price <= sw_l * 1.01:
            cautions.append(
                "Price is near the swing low (₹" + str(sw_l) +
                ") — critical support; a break below would be bearish")

    # ── 52-WEEK HIGH / LOW ────────────────────────────────────
    if w52:
        ph = safe_float(w52.get("pct_from_high", 0))
        pl = safe_float(w52.get("pct_from_low",  0))
        h52 = w52.get("high_52w", "—")
        l52 = w52.get("low_52w",  "—")
        if ph >= -3:
            if sig == "BREAKOUT":
                buy_reasons.append(
                    "Price is within " + str(abs(ph)) + "% of its 52-week high (₹" +
                    str(h52) + ") — near multi-month highs shows sustained demand; "
                    "a fresh all-time/52W high breakout would be very bullish")
            else:
                cautions.append(
                    "Price is near its 52-week high (₹" + str(h52) +
                    ") — strong resistance zone; "
                    "sellers often appear at year-highs")
        elif pl <= 10:
            if sig == "BREAKDOWN":
                sell_reasons.append(
                    "Price is only " + str(abs(pl)) + "% above its 52-week low (₹" +
                    str(l52) + ") — stock is near annual lows; "
                    "downtrend is well entrenched")
            else:
                buy_reasons.append(
                    "Price is near its 52-week low (₹" + str(l52) +
                    ") — deeply discounted; a reversal here would offer "
                    "an excellent risk:reward for patient buyers")
        elif pl >= 50:
            buy_reasons.append(
                "Price has recovered " + str(pl) + "% from its 52-week low — "
                "strong trend recovery underway")

    # ── RISK : REWARD ─────────────────────────────────────────
    #
    # Bug fix: this block used to reward the long side only. risk_reward is
    # always a positive ratio — |T1 - price| / |price - stop| — so a clean
    # BREAKDOWN produced a healthy number and was then praised as an
    # "excellent setup" and pushed onto buy_reasons. Every favourable R:R
    # therefore added a spurious BUY argument to every short, inflating the
    # score and, in close cases, flipping the verdict to BUY outright.
    # The praise has to follow the direction of the trade.
    if rr >= 2.5 and sig == "BREAKOUT":
        buy_reasons.append(
            "Risk:Reward is " + str(rr) + ":1 — excellent setup; "
            "you risk ₹1 to potentially make ₹" + str(rr))
    elif rr >= 2.5 and sig == "BREAKDOWN":
        sell_reasons.append(
            "Risk:Reward is " + str(rr) + ":1 — excellent short setup; "
            "you risk ₹1 to potentially make ₹" + str(rr))
    elif rr >= 1.5 and sig == "BREAKOUT":
        buy_reasons.append(
            "Risk:Reward is " + str(rr) + ":1 — acceptable setup")
    elif rr >= 1.5 and sig == "BREAKDOWN":
        sell_reasons.append(
            "Risk:Reward is " + str(rr) + ":1 — acceptable short setup")
    elif 0 < rr < 1.5 and sig in ("BREAKOUT", "BREAKDOWN"):
        cautions.append(
            "Risk:Reward is only " + str(rr) + ":1 — "
            "the potential gain does not justify the risk at this entry")

    # ── COMPOSITE SCORE ───────────────────────────────────────
    #
    # Bug fix: the `cs >= 75` branch had no signal guard at all (unlike the
    # 55 and 30 branches, which did check for BREAKOUT). calc_composite_score
    # is direction-neutral — it scores conviction, not direction, using
    # abs(confluence) and symmetric bull/bear point tallies — so a powerful
    # BREAKDOWN easily clears 75 and was then described as "most indicators
    # are aligned bullishly" and counted as a BUY argument.
    if cs >= 75 and sig == "BREAKOUT":
        buy_reasons.append(
            "Composite signal score is " + str(int(cs)) + "/100 — "
            "extremely high multi-factor quality rating; "
            "most indicators are aligned bullishly")
    elif cs >= 75 and sig == "BREAKDOWN":
        sell_reasons.append(
            "Composite signal score is " + str(int(cs)) + "/100 — "
            "extremely high multi-factor quality rating; "
            "most indicators are aligned bearishly")
    elif cs >= 55 and sig == "BREAKOUT":
        buy_reasons.append(
            "Composite score of " + str(int(cs)) + "/100 indicates a solid setup "
            "with good indicator confluence")
    elif cs >= 55 and sig == "BREAKDOWN":
        sell_reasons.append(
            "Composite score of " + str(int(cs)) + "/100 indicates a solid "
            "short setup with good indicator confluence")
    elif cs <= 30 and sig == "BREAKOUT":
        cautions.append(
            "Composite score is low (" + str(int(cs)) + "/100) despite a breakout signal — "
            "most indicators are NOT confirming; treat with caution")
    elif cs <= 30 and sig == "BREAKDOWN":
        cautions.append(
            "Composite score is low (" + str(int(cs)) + "/100) despite a breakdown signal — "
            "most indicators are NOT confirming; treat with caution")

    # ── SUPPORT / RESISTANCE CONTEXT ──────────────────────────
    if tgt and price:
        sl  = safe_float(tgt.get("stop", 0))
        t1  = safe_float(tgt.get("target1", 0))
        if sl > 0 and sig == "BREAKOUT":
            buy_reasons.append(
                "Entry: ₹" + str(price) +
                "  |  Stop Loss: ₹" + str(sl) +
                "  |  Target 1: ₹" + str(t1) +
                " — clear trade levels defined")
        elif sl > 0 and sig == "BREAKDOWN":
            sell_reasons.append(
                "Short Entry: ₹" + str(price) +
                "  |  Stop Loss: ₹" + str(sl) +
                "  |  Target 1: ₹" + str(t1) +
                " — clear trade levels defined")

    # ── VERDICT ───────────────────────────────────────────────
    bull_pts = len(buy_reasons)
    bear_pts = len(sell_reasons)

    if sig == "BREAKOUT":
        if bull_pts >= 4 and cs >= 55:
            verdict    = "BUY"
            confidence = "HIGH"
        elif bull_pts >= 2:
            verdict    = "BUY"
            confidence = "MEDIUM"
        else:
            verdict    = "HOLD"
            confidence = "LOW"
    elif sig == "BREAKDOWN":
        if bear_pts >= 4 and cs >= 55:
            verdict    = "SELL"
            confidence = "HIGH"
        elif bear_pts >= 2:
            verdict    = "SELL"
            confidence = "MEDIUM"
        else:
            verdict    = "HOLD"
            confidence = "LOW"
    elif sig == "SIDEWAYS":
        verdict    = "HOLD"
        confidence = "MEDIUM"
    else:
        if bull_pts > bear_pts + 1:
            verdict    = "BUY"
            confidence = "LOW"
        elif bear_pts > bull_pts + 1:
            verdict    = "SELL"
            confidence = "LOW"
        else:
            verdict    = "AVOID"
            confidence = "LOW"

    # One-line headline
    sec = SECTOR_MAP.get(sym, "")
    if verdict == "BUY":
        headline = (sym + " (" + sec + ") looks BULLISH — " +
                    str(bull_pts) + " buy factors vs " + str(bear_pts) + " sell factors")
    elif verdict == "SELL":
        headline = (sym + " (" + sec + ") looks BEARISH — " +
                    str(bear_pts) + " sell factors vs " + str(bull_pts) + " buy factors")
    elif verdict == "HOLD":
        headline = (sym + " (" + sec + ") — wait for clearer direction before acting")
    else:
        headline = (sym + " (" + sec + ") — no actionable setup; stay on the sidelines")

    # Action sentence
    if verdict == "BUY":
        if tgt:
            action = ("Consider a LONG entry near ₹" + str(price) +
                      " with stop below ₹" + str(tgt.get("stop","—")) +
                      " and target ₹" + str(tgt.get("target1","—")) + ".")
        else:
            action = "Consider a LONG entry; use ATR or swing low as stop loss."
    elif verdict == "SELL":
        if tgt:
            action = ("Consider EXITING longs or a SHORT entry near ₹" + str(price) +
                      " with stop above ₹" + str(tgt.get("stop","—")) +
                      " and target ₹" + str(tgt.get("target1","—")) + ".")
        else:
            action = "Consider EXITING or shorting; use ATR or swing high as stop."
    elif verdict == "HOLD":
        action = "Hold existing positions if you own it; do NOT initiate new trades."
    else:
        action = "No trade. Wait for a clear breakout or breakdown before risking capital."

    return {
        "verdict":      verdict,
        "confidence":   confidence,
        "headline":     headline,
        "buy_reasons":  buy_reasons,
        "sell_reasons": sell_reasons,
        "cautions":     cautions,
        "action":       action,
    }


def summary_view(sym, data, tfs=None):
    """Full-screen stock summary view — called from menu option O or view_detail."""
    tfs = tfs or TIMEFRAMES_SWING
    if sym not in data:
        cprint("  No data for " + sym + ". Run a scan first.", C.RED)
        return

    sym_data = data[sym]
    s = generate_stock_summary(sym, sym_data, tfs)

    verdict    = s["verdict"]
    confidence = s["confidence"]

    # Colours
    if verdict == "BUY":
        v_col = C.GREEN
    elif verdict == "SELL":
        v_col = C.RED
    elif verdict == "HOLD":
        v_col = C.YELLOW
    else:
        v_col = C.DIM

    conf_col = (C.GREEN if confidence == "HIGH"
                else (C.YELLOW if confidence == "MEDIUM" else C.DIM))

    header("◉ STOCK SUMMARY  —  " + sym)

    # ── VERDICT BOX ──────────────────────────────────────────
    div("═")
    print()
    cprint("  " + v_col + C.BOLD + "  ██  VERDICT : " + verdict +
           "   (" + conf_col + confidence + C.RESET + v_col +
           " CONFIDENCE)  ██" + C.RESET, "")
    print()
    cprint("  " + s["headline"], C.WHITE)
    print()
    div("─")
    cprint("  ➤  ACTION: " + s["action"], v_col, bold=True)
    div("═")
    print()

    # ── BUY REASONS ──────────────────────────────────────────
    if s["buy_reasons"]:
        cprint("  WHY BUY  (" + str(len(s["buy_reasons"])) + " factors)", C.GREEN, bold=True)
        div("─")
        for i, r in enumerate(s["buy_reasons"], 1):
            # Word-wrap at W-8 chars
            words  = r.split()
            lines  = []
            cur    = "  " + C.GREEN + str(i) + "." + C.RESET + "  "
            indent = "      "
            for w in words:
                if len(_ANSI_RE.sub("", cur)) + len(w) + 1 > W - 2:
                    lines.append(cur)
                    cur = indent + w
                else:
                    cur += (" " if cur.strip() else "") + w
            lines.append(cur)
            print("\n".join(lines))
        print()

    # ── SELL REASONS ─────────────────────────────────────────
    if s["sell_reasons"]:
        cprint("  WHY SELL / RISK  (" + str(len(s["sell_reasons"])) + " factors)", C.RED, bold=True)
        div("─")
        for i, r in enumerate(s["sell_reasons"], 1):
            words  = r.split()
            lines  = []
            cur    = "  " + C.RED + str(i) + "." + C.RESET + "  "
            indent = "      "
            for w in words:
                if len(_ANSI_RE.sub("", cur)) + len(w) + 1 > W - 2:
                    lines.append(cur)
                    cur = indent + w
                else:
                    cur += (" " if cur.strip() else "") + w
            lines.append(cur)
            print("\n".join(lines))
        print()

    # ── CAUTIONS ─────────────────────────────────────────────
    if s["cautions"]:
        cprint("  CAUTIONS / WATCH-OUTS  (" + str(len(s["cautions"])) + ")", C.YELLOW, bold=True)
        div("─")
        for i, r in enumerate(s["cautions"], 1):
            words  = r.split()
            lines  = []
            cur    = "  " + C.YELLOW + "⚠ " + str(i) + "." + C.RESET + "  "
            indent = "        "
            for w in words:
                if len(_ANSI_RE.sub("", cur)) + len(w) + 1 > W - 2:
                    lines.append(cur)
                    cur = indent + w
                else:
                    cur += (" " if cur.strip() else "") + w
            lines.append(cur)
            print("\n".join(lines))
        print()

    div("═")
    cprint("  Disclaimer: This is a technical analysis summary, not financial advice.", C.DIM)
    cprint("  Always do your own research and manage position size appropriately.", C.DIM)
    div("═")
    print()


def sector_view(data):
    header("SECTOR ANALYSIS")
    sectors = {}
    for sym, entry in data.items():
        sec = SECTOR_MAP.get(sym, "OTHER")
        if sec not in sectors:
            sectors[sec] = []
        sectors[sec].append((sym, entry))

    for sec, items in sorted(sectors.items()):
        cprint("\n  ┌─ " + sec + " " + "─" * (W - 5 - len(sec)), C.CYAN, bold=True)
        bo = sum(1 for _, e in items if as_dict(e.get("DAY")).get("signal") == "BREAKOUT")
        bd = sum(1 for _, e in items if as_dict(e.get("DAY")).get("signal") == "BREAKDOWN")
        sw = sum(1 for _, e in items if as_dict(e.get("DAY")).get("signal") == "SIDEWAYS")
        print("  │  " + C.GREEN + "BO:" + str(bo) + C.RESET + "  " +
              C.RED + "BD:" + str(bd) + C.RESET + "  " +
              C.YELLOW + "SW:" + str(sw) + C.RESET)
        for sym, entry in items:
            score = confluence_score(entry, TIMEFRAMES_SWING)
            d = sig_str(as_dict(entry.get("DAY")).get("signal", "NONE"))
            w = sig_str(as_dict(entry.get("WEEK")).get("signal", "NONE"))
            m = sig_str(as_dict(entry.get("MONTH")).get("signal", "NONE"))
            # Bug fix: fallback was list(entry.keys())[0] which is "symbol" (a string),
            # causing AttributeError when .get("price",0) is called on it.
            _tf_key = "DAY" if "DAY" in entry else next(
                (tf for tf in TF_CONFIG if tf in entry), None)
            price = safe_float(as_dict(entry.get(_tf_key)).get("price", 0)) if _tf_key else 0
            price_str = "₹" + str(price) if price else "—"
            print("  │    " +
                  _ljust(sym,  14) + "  " +
                  "D:" + _ljust(d, 4) + "  " +
                  "W:" + _ljust(w, 4) + "  " +
                  "M:" + _ljust(m, 4) + "  " +
                  _ljust(price_str, 10) + "  " +
                  confluence_label(score))
        print("  └" + "─" * (W - 3))


def filter_view(data, signal, tfs=None):
    tfs = tfs or TIMEFRAMES_SWING
    header("FILTER: " + signal)
    found = False
    rows  = []
    for sym, entry in data.items():
        matching = [tf for tf in tfs if as_dict(entry.get(tf)).get("signal") == signal]
        if matching:
            found  = True
            tfs_str= " + ".join(matching)
            liq    = max(safe_float(as_dict(entry[tf]).get("atr_pct", 0)) for tf in matching if tf in entry)
            vol    = max(safe_float(as_dict(entry[tf]).get("volume",    0)) for tf in matching if tf in entry)
            oi     = max(safe_float(as_dict(entry[tf]).get("oi",        0)) for tf in matching if tf in entry)
            rsi    = safe_float(entry[matching[0]].get("rsi", 0))
            str_v  = safe_float(entry[matching[0]].get("trend_strength", 0))
            rows.append((sym, tfs_str, liq, vol, oi, rsi, str_v))

    if not found:
        cprint("  No symbols showing " + signal, C.YELLOW)
    else:
        col = signal_color(signal)
        cprint("  " +
               _ljust("SYMBOL",     14) + "  " +
               _ljust("TIMEFRAMES", 22) + "  " +
               _rjust("Vol",  4) + "  " +
               _rjust("ATR%", 5) + "  " +
               _rjust("RSI",  5) + "  " +
               "TREND", C.DIM)
        div()
        # Sort by trend_strength
        rows.sort(key=lambda r: r[6], reverse=(signal == "BREAKOUT"))
        for sym, tfs_s, liq, vol, oi, rsi, str_v in rows:
            atr_s = "{:.1f}%".format(float(liq)) if liq else "0.0%"
            print("  " +
                  _ljust(col + sym + C.RESET, 14) + "  " +
                  _ljust(tfs_s,               22) + "  " +
                  _rjust(str(vol) + "%",        4) + "  " +
                  _rjust(atr_s,                 5) + "  " +
                  _rjust(str(rsi),              5) + "  " +
                  trend_bar(str_v, 8))
    print()


def statistics_view(data):
    header("STATISTICS")
    if not data:
        cprint("  No data.\n", C.YELLOW)
        return

    total = len(data)
    cprint("  Symbols: " + str(total), C.CYAN)

    for tf in list(TF_CONFIG.keys()):
        counts = {"BREAKOUT": 0, "BREAKDOWN": 0, "SIDEWAYS": 0, "NONE": 0}
        liq_s = vol_s = oi_s = rsi_s = cnt = 0
        for entry in data.values():
            if tf not in entry:
                continue
            e = as_dict(entry[tf])
            s = e.get("signal", "NONE")
            counts[s] = counts.get(s, 0) + 1
            if s != "NONE":
                liq_s += safe_float(e.get("atr_pct", 0))
                vol_s += safe_float(e.get("volume",    0))
                oi_s  += safe_float(e.get("oi",        0))
                rsi_s += safe_float(e.get("rsi",       50))
                cnt   += 1

        denom = max(total, 1)
        # Bug fix (B023): these lambdas closed over the loop variable `cnt`.
        # Bind it as a default argument so the value is captured now.
        avg_int   = lambda x, n=cnt: x // n if n else 0
        avg_float = lambda x, n=cnt: round(x / n, 1) if n else 0.0

        cprint("\n  ── " + tf + " " + "─" * (W - 6 - len(tf)), C.CYAN)
        # Bug fix: was bar(count, denom) which set denom (~50) as bar *width*, not scale.
        # Correct: scale count to 0-100 percentage then pass to bar() with default width.
        print("  " + C.GREEN  + "Breakout : {:>3}  [{}]".format(counts["BREAKOUT"],  bar(int(counts["BREAKOUT"]  / denom * 100))) + C.RESET)
        print("  " + C.RED    + "Breakdown: {:>3}  [{}]".format(counts["BREAKDOWN"], bar(int(counts["BREAKDOWN"] / denom * 100))) + C.RESET)
        print("  " + C.YELLOW + "Sideways : {:>3}  [{}]".format(counts["SIDEWAYS"],  bar(int(counts["SIDEWAYS"]  / denom * 100))) + C.RESET)
        print("  " + C.DIM    + "No Signal: {:>3}".format(counts["NONE"]) + C.RESET)
        if cnt:
            print("  Avg Vol/ATR%/RSI: {}% / {}% / {}".format(
                avg_int(vol_s), avg_float(liq_s), avg_int(rsi_s)))

    print()
    cprint("  ── CONFLUENCE RANKING " + "─" * (W - 24), C.CYAN)
    ranked = sorted(data.items(), key=lambda x: confluence_score(x[1], TIMEFRAMES_SWING), reverse=True)
    for sym, entry in ranked:
        score = confluence_score(entry, TIMEFRAMES_SWING)
        score_str = "{:>+2}".format(score)
        print("  " + _ljust(sym, 16) + "  " + score_str + "  " + confluence_label(score))
    print()


def alert_scanner(data, tfs=None):
    tfs = tfs or TIMEFRAMES_SWING
    header("ALERT SCANNER")
    all_alerts = []
    for sym, entry in data.items():
        all_alerts.extend(gather_alerts(sym, entry, tfs))

    if not all_alerts:
        cprint("  ✓ All clear. No alerts.", C.GREEN)
    else:
        cprint("  " +
               _ljust("SYMBOL", 14) + "  " +
               _ljust("ALERT",  30) + "  " +
               "DETAIL", C.DIM)
        div()
        for sym, alert, detail in all_alerts:
            print("  " +
                  _ljust(sym,   14) + "  " +
                  _ljust(alert, 30) + "  " +
                  detail)
        div()
        cprint("\n  Total alerts: " + str(len(all_alerts)), C.YELLOW)
    print()
    input("  Press ENTER...")


def history_view(sym, tf):
    h   = load_history(limit_per_key=40)
    key = sym + "_" + tf
    header("HISTORY: " + sym + " / " + tf)
    if key not in h or not h[key]:
        cprint("  No history yet.\n", C.YELLOW)
        return

    entries = h[key][-20:]

    # ── Legend ────────────────────────────────────────────────────
    cprint("  Breakout type:", C.DIM)
    cprint("    " + C.GREEN + C.BOLD + "FRESH   " + C.RESET + C.DIM + " BO/BD after NONE or SIDEWAYS  (new move)", C.DIM)
    cprint("    " + C.CYAN  + C.BOLD + "REVERSAL" + C.RESET + C.DIM + " BO after BREAKDOWN or BD after BREAKOUT (trend flip)", C.DIM)
    cprint("    " + C.GREEN +          "CONT    " + C.RESET + C.DIM + " BO after BO / BD after BD  (continuation)", C.DIM)
    print()

    # ── Column header ─────────────────────────────────────────────
    cprint("  " +
           _ljust("CANDLE TIME",   18) + "  " +
           _rjust("SIG",            5) + "  " +
           _ljust("TYPE",          10) + "  " +
           _rjust("RSI",            5) + "  " +
           _rjust("RVol",           5) + "  " +
           _rjust("Vol",            4) + "  " +
           _rjust("ATR%",           5) + "  " +
           "NOTE", C.DIM)
    div()

    for e in reversed(entries):
        sig  = e.get("signal",      "NONE")
        pre  = e.get("prev_signal", "NONE")
        col  = signal_color(sig)
        sig_tag = col + C.BOLD + SIGNAL_SHORT.get(sig, "--") + C.RESET
        rsi  = safe_float(e.get("rsi", 0))

        # ── Breakout / breakdown type ─────────────────────────────
        if sig == "BREAKOUT":
            if   pre in ("NONE", ""):  bo_type = C.GREEN + C.BOLD + "FRESH   " + C.RESET
            elif pre == "SIDEWAYS":    bo_type = C.GREEN + C.BOLD + "FRESH   " + C.RESET
            elif pre == "BREAKDOWN":   bo_type = C.CYAN  + C.BOLD + "REVERSAL" + C.RESET
            else:                      bo_type = C.GREEN +          "CONT    " + C.RESET
        elif sig == "BREAKDOWN":
            if   pre in ("NONE", ""):  bo_type = C.RED   + C.BOLD + "FRESH   " + C.RESET
            elif pre == "SIDEWAYS":    bo_type = C.RED   + C.BOLD + "FRESH   " + C.RESET
            elif pre == "BREAKOUT":    bo_type = C.RED   + C.BOLD + "REVERSAL" + C.RESET
            else:                      bo_type = C.RED   +          "CONT    " + C.RESET
        elif sig == "SIDEWAYS":        bo_type = C.YELLOW +         "CONSOL  " + C.RESET
        else:                          bo_type = C.DIM   +          "—       " + C.RESET

        # ── RSI color ─────────────────────────────────────────────
        rsi_col = (C.RED    if rsi > 75 else
                   C.GREEN  if rsi > 55 else
                   C.YELLOW if rsi > 40 else C.RED)
        rsi_str = rsi_col + str(rsi) + C.RESET

        chg = " ⚡" if e.get("signal_changed") else "  "

        print("  " +
              _ljust(e.get("logged_at", "—"),  18) + "  " +
              _rjust(sig_tag + chg,                  5) + "  " +
              _ljust(bo_type,                        10) + "  " +
              _rjust(rsi_str,                         5) + "  " +
              _rjust(str(e.get("rel_vol", 0)),        5) + "  " +
              _rjust(str(e.get("volume",  0)) + "%",  4) + "  " +
              _rjust(str(e.get("atr_pct", 0)) + "%",  5) + "  " +
              (C.YELLOW + e.get("note","") + C.RESET if e.get("note") else ""))

    div()

    # ── Latest signal summary ─────────────────────────────────────
    if entries:
        newest = entries[-1]
        ns = newest.get("signal",      "NONE")
        np = newest.get("prev_signal", "NONE")
        if ns == "BREAKOUT":
            if np == "BREAKDOWN":
                cprint("  ★ Latest: REVERSAL BREAKOUT from BREAKDOWN — strongest signal type.", C.CYAN, bold=True)
                cprint("    Trend flipped bullish. High conviction when volume > 1.5x avg.", C.DIM)
            elif np in ("SIDEWAYS", "NONE", ""):
                cprint("  ★ Latest: FRESH BREAKOUT from consolidation.", C.GREEN, bold=True)
                cprint("    Confirm with volume spike. Watch for false breakout on low RVol.", C.DIM)
            else:
                cprint("  ★ Latest: CONTINUATION BREAKOUT — trend already running.", C.GREEN)
                cprint("    Lower risk if RSI < 75. Overbought (>80) = higher pullback risk.", C.DIM)
        elif ns == "BREAKDOWN":
            if np == "BREAKOUT":
                cprint("  ★ Latest: REVERSAL BREAKDOWN from BREAKOUT — bearish trend flip.", C.RED, bold=True)
            elif np in ("SIDEWAYS", "NONE", ""):
                cprint("  ★ Latest: FRESH BREAKDOWN from consolidation.", C.RED, bold=True)
            else:
                cprint("  ★ Latest: CONTINUATION BREAKDOWN.", C.RED)
    print()


def best_setups_view(data, tfs=None):
    """Rank all symbols by composite score and show top 20 setups."""
    tfs = tfs or TIMEFRAMES_SWING
    header("BEST SETUPS  —  Ranked by Composite Score")

    rows = []
    for sym, entry in data.items():
        tf_key = _score_tf_key(entry, tfs)
        e      = entry.get(tf_key, {})
        cscore = safe_float(e.get("composite_score", 0))
        sig    = e.get("signal", "NONE")
        price  = safe_float(e.get("price", 0))
        rr     = safe_float(e.get("risk_reward", 0))
        rsi    = safe_float(e.get("rsi", 0))
        cps    = as_list(e.get("candle_patterns"))
        div_   = as_dict(e.get("rsi_divergence"))
        conf   = confluence_score(entry, tfs)
        adx_v  = safe_float(e.get("adx", 0))
        rows.append((sym, sig, cscore, price, rr, rsi, cps, div_, conf, tf_key, adx_v))

    rows.sort(key=lambda r: r[2], reverse=True)
    top = [r for r in rows if r[1] in ("BREAKOUT", "BREAKDOWN")][:20]

    if not top:
        cprint("  No BREAKOUT/BREAKDOWN setups found. Run scan first.\n", C.YELLOW)
        return

    cprint("  " +
           _ljust("SYM",    12) + "  " +
           _ljust("SIG",     8) + "  " +
           _rjust("SCR",     4) + "  " +
           _rjust("PRICE",   9) + "  " +
           _rjust("R:R",     5) + "  " +
           _rjust("RSI",     5) + "  " +
           _rjust("ADX",     5) + "  " +
           _ljust("CANDLES / DIVERGENCE", 30) + "  " +
           "CONFLUENCE", C.DIM)
    div()

    for sym, sig, cscore, price, rr, rsi, cps, div_, conf, tf_key, adx_v in top:
        col    = signal_color(sig)
        sc_col = C.GREEN if cscore >= 65 else (C.YELLOW if cscore >= 40 else C.RED)
        rr_col = C.GREEN if rr >= 2 else (C.YELLOW if rr >= 1.5 else C.RED)
        adx_col= C.GREEN if adx_v > 40 else (C.YELLOW if adx_v > 25 else C.DIM)

        # Summarize patterns + divergence
        extras = []
        bull_cp = [p for p in cps if p in CANDLE_BULL]
        bear_cp = [p for p in cps if p in CANDLE_BEAR]
        if bull_cp: extras.append(C.GREEN  + "+".join(bull_cp) + C.RESET)
        if bear_cp: extras.append(C.RED    + "+".join(bear_cp) + C.RESET)
        if div_.get("regular_bull"): extras.append(C.GREEN + "Reg.BullDiv" + C.RESET)
        if div_.get("hidden_bull"):  extras.append(C.GREEN + "Hid.BullDiv" + C.RESET)
        if div_.get("regular_bear"): extras.append(C.RED   + "Reg.BearDiv" + C.RESET)
        if div_.get("hidden_bear"):  extras.append(C.RED   + "Hid.BearDiv" + C.RESET)
        extras_str = " ".join(extras) if extras else C.DIM + "—" + C.RESET

        price_str = fmt_price(price)

        print("  " +
              _ljust(col + sym + C.RESET, 12) + "  " +
              _ljust(col + sig + C.RESET,  8) + "  " +
              _rjust(sc_col + str(cscore) + C.RESET, 4) + "  " +
              _rjust(price_str, 9) + "  " +
              _rjust(rr_col + str(rr) + C.RESET, 5) + "  " +
              _rjust(str(rsi), 5) + "  " +
              _rjust(adx_col + str(adx_v) + C.RESET, 5) + "  " +
              _ljust(extras_str, 30) + "  " +
              confluence_label(conf))
    div()
    print()


def watchlist_view(data, tfs=None):
    """Show only watchlisted symbols with full detail."""
    tfs = tfs or TIMEFRAMES_SWING
    wl  = load_watchlist()
    header("WATCHLIST  ★  " + str(len(wl)) + " symbols")

    if not wl:
        cprint("  Watchlist is empty. Use [W] to star symbols.\n", C.YELLOW)
        return

    for sym in sorted(wl):
        if sym not in data:
            cprint("  " + sym + "  — no data (run scan)", C.DIM)
            continue
        entry  = data[sym]
        tf_key = _score_tf_key(entry, tfs)
        e      = entry.get(tf_key, {})
        sig    = e.get("signal", "NONE")
        col    = signal_color(sig)
        price  = safe_float(e.get("price", 0))
        cscore = safe_float(e.get("composite_score", 0))
        sc_col = C.GREEN if cscore >= 65 else (C.YELLOW if cscore >= 40 else C.RED)
        rr     = safe_float(e.get("risk_reward", 0))
        conf   = confluence_score(entry, tfs)
        signals_str = "  ".join(
            sig_str(as_dict(entry.get(tf)).get("signal", "NONE")) for tf in tfs)
        price_str = fmt_price(price)

        print("  " + C.YELLOW + "★ " + C.RESET +
              C.BOLD + _ljust(sym, 12) + C.RESET + "  " +
              col + C.BOLD + "{:<10}".format(sig) + C.RESET +
              "  " + signals_str +
              "  Scr:" + sc_col + str(cscore) + C.RESET +
              "  R:R " + str(rr) +
              "  " + price_str +
              "  " + confluence_label(conf))
        tgt = as_dict(e.get("targets"))
        if tgt:
            print("    T1: ₹{}  T2: ₹{}  SL: ₹{}".format(
                tgt.get("target1","—"), tgt.get("target2","—"), tgt.get("stop","—")))
        w52 = as_dict(e.get("52w"))
        if w52:
            print("    52W H: ₹{} ({:+.1f}%)  52W L: ₹{} ({:+.1f}%)".format(
                w52.get("high_52w","—"), w52.get("pct_from_high",0),
                w52.get("low_52w","—"),  w52.get("pct_from_low",0)))
        div("·", 60)
    print()


def candle_pattern_view(data, tfs=None):
    """Show all symbols with detected candlestick patterns."""
    tfs = tfs or TIMEFRAMES_SWING
    header("CANDLESTICK PATTERNS")

    found = False
    for sym, entry in sorted(data.items()):
        tf_key = _score_tf_key(entry, tfs)
        e      = entry.get(tf_key, {})
        cps    = as_list(e.get("candle_patterns"))
        if not cps:
            continue
        found  = True
        sig    = e.get("signal", "NONE")
        col    = signal_color(sig)
        bull_cp = [p for p in cps if p in CANDLE_BULL]
        bear_cp = [p for p in cps if p in CANDLE_BEAR]
        other   = [p for p in cps if p not in CANDLE_BULL + CANDLE_BEAR]

        print("  " + C.BOLD + _ljust(sym, 14) + C.RESET +
              col + "{:<10}".format(sig) + C.RESET + "  " +
              (C.GREEN + " ".join(bull_cp) + C.RESET + "  " if bull_cp else "") +
              (C.RED   + " ".join(bear_cp) + C.RESET + "  " if bear_cp else "") +
              (C.YELLOW+ " ".join(other)   + C.RESET if other else ""))

    if not found:
        cprint("  No patterns detected. Run a scan to populate data.\n", C.YELLOW)
    print()


def export_csv(data, tfs=None):
    """Export scan data to CSV for spreadsheet analysis."""
    tfs   = tfs or TIMEFRAMES_SWING
    fname = "master_export_" + datetime.now(IST).strftime("%Y%m%d_%H%M") + ".csv"
    import csv

    fieldnames = [
        "symbol","sector","timeframe","signal","price","atr","atr_pct",
        "rsi","rel_vol","volume","trend_strength","composite_score",
        "risk_reward","macd_cross","st_direction","ema_alignment",
        "confluence","candle_patterns","rsi_div_bull","rsi_div_bear",
        "fib_38","fib_50","fib_62",
        "high_52w","low_52w","pct_from_high","pct_from_low",
        "target1","target2","stop","support","resistance",
        "adx","plus_di","minus_di","williams_r","cci","mfi",
        "updated"
    ]

    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sym, entry in sorted(data.items()):
            sector = SECTOR_MAP.get(sym, "")
            conf   = confluence_score(entry, tfs)
            for tf in tfs:
                if tf not in entry:
                    continue
                e    = as_dict(entry[tf])
                fib  = as_dict(e.get("fibonacci"))
                w52  = as_dict(e.get("52w"))
                div_ = as_dict(e.get("rsi_divergence"))
                tgt  = as_dict(e.get("targets"))
                cps  = as_list(e.get("candle_patterns"))
                w.writerow({
                    "symbol":          sym,
                    "sector":          sector,
                    "timeframe":       tf,
                    "signal":          e.get("signal",         "NONE"),
                    "price":           e.get("price",          0),
                    "atr":             e.get("atr",            0),
                    "atr_pct":         e.get("atr_pct",        0),
                    "rsi":             e.get("rsi",            0),
                    "rel_vol":         e.get("rel_vol",        0),
                    "volume":          e.get("volume",         0),
                    "trend_strength":  e.get("trend_strength", 0),
                    "composite_score": e.get("composite_score",0),
                    "risk_reward":     e.get("risk_reward",    0),
                    "macd_cross":      e.get("macd_cross",     ""),
                    "st_direction":    e.get("st_direction",   0),
                    "ema_alignment":   e.get("ema_alignment",  ""),
                    "confluence":      conf,
                    "candle_patterns": "|".join(cps),
                    "rsi_div_bull":    int(div_.get("regular_bull", False) or div_.get("hidden_bull", False)),
                    "rsi_div_bear":    int(div_.get("regular_bear", False) or div_.get("hidden_bear", False)),
                    "fib_38":          fib.get("fib_38.2", ""),
                    "fib_50":          fib.get("fib_50",   ""),
                    "fib_62":          fib.get("fib_61.8", ""),
                    "high_52w":        w52.get("high_52w",        ""),
                    "low_52w":         w52.get("low_52w",         ""),
                    "pct_from_high":   w52.get("pct_from_high",   ""),
                    "pct_from_low":    w52.get("pct_from_low",    ""),
                    "target1":         tgt.get("target1",         ""),
                    "target2":         tgt.get("target2",         ""),
                    "stop":            tgt.get("stop",            ""),
                    "support":         "|".join(str(s) for s in as_list(e.get("support"))),
                    "resistance":      "|".join(str(r) for r in as_list(e.get("resistance"))),
                    "adx":             e.get("adx",        0),
                    "plus_di":         e.get("plus_di",    0),
                    "minus_di":        e.get("minus_di",   0),
                    "williams_r":      e.get("williams_r", 0),
                    "cci":             e.get("cci",        0),
                    "mfi":             e.get("mfi",        0),
                    "updated":         e.get("updated", ""),
                })

    cprint("  ✓ CSV saved → " + fname, C.GREEN)
    input("  Press ENTER...")


def export_report(data, tfs=None):
    tfs = tfs or TIMEFRAMES_SWING
    fname = "master_report_" + datetime.now(IST).strftime("%Y%m%d_%H%M") + ".txt"
    lines = [
        "MASTER SCANNER v8.0 PRO — REPORT",
        "Generated: " + datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "=" * W, ""
    ]
    for sym, entry in data.items():
        sector = SECTOR_MAP.get(sym, "")
        lines.append("SYMBOL: " + sym + "  [" + sector + "]")
        lines.append("─" * 40)
        for tf in list(TF_CONFIG.keys()):
            if tf not in entry:
                continue
            e   = as_dict(entry[tf])
            fib = as_dict(e.get("fibonacci"))
            w52 = as_dict(e.get("52w"))
            div_= as_dict(e.get("rsi_divergence"))
            cps = as_list(e.get("candle_patterns"))
            lines += [
                "  " + tf,
                "    Signal       : " + e.get("signal",   "—"),
                "    Reason       : " + e.get("reason",   "—"),
                "    Price        : ₹" + str(e.get("price", "—")),
                "    Vol/ATR%     : " + str(e.get("volume", "—")) + "% / " + "{:.1f}".format(float(e.get("atr_pct", 0))) + "%",
                "    RSI          : " + str(e.get("rsi", "—")),
                "    RelVol       : " + str(e.get("rel_vol", "—")),
                "    Trend Str    : " + str(e.get("trend_strength", 0)),
                "    Comp. Score  : " + str(e.get("composite_score", 0)) + "/100",
                "    Risk:Reward  : " + str(e.get("risk_reward", 0)) + ":1",
                "    EMA Align    : " + str(e.get("ema_alignment", "—")),
                "    MACD Cross   : " + str(e.get("macd_cross", "—")),
                "    SuperTrend   : " + ("BULL" if e.get("st_direction") == 1 else
                                         "BEAR" if e.get("st_direction") == -1 else "—"),
                "    Candle Pats  : " + (" | ".join(cps) if cps else "—"),
                "    RSI Div Bull : " + ("Yes" if div_.get("regular_bull") or div_.get("hidden_bull") else "No"),
                "    RSI Div Bear : " + ("Yes" if div_.get("regular_bear") or div_.get("hidden_bear") else "No"),
            ]
            if fib:
                lines += [
                    "    Fib 38.2%    : ₹" + str(fib.get("fib_38.2",  "—")),
                    "    Fib 50.0%    : ₹" + str(fib.get("fib_50",    "—")),
                    "    Fib 61.8%    : ₹" + str(fib.get("fib_61.8",  "—")),
                    "    Swing H/L   : ₹{} / ₹{}".format(fib.get("sw_high","—"), fib.get("sw_low","—")),
                ]
            if w52:
                lines += [
                    "    52W High     : ₹{} ({:+.1f}%)".format(w52.get("high_52w","—"), w52.get("pct_from_high",0)),
                    "    52W Low      : ₹{} ({:+.1f}%)".format(w52.get("low_52w","—"),  w52.get("pct_from_low",0)),
                ]
            tgt = as_dict(e.get("targets"))
            if tgt:
                lines += [
                    "    Target 1     : ₹" + str(tgt.get("target1", "—")),
                    "    Target 2     : ₹" + str(tgt.get("target2", "—")),
                    "    Stop Loss    : ₹" + str(tgt.get("stop", "—")),
                ]
            supp = as_list(e.get("support"))
            res  = as_list(e.get("resistance"))
            if supp: lines.append("    Support      : " + ", ".join("₹" + str(s) for s in supp))
            if res:  lines.append("    Resistance   : " + ", ".join("₹" + str(r) for r in res))
            if e.get("note"):
                lines.append("    Note         : " + e["note"])
            if e.get("signal_changed"):
                lines.append("    ⚡ CHANGED    : from " + e.get("prev_signal","—"))
            lines.append("")

        score = confluence_score(entry, tfs)
        lines.append("  Confluence: " + str(score) + "  " +
                     confluence_label(score).replace(C.GREEN,"").replace(C.RED,"")
                     .replace(C.YELLOW,"").replace(C.BOLD,"").replace(C.RESET,"").strip())
        lines.append("  Status    : " + conflict_status(entry, tfs)
                     .replace(C.GREEN,"").replace(C.RED,"").replace(C.YELLOW,"")
                     .replace(C.BOLD,"").replace(C.RESET,"").replace(C.DIM,"").strip())
        lines.append("")

    with open(fname, "w") as f:
        f.write("\n".join(lines))
    cprint("  ✓ Saved → " + fname, C.GREEN)
    input("  Press ENTER...")


# ─────────────────────────────────────────────────────────────
#  HTML EXPORT  —  Full visual report (mirrors terminal output)
# ─────────────────────────────────────────────────────────────

def export_html(data, tfs=None):
    """
    Convert the full scan data into a self-contained HTML report.
    Mirrors every section of view_detail + dashboard in a browser-friendly layout:
      • Dashboard summary cards
      • Sector heatmap
      • Best setups table (all-TF aligned)
      • Per-symbol detail cards (signal snapshot, indicators, targets, trade plan)
      • Stock summary (buy/sell reasons)
      • Next-day gap prediction
    """
    tfs   = tfs or TIMEFRAMES_SWING
    fname = "master_report_" + datetime.now(IST).strftime("%Y%m%d_%H%M") + ".html"
    gen   = datetime.now(IST).strftime("%d %b %Y  %H:%M IST")

    # ── Helper: signal → CSS class ────────────────────────────
    def sc(sig):
        return {"BREAKOUT":"bo","BREAKDOWN":"bd","SIDEWAYS":"sw"}.get(sig,"none")

    def fmt_p(v):
        try:
            f = float(v)
            if f >= 10000: return "₹{:,.0f}".format(f)
            if f >= 1000:  return "₹{:,.1f}".format(f)
            return "₹{:.2f}".format(f)
        except Exception: return "₹" + str(v)

    def safe(v, d=0.0):
        try:
            r = float(v)
            return d if (r != r) else r
        except Exception: return d

    def esc(v):
        """HTML-escape interpolated text.

        Bug fix: symbol names, sectors, notes and signal reasons were pasted
        into the markup raw.  A note containing "<b>" (or a pasted ticker with
        "&") corrupted the report; worse, a crafted note could inject script
        into a file the user opens in a browser.
        """
        return (str(v).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("'", "&#39;"))

    # ── Pre-compute summary stats ─────────────────────────────
    bo_d = bd_d = sw_d = no_d = 0
    for e in data.values():
        s = as_dict(e.get("DAY")).get("signal","NONE")
        if s == "BREAKOUT":  bo_d += 1
        elif s == "BREAKDOWN": bd_d += 1
        elif s == "SIDEWAYS":  sw_d += 1
        else: no_d += 1

    all_bo_syms = [sym for sym,e in data.items()
                   if all(as_dict(e.get(tf)).get("signal","NONE")=="BREAKOUT" for tf in tfs)]
    all_bd_syms = [sym for sym,e in data.items()
                   if all(as_dict(e.get(tf)).get("signal","NONE")=="BREAKDOWN" for tf in tfs)]

    # Sector breakdown (DAY)
    sectors = {}
    for sym,e in data.items():
        sec = SECTOR_MAP.get(sym,"OTHER")
        if sec not in sectors: sectors[sec] = {"BO":0,"BD":0,"SW":0,"N":0}
        s = as_dict(e.get("DAY")).get("signal","NONE")
        if s == "BREAKOUT":   sectors[sec]["BO"] += 1
        elif s == "BREAKDOWN":sectors[sec]["BD"] += 1
        elif s == "SIDEWAYS": sectors[sec]["SW"] += 1
        else:                 sectors[sec]["N"]  += 1

    # Sort all symbols by DAY composite score desc
    ranked = sorted(
        [(sym,e) for sym,e in data.items() if as_dict(e.get("DAY")).get("price",0)],
        key=lambda x: safe(as_dict(x[1].get("DAY")).get("composite_score", 0)), reverse=True)

    # ── Build HTML ────────────────────────────────────────────
    H = []
    def w(s): H.append(s)

    w("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Master Scanner Report""" + " — " + gen + """</title>
<style>
:root{--bg:#0a0c0f;--s1:#111318;--s2:#181c23;--bd:#1e2330;
  --gr:#00d68f;--grd:#00d68f22;--re:#ff4757;--red:#ff475720;
  --ye:#ffd32a;--yed:#ffd32a18;--bl:#2ed8ff;--bld:#2ed8ff15;
  --tx:#e8eaf0;--mu:#5a6278;--mono:'Courier New',monospace;--sans:system-ui,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:var(--sans);font-size:13px}
a{color:var(--bl);text-decoration:none}
/* LAYOUT */
.wrap{max-width:1440px;margin:0 auto;padding:16px 20px}
.hdr{background:var(--s1);border-bottom:1px solid var(--bd);padding:14px 20px;
  display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:100}
.hdr-logo{font-family:var(--mono);font-size:11px;letter-spacing:.12em;color:var(--mu)}
.hdr-logo b{color:var(--gr)}
.hdr-ts{font-family:var(--mono);font-size:10px;color:var(--mu)}
/* CARDS ROW */
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.card{background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:14px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.card.gr::before{background:var(--gr)}.card.re::before{background:var(--re)}
.card.ye::before{background:var(--ye)}.card.bl::before{background:var(--bl)}
.card-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--mu);margin-bottom:6px}
.card-val{font-family:var(--mono);font-size:30px;font-weight:700;line-height:1;margin-bottom:3px}
.card.gr .card-val{color:var(--gr)}.card.re .card-val{color:var(--re)}
.card.ye .card-val{color:var(--ye)}.card.bl .card-val{color:var(--bl)}
.card-sub{font-size:11px;color:var(--mu)}
/* SECTION */
.sec{background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:16px;margin-bottom:14px}
.sec-title{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--mu);padding-bottom:8px;border-bottom:1px solid var(--bd);margin-bottom:12px;
  display:flex;align-items:center;justify-content:space-between}
.sec-title span{color:var(--tx)}
/* GRID 2-col */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.g3{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:14px}
/* BADGES */
.badge{display:inline-block;font-family:var(--mono);font-size:9px;font-weight:700;
  letter-spacing:.05em;padding:2px 6px;border-radius:3px}
.badge.bo{background:var(--grd);color:var(--gr);border:1px solid #00d68f44}
.badge.bd{background:var(--red);color:var(--re);border:1px solid #ff475740}
.badge.sw{background:var(--yed);color:var(--ye);border:1px solid #ffd32a30}
.badge.none{background:var(--s2);color:var(--mu);border:1px solid var(--bd)}
/* TF dots */
.tfdots{display:flex;gap:3px}
.tfd{width:20px;height:20px;border-radius:3px;font-family:var(--mono);font-size:7px;
  font-weight:700;display:flex;align-items:center;justify-content:center}
.tfd.bo{background:var(--grd);color:var(--gr)}.tfd.bd{background:var(--red);color:var(--re)}
.tfd.sw{background:var(--yed);color:var(--ye)}.tfd.none{background:var(--s2);color:var(--mu)}
/* TABLE */
table{width:100%;border-collapse:collapse}
th{font-family:var(--mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mu);text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #13161e;vertical-align:middle}
tr:hover td{background:var(--s2)}
tr:last-child td{border-bottom:none}
.sym{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--tx)}
.sec-tag{font-size:9px;color:var(--mu);display:block}
.mono{font-family:var(--mono);font-size:11px}
.gr{color:var(--gr)}.re{color:var(--re)}.ye{color:var(--ye)}.mu{color:var(--mu)}
/* SCORE BAR */
.sbar-wrap{width:55px}
.sbar-num{font-family:var(--mono);font-size:10px;margin-bottom:2px}
.sbar-bg{background:var(--s2);border-radius:2px;height:4px;overflow:hidden}
.sbar-fill{height:4px;border-radius:2px}
/* RSI PILL */
.rsi-p{display:inline-block;font-family:var(--mono);font-size:10px;padding:1px 5px;border-radius:10px}
.rsi-ob{background:#ff475718;color:var(--re)}.rsi-bull{background:var(--grd);color:var(--gr)}
.rsi-mid{background:var(--yed);color:var(--ye)}.rsi-os{background:var(--bld);color:var(--bl)}
/* SECTOR HEATMAP */
.sec-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px}
.sec-cell{background:var(--s2);border:1px solid var(--bd);border-radius:4px;padding:10px}
.sec-name{font-size:9px;font-weight:600;color:var(--mu);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}
.sec-bars{display:flex;gap:2px;height:22px;align-items:flex-end;margin-bottom:4px}
.sec-bar{border-radius:2px 2px 0 0;flex:0 0 14px;min-height:2px}
.sec-counts{display:flex;gap:6px;font-family:var(--mono);font-size:9px}
/* DETAIL CARD */
.det-card{background:var(--s1);border:1px solid var(--bd);border-radius:6px;
  margin-bottom:12px;overflow:hidden}
.det-hdr{padding:12px 16px;border-bottom:1px solid var(--bd);
  display:flex;align-items:center;justify-content:space-between;
  background:var(--s2)}
.det-sym{font-family:var(--mono);font-size:16px;font-weight:700}
.det-meta{font-size:11px;color:var(--mu)}
.det-body{padding:14px 16px}
/* TF BLOCK */
.tf-block{border:1px solid var(--bd);border-radius:4px;margin-bottom:8px;overflow:hidden}
.tf-block-hdr{padding:8px 12px;background:var(--s2);display:flex;align-items:center;gap:10px;
  border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:11px;font-weight:700}
.tf-block-body{padding:10px 12px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.tf-kv{font-size:11px}
.tf-kv-lbl{color:var(--mu);font-size:9px;margin-bottom:1px;text-transform:uppercase;letter-spacing:.06em}
.tf-kv-val{font-family:var(--mono);font-size:11px}
/* REASON TAG */
.reason{font-size:10px;color:var(--mu);font-style:italic;padding:3px 0 6px;border-bottom:1px solid #13161e;margin-bottom:8px}
/* TARGETS ROW */
.tgt-row{display:flex;gap:12px;flex-wrap:wrap;padding:8px 0;border-top:1px solid #13161e;margin-top:6px}
.tgt-box{background:var(--s2);border-radius:4px;padding:6px 10px;font-family:var(--mono);font-size:11px}
.tgt-lbl{font-size:8px;color:var(--mu);display:block;margin-bottom:2px;text-transform:uppercase;letter-spacing:.08em}
/* GAP PREDICTION */
.gap-bar-wrap{background:var(--s2);border-radius:3px;height:8px;overflow:hidden;flex:1}
.gap-bar-fill{height:8px;border-radius:3px}
.gap-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.gap-score{font-family:var(--mono);font-size:22px;font-weight:700;min-width:50px}
.gap-bias{font-family:var(--mono);font-size:13px;font-weight:700}
.gap-facts{margin-top:6px}
.gap-fact{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px}
.gap-pts{font-family:var(--mono);font-size:11px;font-weight:700;min-width:40px}
/* STOCK SUMMARY */
.verdict-banner{padding:10px 14px;border-radius:4px;font-family:var(--mono);
  font-size:14px;font-weight:700;margin-bottom:10px}
.verdict-buy{background:var(--grd);color:var(--gr);border:1px solid #00d68f44}
.verdict-sell{background:var(--red);color:var(--re);border:1px solid #ff475740}
.verdict-hold{background:var(--yed);color:var(--ye);border:1px solid #ffd32a30}
.reason-list{margin-bottom:10px}
.reason-item{display:flex;align-items:flex-start;gap:8px;padding:4px 0;font-size:11px;
  border-bottom:1px solid #13161e}
.reason-item:last-child{border-bottom:none}
.reason-icon{font-size:12px;flex-shrink:0;margin-top:1px}
/* CANDLE CHIPS */
.chip{display:inline-block;background:var(--s2);border:1px solid var(--bd);border-radius:3px;
  font-family:var(--mono);font-size:9px;padding:2px 5px;margin:2px;color:var(--bl)}
/* CONFLUENCE */
.conf-str{font-family:var(--mono);font-size:11px;font-weight:700}
/* NAV */
.nav{position:fixed;top:52px;right:16px;background:var(--s1);border:1px solid var(--bd);
  border-radius:6px;padding:10px;max-height:80vh;overflow-y:auto;font-size:10px;width:140px;
  z-index:99}
.nav-title{font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--mu);margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid var(--bd)}
.nav a{display:block;padding:3px 0;color:var(--mu);font-family:var(--mono);font-size:9px}
.nav a:hover,.nav a.bo{color:var(--gr)}.nav a.bd{color:var(--re)}.nav a.sw{color:var(--ye)}
/* FOOTER */
.ftr{border-top:1px solid var(--bd);padding:12px 20px;font-family:var(--mono);
  font-size:9px;color:var(--mu);display:flex;justify-content:space-between}
@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.g2,.g3{grid-template-columns:1fr}.nav{display:none}.tf-block-body{grid-template-columns:1fr 1fr}}
@media(max-width:500px){.cards{grid-template-columns:1fr}}
</style>
</head>
<body>
""")

    # ── HEADER ────────────────────────────────────────────────
    w('<div class="hdr">')
    w('  <div class="hdr-logo">Master Scanner <b>v8.0 PRO</b> — NSE Nifty 50</div>')
    w('  <div class="hdr-ts">Generated: ' + gen + ' · ' + str(len(data)) + ' Symbols · ' + ', '.join(tfs) + '</div>')
    w('</div>')

    # ── SIDE NAV ─────────────────────────────────────────────
    w('<div class="nav"><div class="nav-title">Symbols</div>')
    for sym, e in ranked:
        sig = as_dict(e.get("DAY")).get("signal", "NONE")
        w('<a href="#sym-' + sym + '" class="' + sc(sig) + '">' + sym + '</a>')
    w('</div>')

    w('<div class="wrap">')

    # ── STAT CARDS ────────────────────────────────────────────
    w('<div class="cards">')
    w('<div class="card gr"><div class="card-lbl">Breakout (Day)</div><div class="card-val">' + str(bo_d) + '</div><div class="card-sub">' + str(round(bo_d/max(len(data),1)*100)) + '% of universe</div></div>')
    w('<div class="card re"><div class="card-lbl">Breakdown (Day)</div><div class="card-val">' + str(bd_d) + '</div><div class="card-sub">' + str(round(bd_d/max(len(data),1)*100)) + '% of universe</div></div>')
    w('<div class="card ye"><div class="card-lbl">All-TF Breakout</div><div class="card-val">' + str(len(all_bo_syms)) + '</div><div class="card-sub">' + ', '.join(tfs) + ' aligned</div></div>')
    w('<div class="card bl"><div class="card-lbl">All-TF Breakdown</div><div class="card-val">' + str(len(all_bd_syms)) + '</div><div class="card-sub">' + (', '.join(all_bd_syms[:3]) or 'None') + '</div></div>')
    w('</div>')

    # ── SECTOR HEATMAP + BEST SETUPS ─────────────────────────
    w('<div class="g2">')

    # Sector heatmap
    w('<div class="sec"><div class="sec-title">Sector Heatmap <span>DAY signal</span></div>')
    w('<div class="sec-grid">')
    for sec in sorted(sectors, key=lambda s: -sectors[s]["BO"]):
        c = sectors[sec]
        tot = max(c["BO"]+c["BD"]+c["SW"]+c["N"], 1)
        hbo = max(int(c["BO"]/tot*40), 2 if c["BO"] else 0)
        hbd = max(int(c["BD"]/tot*40), 2 if c["BD"] else 0)
        hsw = max(int(c["SW"]/tot*40), 2 if c["SW"] else 0)
        w('<div class="sec-cell"><div class="sec-name">' + sec + '</div>')
        w('<div class="sec-bars">')
        if c["BO"]: w('<div class="sec-bar" style="height:' + str(hbo) + 'px;background:var(--gr);opacity:.85"></div>')
        if c["BD"]: w('<div class="sec-bar" style="height:' + str(hbd) + 'px;background:var(--re);opacity:.85"></div>')
        if c["SW"]: w('<div class="sec-bar" style="height:' + str(hsw) + 'px;background:var(--ye);opacity:.7"></div>')
        w('</div><div class="sec-counts">')
        if c["BO"]: w('<span class="gr">▲' + str(c["BO"]) + '</span>')
        if c["BD"]: w('<span class="re">▼' + str(c["BD"]) + '</span>')
        if c["SW"]: w('<span class="ye">~' + str(c["SW"]) + '</span>')
        w('</div></div>')
    w('</div></div>')

    # All-TF Breakout table
    w('<div class="sec"><div class="sec-title">All-TF Breakout Setups <span>' + '+'.join(tfs) + '</span></div>')
    w('<div style="overflow-x:auto"><table><thead><tr>')
    for h in ["Symbol","Score","Price","RSI","RVol","R:R","Candle"]:
        w('<th>' + h + '</th>')
    w('</tr></thead><tbody>')
    allbo_data = [(sym, data[sym]) for sym in all_bo_syms if sym in data]
    allbo_data.sort(key=lambda x: safe(as_dict(x[1].get("DAY")).get("composite_score", 0)), reverse=True)
    for sym, e in allbo_data:
        de = as_dict(e.get("DAY"))
        cs = int(safe(de.get("composite_score",0)))
        sc_col = "var(--gr)" if cs >= 65 else ("var(--ye)" if cs >= 40 else "var(--re)")
        rsi = safe(de.get("rsi",0))
        rv  = safe(de.get("rel_vol",0))
        rr  = safe(de.get("risk_reward",0))
        cps = as_list(de.get("candle_patterns"))
        rsi_cls = "rsi-ob" if rsi>70 else ("rsi-bull" if rsi>55 else ("rsi-mid" if rsi>40 else "rsi-os"))
        rr_col = "var(--gr)" if rr>=1 else ("var(--ye)" if rr>=0.5 else "var(--re)")
        chips = "".join('<span class="chip">' + esc(p) + '</span>' for p in cps) if cps else '<span class="mu">—</span>'
        w('<tr><td><a href="#sym-' + esc(sym) + '"><span class="sym">' + esc(sym) + '</span></a><span class="sec-tag">' + esc(SECTOR_MAP.get(sym,"")) + '</span></td>')
        w('<td><div class="sbar-num" style="color:' + sc_col + '">' + str(cs) + '</div><div class="sbar-wrap"><div class="sbar-bg"><div class="sbar-fill" style="width:' + str(cs) + '%;background:' + sc_col + '"></div></div></div></td>')
        w('<td class="mono">' + fmt_p(de.get("price",0)) + '</td>')
        w('<td><span class="rsi-p ' + rsi_cls + '">' + str(rsi) + '</span></td>')
        rv_col = "var(--gr)" if rv>=1.5 else ("var(--ye)" if rv>=1 else "var(--mu)")
        w('<td class="mono" style="color:' + rv_col + '">' + str(rv) + '×</td>')
        w('<td class="mono" style="color:' + rr_col + '">' + str(rr) + ':1</td>')
        w('<td>' + chips + '</td></tr>')
    w('</tbody></table></div></div>')
    w('</div>')  # g2

    # ── DASHBOARD TABLE ──────────────────────────────────────
    w('<div class="sec"><div class="sec-title">All Symbols — DAY Timeframe <span>Sorted by Composite Score</span></div>')
    w('<div style="overflow-x:auto"><table><thead><tr>')
    for h in ["Symbol","Signal"] + [tf[:4] for tf in tfs] + ["Score","Price","RSI","RVol","R:R","EMA","Candle"]:
        w('<th>' + h + '</th>')
    w('</tr></thead><tbody>')
    for sym, e in ranked:
        de  = as_dict(e.get("DAY"))
        sig = de.get("signal","NONE")
        cs  = int(safe(de.get("composite_score",0)))
        sc_col = "var(--gr)" if cs>=65 else ("var(--ye)" if cs>=40 else "var(--re)")
        rsi = safe(de.get("rsi",0))
        rv  = safe(de.get("rel_vol",0))
        rr  = safe(de.get("risk_reward",0))
        ema = de.get("ema_alignment","")
        cps = as_list(de.get("candle_patterns"))
        rsi_cls = "rsi-ob" if rsi>70 else ("rsi-bull" if rsi>55 else ("rsi-mid" if rsi>40 else "rsi-os"))
        rr_col = "var(--gr)" if rr>=1 else ("var(--ye)" if rr>=0.5 else "var(--re)")
        rv_col = "var(--gr)" if rv>=1.5 else ("var(--ye)" if rv>=1 else "var(--mu)")
        ema_col = "var(--gr)" if "FULL BULL" in ema else ("var(--re)" if "FULL BEAR" in ema else ("var(--gr)" if "BULL" in ema else ("var(--re)" if "BEAR" in ema else "var(--mu)")))
        chips = "".join('<span class="chip">' + esc(p) + '</span>' for p in cps)
        w('<tr><td><a href="#sym-' + esc(sym) + '"><span class="sym">' + esc(sym) + '</span></a><span class="sec-tag">' + esc(SECTOR_MAP.get(sym,"")) + '</span></td>')
        w('<td><span class="badge ' + sc(sig) + '">' + SIGNAL_SHORT.get(sig,"--") + '</span></td>')
        for tf in tfs:
            s2 = as_dict(e.get(tf)).get("signal","NONE")
            w('<td><span class="badge ' + sc(s2) + '">' + SIGNAL_SHORT.get(s2,"--") + '</span></td>')
        w('<td><div class="sbar-num" style="color:' + sc_col + '">' + str(cs) + '</div><div class="sbar-wrap"><div class="sbar-bg"><div class="sbar-fill" style="width:' + str(cs) + '%;background:' + sc_col + '"></div></div></div></td>')
        w('<td class="mono">' + fmt_p(de.get("price",0)) + '</td>')
        w('<td><span class="rsi-p ' + rsi_cls + '">' + str(rsi) + '</span></td>')
        w('<td class="mono" style="color:' + rv_col + '">' + str(rv) + '×</td>')
        w('<td class="mono" style="color:' + rr_col + '">' + str(rr) + ':1</td>')
        w('<td style="font-size:10px;color:' + ema_col + '">' + esc(ema.replace("FULL BULL STACK","FULL BULL").replace("FULL BEAR STACK","FULL BEAR")) + '</td>')
        w('<td>' + chips + '</td></tr>')
    w('</tbody></table></div></div>')

    # ── PER-SYMBOL DETAIL CARDS ───────────────────────────────
    w('<h2 style="font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mu);margin:20px 0 10px">Symbol Detail Cards</h2>')

    for sym, e in ranked:
        sec     = SECTOR_MAP.get(sym,"")
        conf    = confluence_score(e, tfs)
        conf_lbl_map = {3:"STRONG BULL",2:"BULL",1:"WEAK BULL",0:"NEUTRAL",-1:"WEAK BEAR",-2:"BEAR"}
        conf_str = conf_lbl_map.get(conf, ("STRONG BULL" if conf>3 else "STRONG BEAR"))
        conf_col = "var(--gr)" if conf>=2 else ("var(--re)" if conf<=-2 else "var(--ye)")
        day_e   = as_dict(e.get("DAY"))
        price   = safe(day_e.get("price",0))

        w('<div class="det-card" id="sym-' + sym + '">')
        w('<div class="det-hdr">')
        w('  <div>')
        w('    <div class="det-sym">' + esc(sym) + ' <span style="font-size:13px;color:var(--mu);font-weight:400">[' + esc(sec) + ']</span></div>')
        w('    <div class="det-meta">' + fmt_p(price) + ' &nbsp;·&nbsp; Confluence: <span style="color:' + conf_col + ';font-weight:700">' + conf_str + ' (' + ('+' if conf>=0 else '') + str(conf) + ')</span></div>')
        w('  </div>')
        w('  <div class="tfdots">')
        for tf in list(TF_CONFIG.keys()):
            if tf in e:
                s2 = as_dict(e[tf]).get("signal", "NONE")
                w('    <div class="tfd ' + sc(s2) + '" title="' + esc(tf + ': ' + s2) + '">' + tf[:1] + ('M' if tf=="15MIN" else tf[1:2] if tf not in ["5MIN","1HR","DAY"] else '') + '</div>')
        w('  </div>')
        w('</div>')
        w('<div class="det-body">')

        # Signal snapshot strip
        tf_order = [tf for tf in list(TF_CONFIG.keys()) if tf in e]
        w('<div style="margin-bottom:14px">')
        w('<table style="width:100%"><thead><tr>')
        for h in ["TF","Signal","Score","RSI","RVol","Price","Date"]:
            w('<th>' + h + '</th>')
        w('</tr></thead><tbody>')
        for tf in tf_order:
            te  = as_dict(e[tf])
            sig = te.get("signal","NONE")
            cs  = int(safe(te.get("composite_score",0)))
            sc_col = "var(--gr)" if cs>=65 else ("var(--ye)" if cs>=40 else ("var(--re)" if cs>0 else "var(--mu)"))
            rsi = safe(te.get("rsi",0))
            rv  = safe(te.get("rel_vol",0))
            pr  = safe(te.get("price",0))
            cd  = te.get("candle_date","—")
            chg = te.get("signal_changed",False)
            chg_tag = ' <span style="color:var(--ye);font-size:9px">⚡CHANGED</span>' if chg else ""
            rsi_cls = "rsi-ob" if rsi>70 else ("rsi-bull" if rsi>55 else ("rsi-mid" if rsi>40 else "rsi-os"))
            rv_col = "var(--gr)" if rv>=1.5 else ("var(--ye)" if rv>=1 else "var(--mu)")
            w('<tr><td class="mono" style="font-weight:700">' + tf + '</td>')
            w('<td><span class="badge ' + sc(sig) + '">' + SIGNAL_SHORT.get(sig,"--") + '</span>' + chg_tag + '</td>')
            w('<td><div style="color:' + sc_col + ';font-family:var(--mono);font-size:11px">' + str(cs) + '</div><div class="sbar-bg" style="width:45px;margin-top:2px"><div class="sbar-fill" style="width:' + str(cs) + '%;background:' + sc_col + '"></div></div></td>')
            w('<td><span class="rsi-p ' + rsi_cls + '">' + str(rsi) + '</span></td>')
            w('<td class="mono" style="color:' + rv_col + '">' + str(rv) + '×</td>')
            w('<td class="mono">' + fmt_p(pr) + '</td>')
            w('<td style="font-size:10px;color:var(--mu)">' + esc(cd) + '</td></tr>')
        w('</tbody></table></div>')

        # Per-TF detail blocks
        for tf in tf_order:
            te  = as_dict(e[tf])
            sig = te.get("signal","NONE")
            if sig == "NONE": continue
            atr   = safe(te.get("atr",0))
            atrp  = safe(te.get("atr_pct",0))
            cs    = int(safe(te.get("composite_score",0)))
            rsi   = safe(te.get("rsi",0))
            rv    = safe(te.get("rel_vol",0))
            ts    = safe(te.get("trend_strength",0))
            ema   = te.get("ema_alignment","")
            st    = int(safe(te.get("st_direction",0)))
            mc    = te.get("macd_cross","")
            rr    = safe(te.get("risk_reward",0))
            tgt   = as_dict(te.get("targets"))
            supp  = as_list(te.get("support"))
            res   = as_list(te.get("resistance"))
            cps   = as_list(te.get("candle_patterns"))
            fib   = as_dict(te.get("fibonacci"))
            w52   = as_dict(te.get("52w"))
            div_  = as_dict(te.get("rsi_divergence"))
            reason= te.get("reason","")
            pr    = safe(te.get("price",0))
            ema_col = "var(--gr)" if "BULL" in ema else ("var(--re)" if "BEAR" in ema else "var(--mu)")
            st_txt  = ("↑ BULL" if st==1 else ("↓ BEAR" if st==-1 else "—"))
            st_col  = "var(--gr)" if st==1 else ("var(--re)" if st==-1 else "var(--mu)")
            mc_col  = "var(--gr)" if mc=="BULL_CROSS" else ("var(--re)" if mc=="BEAR_CROSS" else "var(--mu)")
            ts_col  = "var(--gr)" if ts>=0 else "var(--re)"
            sc_col  = "var(--gr)" if cs>=65 else ("var(--ye)" if cs>=40 else "var(--re)")

            w('<div class="tf-block"><div class="tf-block-hdr">')
            w('<span style="color:var(--mu);font-size:9px;font-weight:400">──</span>')
            w('<span style="color:var(--ye)">' + tf + '</span>')
            w('<span class="badge ' + sc(sig) + '">' + sig + '</span>')
            w('<span style="color:' + sc_col + ';font-size:11px">' + str(cs) + '/100</span>')
            w('<span style="color:var(--mu);font-size:10px;font-weight:400;margin-left:auto">R:R <span style="color:' + ("var(--gr)" if rr>=1 else "var(--ye)") + '">' + str(rr) + ':1</span></span>')
            w('</div>')
            if reason:
                w('<div class="reason" style="padding:4px 12px">' + esc(reason.replace("|"," · ")) + '</div>')
            w('<div class="tf-block-body">')
            for lbl,val,col in [
                ("ATR", "₹"+str(round(atr,2))+" ("+str(atrp)+"%)", "var(--tx)"),
                ("RSI(14)", str(rsi), "var(--gr)" if rsi>65 else ("var(--re)" if rsi<35 else "var(--ye)")),
                ("Rel Vol", str(rv)+"×", "var(--gr)" if rv>=1.5 else ("var(--ye)" if rv>=1 else "var(--mu)")),
                ("EMA Align", esc(ema.replace("FULL BULL STACK","FULL BULL").replace("FULL BEAR STACK","FULL BEAR")), ema_col),
                ("SuperTrend", st_txt, st_col),
                ("MACD Cross", mc if mc else "—", mc_col),
                ("Trend Str", str(int(ts)), ts_col),
                ("Comp Score", str(cs)+"/100", sc_col),
            ]:
                w('<div class="tf-kv"><div class="tf-kv-lbl">' + lbl + '</div><div class="tf-kv-val" style="color:' + col + '">' + str(val) + '</div></div>')
            w('</div>')

            # S/R + targets
            if supp or res or tgt:
                w('<div style="padding:8px 12px;border-top:1px solid var(--bd)">')
                if supp: w('<div style="font-size:11px;margin-bottom:4px">Support: ' + '&nbsp;'.join('<span class="mono gr">₹'+str(s)+'</span>' for s in supp) + '</div>')
                if res:  w('<div style="font-size:11px;margin-bottom:4px">Resistance: ' + '&nbsp;'.join('<span class="mono re">₹'+str(r)+'</span>' for r in res) + '</div>')
                if tgt:
                    t1 = tgt.get("target1",""); t2 = tgt.get("target2",""); sl = tgt.get("stop","")
                    w('<div class="tgt-row">')
                    w('<div class="tgt-box"><span class="tgt-lbl">Entry</span><span class="mono">' + fmt_p(pr) + '</span></div>')
                    if t1: w('<div class="tgt-box"><span class="tgt-lbl">Target 1</span><span class="mono gr">' + fmt_p(t1) + '</span></div>')
                    if t2: w('<div class="tgt-box"><span class="tgt-lbl">Target 2</span><span class="mono gr">' + fmt_p(t2) + '</span></div>')
                    if sl: w('<div class="tgt-box"><span class="tgt-lbl">Stop Loss</span><span class="mono re">' + fmt_p(sl) + '</span></div>')
                    w('</div>')
                w('</div>')

            # Candle + Fib + 52W + divergence
            extras = []
            if cps:
                bull_cp = [p for p in cps if p in CANDLE_BULL]
                bear_cp = [p for p in cps if p in CANDLE_BEAR]
                if bull_cp: extras.append('<span style="color:var(--gr);font-size:10px">Candle ↑</span> ' + "".join('<span class="chip">'+p+'</span>' for p in bull_cp))
                if bear_cp: extras.append('<span style="color:var(--re);font-size:10px">Candle ↓</span> ' + "".join('<span class="chip">'+p+'</span>' for p in bear_cp))
            if fib:
                sw_h = fib.get("sw_high",""); sw_l = fib.get("sw_low","")
                f382 = fib.get("fib_38.2",""); f500 = fib.get("fib_50",""); f618 = fib.get("fib_61.8","")
                fib_line = '<span style="font-size:10px;color:var(--mu)">Fib:</span>'
                for lbl,lvl in [("38.2%",f382),("50%",f500),("61.8%",f618)]:
                    if lvl:
                        near = pr and abs(float(lvl)-pr)/max(pr,0.01) < 0.025
                        fib_line += ' <span class="mono" style="color:' + ("var(--ye)" if near else "var(--mu)") + '">' + lbl + '=₹' + str(lvl) + '</span>'
                if sw_h: fib_line += ' <span class="mono mu">H₹' + str(sw_h) + ' L₹' + str(sw_l) + '</span>'
                extras.append(fib_line)
            if w52:
                ph = safe(w52.get("pct_from_high",0)); pl = safe(w52.get("pct_from_low",0))
                ph_col = "var(--gr)" if ph>=-5 else ("var(--ye)" if ph>=-15 else "var(--re)")
                extras.append('<span style="font-size:10px;color:var(--mu)">52W:</span> <span class="mono" style="color:' + ph_col + '">High ₹' + str(w52.get("high_52w","")) + ' (' + str(ph) + '%)</span> <span class="mono mu">Low ₹' + str(w52.get("low_52w","")) + ' (+' + str(pl) + '%)</span>')
            div_items = []
            if div_.get("regular_bull"): div_items.append('<span style="color:var(--gr)">Reg.Bull▲</span>')
            if div_.get("hidden_bull"):  div_items.append('<span style="color:var(--gr)">Hid.Bull▲</span>')
            if div_.get("regular_bear"): div_items.append('<span style="color:var(--re)">Reg.Bear▼</span>')
            if div_.get("hidden_bear"):  div_items.append('<span style="color:var(--re)">Hid.Bear▼</span>')
            if div_items: extras.append('<span style="font-size:10px;color:var(--mu)">RSI Div:</span> ' + " ".join(div_items))
            if te.get("retest_bo"): extras.append('<span style="color:var(--gr);font-weight:700">★ RETEST BREAKOUT R1=₹' + str(te.get("retest_bo_level","")) + '</span>')
            if te.get("retest_bd"): extras.append('<span style="color:var(--re);font-weight:700">★ RETEST BREAKDOWN S1=₹' + str(te.get("retest_bd_level","")) + '</span>')

            if extras:
                w('<div style="padding:6px 12px;border-top:1px solid var(--bd);display:flex;flex-wrap:wrap;gap:10px">')
                for ex in extras:
                    w('<div style="font-size:11px">' + ex + '</div>')
                w('</div>')
            w('</div>')  # tf-block

        # Next-day gap prediction
        gap_score, gap_bias, gap_facts = calc_next_day_gap_score(e)
        gb_col = "var(--gr)" if gap_bias=="GAP_UP" else ("var(--re)" if gap_bias=="GAP_DOWN" else "var(--ye)")
        gb_str = ("▲ GAP UP LIKELY" if gap_bias=="GAP_UP" else ("▼ GAP DOWN LIKELY" if gap_bias=="GAP_DOWN" else "— NEUTRAL / WAIT"))
        bar_pct = gap_score
        bar_col = "var(--gr)" if gap_score>=62 else ("var(--re)" if gap_score<=38 else "var(--ye)")

        w('<div style="margin-top:12px"><div class="sec-title">Next Day Gap Prediction</div>')
        w('<div class="gap-row">')
        w('<div class="gap-score" style="color:' + gb_col + '">' + str(gap_score) + '%</div>')
        w('<div class="gap-bar-wrap"><div class="gap-bar-fill" style="width:' + str(bar_pct) + '%;background:' + bar_col + '"></div></div>')
        w('<div class="gap-bias" style="color:' + gb_col + '">' + gb_str + '</div>')
        w('</div><div class="gap-facts">')
        for lbl, pts in sorted(gap_facts, key=lambda x: -abs(x[1])):
            fc = "var(--gr)" if pts>0 else ("var(--re)" if pts<0 else "var(--mu)")
            sign = "+" if pts>=0 else ""
            w('<div class="gap-fact"><span class="gap-pts" style="color:' + fc + '">' + sign + str(pts) + 'pts</span><span>' + esc(lbl) + '</span></div>')
        w('</div></div>')

        # Stock summary (buy/sell reasons)
        s = generate_stock_summary(sym, e, tfs)
        verdict = s["verdict"]
        vc = "verdict-buy" if verdict=="BUY" else ("verdict-sell" if verdict=="SELL" else "verdict-hold")
        w('<div style="margin-top:12px"><div class="sec-title">Stock Summary — Why Buy / Why Sell</div>')
        w('<div class="verdict-banner ' + vc + '">')
        w('  VERDICT: ' + verdict + ' &nbsp;·&nbsp; ' + s["confidence"] + ' CONFIDENCE')
        w('</div>')
        w('<div style="font-size:12px;color:var(--mu);margin-bottom:8px">' + esc(s["headline"]) + '</div>')
        if s["buy_reasons"]:
            w('<div class="reason-list">')
            for r in s["buy_reasons"]:
                w('<div class="reason-item"><span class="reason-icon" style="color:var(--gr)">✔</span><span>' + esc(r) + '</span></div>')
            w('</div>')
        if s["sell_reasons"]:
            w('<div class="reason-list">')
            for r in s["sell_reasons"]:
                w('<div class="reason-item"><span class="reason-icon" style="color:var(--re)">✘</span><span>' + esc(r) + '</span></div>')
            w('</div>')
        if s["cautions"]:
            w('<div class="reason-list">')
            for r in s["cautions"]:
                w('<div class="reason-item"><span class="reason-icon" style="color:var(--ye)">⚠</span><span>' + esc(r) + '</span></div>')
            w('</div>')
        w('</div>')  # stock summary

        w('</div></div>')  # det-body + det-card

    # ── FOOTER ────────────────────────────────────────────────
    w('</div>')  # wrap
    w('<div class="ftr"><span>Master Scanner Pro v8.0 · NSE Equity · Upstox Data · Generated ' + gen + '</span><span>For educational purposes only. Not financial advice.</span></div>')
    w('</body></html>')

    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(H))
    cprint("  ✓ HTML report saved → " + fname, C.GREEN, bold=True)
    cprint("  Open in any web browser (Chrome / Firefox / Safari).", C.DIM)
    input("  Press ENTER...")


# ─────────────────────────────────────────────────────────────
#  SYMBOL MANAGEMENT
# ─────────────────────────────────────────────────────────────

# NSE/BSE equity & index keys look like "NSE_EQ|INE002A01018".
# Validating up front turns a silent "no data for 6 timeframes" failure into
# an immediate, actionable message.
_SYMBOL_KEY_RE = re.compile(r"^[A-Z]{3,10}_[A-Z]{2,6}\|[A-Za-z0-9][A-Za-z0-9 ]{4,25}$")


def add_custom_symbol():
    print("\n  Add Custom Symbol")
    cprint("  Key format : NSE_EQ|ISIN  (find at upstox.com/developer)", C.DIM)
    key  = input("  Instrument key : ").strip()
    if key and not _SYMBOL_KEY_RE.match(key):
        cprint("  ✗ Invalid key '" + key + "'.", C.RED)
        cprint("    Expected e.g. NSE_EQ|INE002A01018 (EXCHANGE_SEGMENT|TOKEN),",
               C.DIM)
        cprint("    or NSE_INDEX|Nifty 50 / NSE_INDEX|India VIX.", C.DIM)
        input("  Press ENTER...")
        return
    name = input("  Display name   : ").strip().upper()
    sec  = input("  Sector         : ").strip().upper() or "OTHER"
    if key and name:
        SYMBOL_MAP[key]      = name
        SECTOR_MAP[name]     = sec
        cprint("  ✓ Added: " + name + " [" + sec + "]", C.GREEN)
    else:
        cprint("  Cancelled.", C.YELLOW)
    input("  Press ENTER...")

def remove_custom_symbol():
    print("\n  Current symbols: " + ", ".join(SYMBOL_MAP.values()))
    name = input("  Name to remove: ").strip().upper()
    for k, v in list(SYMBOL_MAP.items()):
        if v == name:
            del SYMBOL_MAP[k]
            SECTOR_MAP.pop(name, None)
            cprint("  ✓ Removed: " + name, C.GREEN)
            input("  Press ENTER...")
            return
    cprint("  Not found.", C.RED)
    input("  Press ENTER...")

def pick_symbol(data, prompt="  Symbol: "):
    sym = input(prompt).strip().upper()
    if sym not in data:
        cprint("  Not found: " + sym, C.RED)
        input("  Press ENTER...")
        return None
    return sym

def edit_note(sym, data):
    header("EDIT NOTE: " + sym)
    # Load existing note from the persistent notes table
    cur_note = load_note(sym)
    n = input("  Note for " + sym + " (ENTER to keep '" + cur_note + "'): ").strip()
    if n:
        # Persist to dedicated notes table (survives rescans)
        save_note(sym, n)
        # Also update in-memory scan_data so display reflects the change immediately
        for tf in list(TF_CONFIG.keys()):
            if isinstance(as_dict(data.get(sym)).get(tf), dict):
                data[sym][tf]["note"] = n
        save_data(data)
        cprint("  ✓ Note saved.", C.GREEN)
    else:
        cprint("  Note unchanged.", C.DIM)
    input("  Press ENTER...")



# ─────────────────────────────────────────────────────────────
#  NEW: NEXT DAY GAP PREDICTOR  (calc + view)
# ─────────────────────────────────────────────────────────────

def calc_next_day_gap_score(sym_data):
    """
    Score a symbol 0-100 for next-day gap-up probability using
    end-of-day signals from the last DAY candle.

    Factors (positive = gap-up bias, negative = gap-down bias):
      Close position in day range    ±20  (top/bottom 20% of H-L)
      Multi-TF signal confluence     ±18  (all BO = +18, all BD = -18)
      RSI zone                       ±15  (>65 bull / <35 bear)
      Volume / interest              +12  (rel_vol > 1.5 = strong close)
      Candle pattern                 ±10  (bull/bear reversal/continuation)
      EMA alignment                  ±10  (FULL BULL / BEAR STACK)
      52W High proximity             + 8  (within 3% = magnetic)
      Trend strength                 ± 7  (from trend_strength field)
      ─────────────────────────────────
      Raw range approx    -80 to +80  → mapped to 0-100

    Returns:
      score      0-100
      bias       "GAP_UP" / "NEUTRAL" / "GAP_DOWN"
      factors    list of (label, pts) for display
    """
    e      = as_dict(sym_data.get("DAY"))
    price  = safe_float(e.get("price",  0))
    if not price:
        return 50, "NEUTRAL", []

    raw   = 0.0
    facts = []

    # ── 1. Close position in the period's range ──────────────
    # Bug fix: this factor claimed to measure where the close sat inside the
    # day's high-low range, but it actually read trend_strength — a different
    # quantity that factor 8 already scores. So the label lied and trend
    # strength was double-counted, contributing up to ±27 of the ±80 raw
    # range (a third of the whole score) instead of the documented ±7.
    # Now uses the real OHLC, with the old proxy only as a fallback for rows
    # written by a scanner that predates the day_high/day_low keys.
    ts = safe_float(e.get("trend_strength", 0))
    hi = safe_float(e.get("day_high", 0))
    lo = safe_float(e.get("day_low",  0))
    if hi > lo > 0:
        pos = (price - lo) / (hi - lo)          # 0 = at the low, 1 = at the high
        if   pos >= 0.8: pts =  20; label = "Close near High (top 20% of range)"
        elif pos >= 0.6: pts =  10; label = "Close in upper range"
        elif pos >= 0.4: pts =   3; label = "Close mid-range"
        elif pos >= 0.2: pts =  -8; label = "Close in lower range"
        else:            pts = -20; label = "Close near Low (bottom 20%)"
    else:
        if   ts >= 70:  pts = 20;  label = "Close near High (strong)"
        elif ts >= 40:  pts = 10;  label = "Close in upper range"
        elif ts >= 0:   pts =  3;  label = "Close mid-range"
        elif ts >= -40: pts = -8;  label = "Close in lower range"
        else:           pts = -20; label = "Close near Low (weak)"
    raw += pts; facts.append((label, pts))

    # ── 2. Multi-TF signal confluence ────────────────────────
    # Swing TF confluence (DAY/WEEK/MONTH) carries the most weight for next-day gaps.
    tfs_all   = ["DAY", "WEEK", "MONTH"]
    conf_raw  = sum({"BREAKOUT":1,"BREAKDOWN":-1}.get(
                    as_dict(sym_data.get(tf)).get("signal","NONE"), 0)
                    for tf in tfs_all)
    pts = conf_raw * 6   # -18..+18
    label = {3:"All TFs BREAKOUT", 2:"2 TFs BREAKOUT", 1:"1 TF BREAKOUT",
             0:"Mixed/Neutral", -1:"1 TF BREAKDOWN", -2:"2 TFs BREAKDOWN",
             -3:"All TFs BREAKDOWN"}.get(conf_raw, "Mixed")
    raw += pts; facts.append(("Confluence: " + label, pts))

    # ── 2b. Intraday TF direction adjustment ─────────────────
    # Bug fix: previously intraday TFs (5MIN/15MIN/1HR) had zero weight.
    # When all intraday TFs are BREAKDOWN while swing TFs are BREAKOUT,
    # the gap score was 87% GAP UP — ignoring a clear near-term bearish signal.
    # Now add a mild ±8 correction based on intraday consensus.
    tfs_intra  = ["5MIN", "15MIN", "1HR"]
    intra_sigs = [as_dict(sym_data.get(tf)).get("signal", "NONE") for tf in tfs_intra
                  if as_dict(sym_data.get(tf)).get("signal", "NONE") != "NONE"]
    if intra_sigs:
        intra_raw = sum({"BREAKOUT":1,"BREAKDOWN":-1}.get(s, 0) for s in intra_sigs)
        # Scale: ±3 intraday TFs → ±8 pts (less influential than swing ±18)
        intra_pts = round(intra_raw / max(len(intra_sigs), 1) * 8)
        if intra_pts != 0:
            i_dir = "bullish" if intra_pts > 0 else "bearish"
            raw += intra_pts
            facts.append(("Intraday (" + "/".join(tfs_intra) + ") " + i_dir, intra_pts))

    # ── 3. RSI zone ──────────────────────────────────────────
    rsi = safe_float(e.get("rsi", 50))
    if   rsi > 70: pts = 15; label = "RSI overbought (" + str(rsi) + ") — momentum"
    elif rsi > 60: pts = 10; label = "RSI bullish zone (" + str(rsi) + ")"
    elif rsi > 50: pts =  4; label = "RSI mildly bullish (" + str(rsi) + ")"
    elif rsi > 40: pts = -4; label = "RSI mildly bearish (" + str(rsi) + ")"
    elif rsi > 30: pts =-10; label = "RSI bearish zone (" + str(rsi) + ")"
    else:          pts =-15; label = "RSI oversold (" + str(rsi) + ") — weak"
    raw += pts; facts.append(("RSI: " + label, pts))

    # ── 4. Volume / interest ─────────────────────────────────
    rv  = safe_float(e.get("rel_vol", 1.0))
    sig = e.get("signal", "NONE")
    if   rv >= 2.0 and sig == "BREAKOUT":  pts = 12; label = "High vol BO (rv=" + str(rv) + "x)"
    elif rv >= 1.5 and sig == "BREAKOUT":  pts =  8; label = "Above-avg vol BO (rv=" + str(rv) + "x)"
    elif rv >= 1.5:                         pts =  5; label = "Volume spike (rv=" + str(rv) + "x)"
    elif rv >= 1.0:                         pts =  2; label = "Normal volume (rv=" + str(rv) + "x)"
    elif rv < 0.6 and sig == "BREAKOUT":   pts = -6; label = "Weak vol BO risk (rv=" + str(rv) + "x)"
    else:                                   pts =  0; label = "Low volume (rv=" + str(rv) + "x)"
    raw += pts; facts.append(("Volume: " + label, pts))

    # ── 5. Candle pattern ─────────────────────────────────────
    cps     = as_list(e.get("candle_patterns"))
    bull_cp = [p for p in cps if p in CANDLE_BULL]
    bear_cp = [p for p in cps if p in CANDLE_BEAR]
    strong_bull = {"THREE_WHITE_SOLDIERS", "MORNING_STAR", "BULL_ENGULF"}
    strong_bear = {"THREE_BLACK_CROWS",    "EVENING_STAR", "BEAR_ENGULF"}
    if   any(p in strong_bull for p in bull_cp): pts = 10; label = "Strong bull candle (" + bull_cp[0] + ")"
    elif bull_cp:                                 pts =  5; label = "Bull candle (" + bull_cp[0] + ")"
    elif any(p in strong_bear for p in bear_cp): pts =-10; label = "Strong bear candle (" + bear_cp[0] + ")"
    elif bear_cp:                                 pts = -5; label = "Bear candle (" + bear_cp[0] + ")"
    else:                                         pts =  0; label = "No pattern"
    raw += pts; facts.append(("Candle: " + label, pts))

    # ── 6. EMA alignment ─────────────────────────────────────
    ema = e.get("ema_alignment", "")
    if   "FULL BULL" in ema: pts = 10; label = "Full Bull EMA Stack"
    elif "BULLISH"   in ema: pts =  5; label = "EMA bullish"
    elif "FULL BEAR" in ema: pts =-10; label = "Full Bear EMA Stack"
    elif "BEARISH"   in ema: pts = -5; label = "EMA bearish"
    else:                     pts =  0; label = "EMA mixed"
    raw += pts; facts.append(("EMA: " + label, pts))

    # ── 7. 52W High proximity ─────────────────────────────────
    w52     = as_dict(e.get("52w"))
    pct_h   = safe_float(w52.get("pct_from_high", -50))
    if   pct_h >= -1:  pts = 8; label = "At 52W High! (+" + str(abs(pct_h)) + "%)"
    elif pct_h >= -3:  pts = 6; label = "Near 52W High (" + str(pct_h) + "%)"
    elif pct_h >= -8:  pts = 3; label = "Within 8% of 52W High"
    elif pct_h <= -30: pts =-3; label = "Far from 52W High (" + str(pct_h) + "%)"
    else:               pts = 0; label = "52W High dist: " + str(pct_h) + "%"
    raw += pts; facts.append(("52W High: " + label, pts))

    # ── 8. Trend strength ─────────────────────────────────────
    if   ts >= 84: pts = 7; label = "Max trend strength (" + str(int(ts)) + ")"
    elif ts >= 56: pts = 4; label = "Strong trend (" + str(int(ts)) + ")"
    elif ts >= 0:  pts = 1; label = "Mild trend (" + str(int(ts)) + ")"
    elif ts >= -56:pts =-4; label = "Weak trend (" + str(int(ts)) + ")"
    else:          pts =-7; label = "Strong bearish trend (" + str(int(ts)) + ")"
    raw += pts; facts.append(("Trend Str: " + label, pts))

    # ── Map raw → 0-100 ──────────────────────────────────────
    # Raw range approx -80 to +80 → clamp → rescale
    clamped = max(-80, min(80, raw))
    # Bug fix: cap at 95 not 100 — 100% implies certainty which is never true.
    # A score of 95 still reads as "very strong gap-up bias" without false precision.
    score   = int(min(95, max(5, (clamped + 80) / 160 * 100)))

    if   score >= 62: bias = "GAP_UP"
    elif score <= 38: bias = "GAP_DOWN"
    else:             bias = "NEUTRAL"

    return score, bias, facts


def next_day_gap_view(data):
    """
    Rank all symbols by next-day gap probability using end-of-day signals.
    Run this AFTER market close (3:30 PM IST) for best results.
    No extra API calls needed — uses cached scan data.
    """
    header("NEXT DAY GAP PREDICTOR  —  Run after 3:30 PM IST")

    if not data:
        cprint("  No data. Run a scan (S) including DAY timeframe first.", C.YELLOW)
        input("  Press ENTER..."); return

    # Check if DAY data exists
    has_day = any("DAY" in entry and entry["DAY"].get("price",0) for entry in data.values())
    if not has_day:
        cprint("  No DAY timeframe data found. Run scan with DAY tf included.", C.YELLOW)
        input("  Press ENTER..."); return

    cprint("  Scoring " + str(len(data)) + " symbols on 8 end-of-day factors...", C.DIM)
    print()

    rows = []
    for sym, sym_data in data.items():
        score, bias, facts = calc_next_day_gap_score(sym_data)
        e      = as_dict(sym_data.get("DAY"))
        price  = e.get("price", 0)
        rsi    = safe_float(e.get("rsi",   0))
        rv     = safe_float(e.get("rel_vol", 0))
        sig    = e.get("signal", "NONE")
        conf   = confluence_score(sym_data, TIMEFRAMES_SWING)
        sector = SECTOR_MAP.get(sym, "")
        rows.append((sym, score, bias, price, rsi, rv, sig, conf, sector, facts))

    rows.sort(key=lambda x: x[1], reverse=True)

    # ── Columns ───────────────────────────────────────────────
    cprint("  " +
           _ljust("SYMBOL",  12) + "  " +
           _rjust("SCORE",    6) + "  " +
           _ljust("BIAS",    10) + "  " +
           _ljust("PROB BAR", 20) + "  " +
           _rjust("PRICE",    9) + "  " +
           _rjust("RSI",      5) + "  " +
           _rjust("RVol",     5) + "  " +
           _rjust("SIG",      4) + "  " +
           "CONFLUENCE", C.DIM)
    div()

    gap_ups   = [r for r in rows if r[2] == "GAP_UP"]
    neutrals  = [r for r in rows if r[2] == "NEUTRAL"]
    gap_downs = [r for r in rows if r[2] == "GAP_DOWN"]

    def _print_row(sym, score, bias, price, rsi, rv, sig, conf, sector, facts):
        if   bias == "GAP_UP":   bias_col = C.GREEN;  bias_str = "▲ GAP_UP  "
        elif bias == "GAP_DOWN": bias_col = C.RED;    bias_str = "▼ GAP_DOWN"
        else:                    bias_col = C.YELLOW;  bias_str = "— NEUTRAL "

        # Probability bar: green for gap-up zone, red for gap-down zone
        bar_len  = 20
        filled   = int(safe_float(score) / 100 * bar_len)
        if   score >= 62: bar_col = C.GREEN
        elif score <= 38: bar_col = C.RED
        else:             bar_col = C.YELLOW
        bar_str  = "[" + bar_col + "█" * filled + C.DIM + "░" * (bar_len - filled) + C.RESET + "]"

        sig_s    = signal_color(sig) + SIGNAL_SHORT.get(sig, "--") + C.RESET
        rsi_col  = C.RED if rsi > 75 else C.GREEN if rsi > 55 else C.YELLOW if rsi > 40 else C.RED
        pr_str   = fmt_price(price)
        sc_col   = bias_col

        print("  " +
              _ljust(C.BOLD + sym + C.RESET,   12) + "  " +
              _rjust(sc_col + str(score) + "%" + C.RESET, 6) + "  " +
              _ljust(bias_col + bias_str + C.RESET, 10) + "  " +
              bar_str + "  " +
              _rjust(pr_str,  9) + "  " +
              _rjust(rsi_col + str(rsi) + C.RESET, 5) + "  " +
              _rjust(str(rv), 5) + "  " +
              _rjust(sig_s,   4) + "  " +
              confluence_label(conf))

    if gap_ups:
        cprint("  ┌─ GAP UP CANDIDATES  (" + str(len(gap_ups)) + " symbols)  Score ≥ 62%", C.GREEN, bold=True)
        for r in gap_ups: _print_row(*r)

    if neutrals:
        cprint("\n  ┌─ NEUTRAL  (" + str(len(neutrals)) + " symbols)  Score 38-61%", C.YELLOW)
        for r in neutrals: _print_row(*r)

    if gap_downs:
        cprint("\n  ┌─ GAP DOWN CANDIDATES  (" + str(len(gap_downs)) + " symbols)  Score ≤ 38%", C.RED, bold=True)
        for r in gap_downs: _print_row(*r)

    div()
    print()

    # ── Detail view for one symbol ────────────────────────────
    cprint("  Enter symbol for factor breakdown (ENTER to skip): ", C.DIM, bold=False)
    pick = input("  Symbol: ").strip().upper()
    if pick and pick in data:
        score, bias, facts = calc_next_day_gap_score(data[pick])
        print()
        cprint("  FACTOR BREAKDOWN — " + pick, C.CYAN, bold=True)
        div()
        if   bias == "GAP_UP":   bias_col = C.GREEN;  bias_str = "▲ GAP UP"
        elif bias == "GAP_DOWN": bias_col = C.RED;    bias_str = "▼ GAP DOWN"
        else:                    bias_col = C.YELLOW;  bias_str = "— NEUTRAL"
        print("  Overall Score : " + bias_col + C.BOLD + str(score) + "%" + C.RESET +
              "  Bias: " + bias_col + C.BOLD + bias_str + C.RESET)
        print()
        for label, pts in facts:
            col  = C.GREEN if pts > 0 else (C.RED if pts < 0 else C.DIM)
            sign = "+" if pts >= 0 else ""
            bar_pts = abs(int(pts / 20 * 12))
            bar_s   = col + "█" * bar_pts + C.DIM + "░" * (12 - bar_pts) + C.RESET
            print("  [" + bar_s + "]  " + col + sign + str(pts) + "pts" + C.RESET +
                  "  " + label)
        print()
        cprint("  Trading rules:", C.CYAN)
        if bias == "GAP_UP":
            cprint("    • At open: if gap-up confirmed → buy on first 5MIN pullback to VWAP", C.DIM)
            cprint("    • Stop: below prev day low or gap fill level", C.DIM)
            cprint("    • Target 1: " + str(as_dict(as_dict(data[pick].get("DAY")).get("targets")).get("target1", "—")), C.DIM)
        elif bias == "GAP_DOWN":
            cprint("    • At open: if gap-down confirmed → wait for bounce to prev_close, short", C.DIM)
            cprint("    • Stop: above prev day high", C.DIM)
        else:
            cprint("    • Mixed signals — wait for first 15MIN candle direction before entering", C.DIM)
        print()

    cprint("  NOTE: This is a probability estimate, NOT a guarantee.", C.YELLOW)
    cprint("  Always confirm with live price action at open (9:15 IST).", C.DIM)
    print()
    input("  Press ENTER...")


# ─────────────────────────────────────────────────────────────
#  NEW FEATURE: HEATMAP VIEW
# ─────────────────────────────────────────────────────────────

def heatmap_view(data, tfs=None):
    """
    Color-coded signal heatmap — all symbols × active TFs, grouped by sector.
    Each cell shows BO/BD/SW/-- colored by signal.
    Score bar shows composite signal strength as a mini trend bar.
    Instantly shows the market's overall bullish/bearish tilt at a glance.
    """
    tfs = tfs or TIMEFRAMES_SWING
    header("HEATMAP  —  Signal Intensity Across All Sectors")
    if not data:
        cprint("  No data. Run a scan first.", C.YELLOW); return

    wl = load_watchlist()

    # Group by sector
    sectors = {}
    for sym in sorted(data.keys()):
        sec = SECTOR_MAP.get(sym, "OTHER")
        sectors.setdefault(sec, []).append(sym)

    # ── Legend ───────────────────────────────────────────────
    print("  " + C.GREEN  + C.BOLD + "BO" + C.RESET + "=Breakout  " +
          C.RED   + C.BOLD + "BD" + C.RESET + "=Breakdown  " +
          C.YELLOW +          "SW" + C.RESET + "=Sideways  " +
          C.DIM   +          "--" + C.RESET + "=None   " +
          C.YELLOW + "★" + C.RESET + "=Watchlist")
    print("  Score bar: " + C.GREEN + "█" + C.RESET + "=bullish   " +
          C.RED + "█" + C.RESET + "=bearish   (bar length = strength 0-100)")
    print()

    tf_show  = tfs[:5]   # max 5 TF columns
    col_sym  = 12
    col_sec  = 9
    col_tf   = 4
    col_scr  = 4
    col_bar  = 8

    hdr_line = ("  " +
                "  " +                                        # star col
                _ljust("SYMBOL",  col_sym) + "  " +
                _ljust("SECTOR",  col_sec) + "  " +
                "  ".join(_rjust(t[:4], col_tf) for t in tf_show) + "  " +
                _rjust("SCR", col_scr) + "  " +
                "TREND BAR")
    cprint(hdr_line, C.DIM)
    div()

    SIG_CELL = {
        "BREAKOUT":  C.GREEN  + C.BOLD + "BO" + C.RESET,
        "BREAKDOWN": C.RED    + C.BOLD + "BD" + C.RESET,
        "SIDEWAYS":  C.YELLOW +          "SW" + C.RESET,
        "NONE":      C.DIM    +          "--" + C.RESET,
    }

    grand_bo = grand_bd = grand_sw = 0

    for sec in sorted(sectors.keys()):
        syms = sectors[sec]
        # Sector-level counts (DAY or first TF)
        pri_tf = "DAY" if "DAY" in tf_show else tf_show[0]
        sec_bo = sum(1 for s in syms if data[s].get(pri_tf, {}).get("signal") == "BREAKOUT")
        sec_bd = sum(1 for s in syms if data[s].get(pri_tf, {}).get("signal") == "BREAKDOWN")
        sec_sw = sum(1 for s in syms if data[s].get(pri_tf, {}).get("signal") == "SIDEWAYS")
        grand_bo += sec_bo; grand_bd += sec_bd; grand_sw += sec_sw

        sec_col = (C.GREEN  if sec_bo > sec_bd else
                   C.RED    if sec_bd > sec_bo else C.YELLOW)
        print()
        cprint("  ┌─ " + sec_col + C.BOLD + sec + C.RESET + C.DIM +
               "  [" + str(len(syms)) + " sym  BO:" + str(sec_bo) +
               " BD:" + str(sec_bd) + " SW:" + str(sec_sw) + "]" + C.RESET, C.DIM)

        for sym in syms:
            entry  = data[sym]
            _stf   = _score_tf_key(entry, tfs)
            cscore = safe_float(as_dict(entry.get(_stf)).get("composite_score", 0))
            ts     = safe_float(as_dict(entry.get(_stf)).get("trend_strength",  0))
            star   = C.YELLOW + "★" + C.RESET if sym in wl else " "

            tf_cells = "  ".join(
                _rjust(SIG_CELL.get(as_dict(entry.get(tf)).get("signal", "NONE"),
                                    C.DIM + "--" + C.RESET), col_tf)
                for tf in tf_show)

            sc_col = (C.GREEN  if cscore >= 65 else
                      C.YELLOW if cscore >= 40 else C.DIM)
            scr_str = sc_col + _rjust(str(cscore), col_scr) + C.RESET

            print("  " + star +
                  _ljust(sym,            col_sym) + "  " +
                  _ljust(sec[:col_sec],  col_sec) + "  " +
                  tf_cells + "  " +
                  scr_str  + "  " +
                  trend_bar(ts, col_bar))

    div()
    total = len(data)
    print()
    print("  TOTAL  " + str(total) + " symbols  │  " +
          C.GREEN + "BO: " + str(grand_bo) + C.RESET + "  " +
          C.RED   + "BD: " + str(grand_bd) + C.RESET + "  " +
          C.YELLOW + "SW: " + str(grand_sw) + C.RESET)
    # Market sentiment
    if grand_bo > grand_bd * 1.5:
        cprint("  Market Bias: STRONGLY BULLISH", C.GREEN, bold=True)
    elif grand_bd > grand_bo * 1.5:
        cprint("  Market Bias: STRONGLY BEARISH", C.RED, bold=True)
    elif grand_bo > grand_bd:
        cprint("  Market Bias: Mildly Bullish", C.GREEN)
    elif grand_bd > grand_bo:
        cprint("  Market Bias: Mildly Bearish", C.RED)
    else:
        cprint("  Market Bias: NEUTRAL / MIXED", C.YELLOW)
    print()


# ─────────────────────────────────────────────────────────────
#  NEW FEATURE: GAP SCANNER
# ─────────────────────────────────────────────────────────────

def gap_scanner_view(data):
    """
    Show gap analysis for all symbols that have been scanned on DAY tf.
    Gap data is computed during the regular scan — no extra API calls needed.

    Gap types:
      GAP_UP   today's open > yesterday's close by >0.3%
      GAP_DOWN today's open < yesterday's close by >0.3%
      NO_GAP   open within ±0.3% of prev_close

    OPEN gap  → gap level has not been retested (momentum trade setup)
    FILLED gap → price came back to fill the gap (reversal/exhaustion signal)
    """
    header("GAP SCANNER  —  Today's Open vs Yesterday's Close  [DAY TF]")

    if not data:
        cprint("  No data. Run a scan first (S).", C.YELLOW)
        input("  Press ENTER..."); return

    gaps = []
    missing = []
    for sym, entry in data.items():
        e   = as_dict(entry.get("DAY"))
        gap = as_dict(e.get("gap"))
        if not gap:
            missing.append(sym)
            continue
        sig  = e.get("signal", "NONE")
        rsi  = safe_float(e.get("rsi", 0))
        cs   = safe_float(e.get("composite_score", 0))
        conf = confluence_score(entry, TIMEFRAMES_SWING)
        gaps.append((sym, gap, sig, rsi, cs, conf))

    if not gaps:
        cprint("  No gap data found. Run a FULL SCAN including DAY timeframe first.", C.YELLOW)
        if missing:
            cprint("  (" + str(len(missing)) + " symbols have no DAY data)", C.DIM)
        input("  Press ENTER..."); return

    # Sort buckets: gap_up (biggest first), gap_down (biggest first), no_gap
    gap_ups   = sorted([g for g in gaps if g[1]["gap_type"] == "GAP_UP"],
                        key=lambda x: x[1]["gap_pct"], reverse=True)
    gap_downs = sorted([g for g in gaps if g[1]["gap_type"] == "GAP_DOWN"],
                        key=lambda x: x[1]["gap_pct"])
    no_gaps   = sorted([g for g in gaps if g[1]["gap_type"] == "NO_GAP"],
                        key=lambda x: x[0])

    # Column header
    cprint("  " +
           _ljust("SYMBOL",    12) + "  " +
           _rjust("GAP%",       7) + "  " +
           _ljust("TYPE",      10) + "  " +
           _rjust("PREV_CLS",   9) + "  " +
           _rjust("TODAY_OPN",  9) + "  " +
           _rjust("GAP_₹",      7) + "  " +
           _ljust("STATUS",     8) + "  " +
           _rjust("SIG",        4) + "  " +
           _rjust("RSI",        5) + "  " +
           "CONFLUENCE", C.DIM)
    div()

    def _row(sym, gap, sig, rsi, cs, conf):
        gt     = gap["gap_type"]
        pct    = gap["gap_pct"]
        filled = gap["gap_filled"]
        if   gt == "GAP_UP":   col = C.GREEN
        elif gt == "GAP_DOWN": col = C.RED
        else:                  col = C.DIM
        pct_str    = col + C.BOLD + "{:+.2f}%".format(pct) + C.RESET
        type_str   = col + gt + C.RESET
        status_str = (C.YELLOW + C.BOLD + "FILLED" + C.RESET if filled else
                      col + "OPEN  " + C.RESET)
        sig_s   = signal_color(sig) + SIGNAL_SHORT.get(sig,"--") + C.RESET
        rsi_col = (C.RED if rsi > 75 else C.GREEN if rsi > 55 else
                   C.YELLOW if rsi > 40 else C.RED)
        print("  " +
              _ljust(C.BOLD + sym + C.RESET, 12) + "  " +
              _rjust(pct_str,                  7) + "  " +
              _ljust(type_str,                10) + "  " +
              _rjust("₹" + str(gap["prev_close"]),  9) + "  " +
              _rjust("₹" + str(gap["today_open"]),  9) + "  " +
              _rjust("₹" + str(gap["gap_rs"]),       7) + "  " +
              _ljust(status_str,               8) + "  " +
              _rjust(sig_s,                    4) + "  " +
              _rjust(rsi_col + str(rsi) + C.RESET, 5) + "  " +
              confluence_label(conf))

    if gap_ups:
        cprint("\n  ┌─ GAP UP  (" + str(len(gap_ups)) + " symbols) " +
               "─" * 30, C.GREEN, bold=True)
        for row in gap_ups: _row(*row)

    if gap_downs:
        cprint("\n  ┌─ GAP DOWN  (" + str(len(gap_downs)) + " symbols) " +
               "─" * 28, C.RED, bold=True)
        for row in gap_downs: _row(*row)

    if no_gaps:
        cprint("  ┌─ NO SIGNIFICANT GAP  (" + str(len(no_gaps)) + " symbols)", C.DIM)
        for row in no_gaps: _row(*row)

    div()
    print()
    print("  " + C.GREEN + "Gap Up: " + str(len(gap_ups)) + C.RESET +
          "   " + C.RED + "Gap Down: " + str(len(gap_downs)) + C.RESET +
          "   " + C.DIM + "No Gap: " + str(len(no_gaps)) + C.RESET +
          ("   " + C.YELLOW + "(⚠ " + str(len(missing)) +
           " symbols need DAY scan)" + C.RESET if missing else ""))
    print()
    cprint("  Trading rules for gaps:", C.CYAN)
    cprint("    GAP UP  + OPEN   → Momentum — buy first pullback toward prev_close", C.DIM)
    cprint("    GAP UP  + FILLED → Exhaustion — gap faded, watch for reversal", C.DIM)
    cprint("    GAP DOWN + OPEN  → Breakdown — sell/short bounce toward prev_close", C.DIM)
    cprint("    GAP DOWN + FILLED → Recovery — buyers absorbed the gap, may reverse", C.DIM)
    print()
    input("  Press ENTER...")


# ─────────────────────────────────────────────────────────────
#  NEW FEATURE: VOLUME PROFILE VIEW
# ─────────────────────────────────────────────────────────────

def volume_profile_view(data, active_tfs):
    """
    Interactive: pick symbol + TF, fetch fresh candles, compute and display
    a horizontal volume profile bar chart.

    HVN (High Volume Node) — price where most trading happened.
        Acts as a price magnet. Expect slow moves / reversals here.
    LVN (Low Volume Node) — thin trading zone.
        Price moves through quickly. Minimal support/resistance here.
    POC (Point of Control) — single price level with highest total volume.
        Strongest S/R level on the chart.
    """
    header("VOLUME PROFILE")
    if not data:
        cprint("  No data. Run a scan first.", C.YELLOW)
        input("  Press ENTER..."); return

    all_syms = sorted(SYMBOL_MAP.values())
    cols = 6
    cprint("  Available symbols:", C.DIM)
    for i in range(0, len(all_syms), cols):
        print("    " + "  ".join(_ljust(s, 12) for s in all_syms[i:i+cols]))
    print()
    sym = input("  Symbol: ").strip().upper()
    if not sym or sym not in SYMBOL_MAP.values():
        cprint("  Not found.", C.RED); input("  Press ENTER..."); return

    cprint("  TFs: " + ", ".join(TF_CONFIG.keys()), C.DIM)
    tf_in = input("  Timeframe [DAY]: ").strip().upper() or "DAY"
    if tf_in not in TF_CONFIG:
        cprint("  Invalid TF.", C.RED); input("  Press ENTER..."); return

    raw_lb = input("  Lookback bars [60]: ").strip()
    n_bars = int(raw_lb) if raw_lb.isdigit() and int(raw_lb) > 0 else 60

    raw_bn = input("  Price buckets [24]: ").strip()
    n_bins = int(raw_bn) if raw_bn.isdigit() and int(raw_bn) >= 8 else 24

    token = load_token()
    if not token: input("  Press ENTER..."); return
    hdrs     = make_headers(token)
    inst_key = next((k for k, v in SYMBOL_MAP.items() if v == sym), None)
    if not inst_key:
        cprint("  Instrument key not found.", C.RED); input("  Press ENTER..."); return

    unit, value, lookback = TF_CONFIG[tf_in]
    cprint("  Fetching " + str(n_bars) + " candles...", C.DIM)
    df = fetch_candles(inst_key, unit, value, lookback, hdrs, verbose=False, tf_name=tf_in)
    if df.empty:
        cprint("  No data returned.", C.RED); input("  Press ENTER..."); return

    df = df.tail(n_bars).reset_index(drop=True)
    profile, hvn, lvn, poc = calc_volume_profile(df, n_bins=n_bins)
    if not profile:
        cprint("  Could not compute profile.", C.YELLOW); input("  Press ENTER..."); return

    cur_price = float(df.iloc[-1]["close"])
    hvn_p = {p[0] for p in hvn}
    lvn_p = {p[0] for p in lvn}
    BAR_W = 28

    # Bug fix: is_cur tolerance was 1/n_bins (4.17% at 24 bins) — for SBIN at ₹1209
    # that's ±₹50, marking 4-5 buckets with "◀ PRICE" instead of just 1.
    # Fix: derive actual bucket width from consecutive profile entries and use
    # half a bin as tolerance → at most 1 bucket matches current price.
    bin_size = ((profile[1][0] - profile[0][0])
                if len(profile) > 1
                else max(cur_price * 0.005, 1.0))

    header("VOLUME PROFILE  —  " + sym + " / " + tf_in +
           "  [" + str(len(df)) + " bars, " + str(n_bins) + " buckets]")

    cprint("  " +
           _rjust("PRICE",  10) + "  " +
           _rjust("VOL%",    5) + "  " +
           _ljust("BAR", BAR_W + 2) + "  LEVEL", C.DIM)
    div()

    for mid, vol, pct in reversed(profile):
        filled = int(pct / 100 * BAR_W)
        empty  = BAR_W - filled

        is_poc  = abs(mid - poc) < 0.01
        is_hvn  = mid in hvn_p
        is_lvn  = mid in lvn_p
        # Tightened: within half a bucket of cur_price → at most 1 row matches.
        is_cur  = abs(mid - cur_price) <= bin_size * 0.6

        if is_poc:
            bar_col = C.YELLOW + C.BOLD
            label   = C.YELLOW + C.BOLD + "◀ POC — Point of Control (max volume)" + C.RESET
        elif is_hvn:
            bar_col = C.GREEN
            label   = C.GREEN + "★ HVN — strong support/resistance zone" + C.RESET
        elif is_lvn:
            bar_col = C.RED + C.DIM
            label   = C.RED + "✕ LVN — thin zone, fast price moves" + C.RESET
        else:
            bar_col = C.BLUE
            label   = ""

        # Bug fix: cur_marker was printed BEFORE label, producing messy output like
        # "◀ PRICE ₹1209.  ★ HVN — ..." on HVN rows.  Now label comes first,
        # cur_marker appended after so it reads "★ HVN —...  ◀ ₹1209. PRICE" naturally.
        cur_marker = (C.CYAN + C.BOLD + "  ◀ ₹" + str(round(cur_price, 2))
                      + " ← PRICE" + C.RESET) if is_cur else ""
        bar_str    = bar_col + "█" * filled + C.DIM + "░" * empty + C.RESET

        print("  " +
              _rjust("₹" + str(mid),  10) + "  " +
              _rjust(str(pct) + "%",   5) + "  " +
              "[" + bar_str + "]  " +
              label + cur_marker)

    div()
    print()
    cprint("  SUMMARY:", C.CYAN, bold=True)
    print("  POC (highest volume)  : " + C.YELLOW + C.BOLD + "₹" + str(poc) + C.RESET +
          "  ← strongest S/R, expect reversals here")
    for rank, (p, v, pct) in enumerate(
            sorted(hvn, key=lambda x: x[0], reverse=True), 1):
        print("  HVN #" + str(rank) + "               : " +
              C.GREEN + "₹" + str(p) + C.RESET +
              "  (" + str(pct) + "% of peak vol)  ← slow/sticky price zone")
    for rank, (p, v, pct) in enumerate(
            sorted(lvn, key=lambda x: x[0], reverse=True), 1):
        print("  LVN #" + str(rank) + "               : " +
              C.RED + "₹" + str(p) + C.RESET +
              "  (" + str(pct) + "% of peak vol)  ← fast-pass zone, little S/R")
    print()
    cprint("  How to trade:", C.CYAN)
    cprint("    Price approaching HVN/POC → expect slowdown, possible reversal", C.DIM)
    cprint("    Price in LVN              → expect fast move to next HVN/POC", C.DIM)
    cprint("    Breakout above HVN on vol → strong signal, HVN becomes new support", C.DIM)
    print()
    input("  Press ENTER...")


# ─────────────────────────────────────────────────────────────
#  NEW FEATURE: AUTO-SCHEDULE SCAN
# ─────────────────────────────────────────────────────────────

def _schedule_countdown(interval_sec):
    """Live countdown bar between scheduled scans. Ctrl+C propagates up."""
    next_dt = datetime.now(IST) + timedelta(seconds=interval_sec)
    print()
    cprint("  Next scan at " + next_dt.strftime("%H:%M:%S IST") +
           "  │  Ctrl+C to stop", C.DIM)
    bar_w = 40
    for remaining in range(interval_sec, 0, -1):
        done    = interval_sec - remaining
        filled  = int(done / interval_sec * bar_w)
        empty   = bar_w - filled
        mins, s = divmod(remaining, 60)
        line = ("\r  \u23f1  [{}>{}]  {:02d}:{:02d} remaining   ".format(
            "=" * filled, " " * empty, mins, s))
        sys.stdout.write(line)
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


def auto_schedule_scan(data, active_tfs):
    """
    Repeat scans automatically on a timer.

    Options:
      Scope   — ALL symbols / Watchlist only / Specific symbols
      TFs     — override active TFs or keep current
      Interval — N minutes between scans
      Cycles  — max number of scans (or unlimited until Ctrl+C)

    After each cycle: prints BO/BD/SW summary and new signal changes.
    Press Ctrl+C at any time to stop and return to the main menu.
    """
    header("AUTO-SCHEDULE SCAN")

    # ── Scope ────────────────────────────────────────────────
    print("  Symbol scope:")
    print("    1  ALL symbols  (" + str(len(SYMBOL_MAP)) + ")")
    print("    2  Watchlist only")
    print("    3  Specific symbol(s)")
    scope = input("  Choose [1/2/3]: ").strip()

    if scope == "2":
        wl = load_watchlist()
        target = {k: v for k, v in SYMBOL_MAP.items() if v in wl}
        if not target:
            cprint("  Watchlist empty — use [W] to star symbols.", C.YELLOW)
            input("  Press ENTER..."); return data
    elif scope == "3":
        raw = input("  Symbols (comma-separated, e.g. SBIN,TCS): ").strip().upper()
        names  = {s.strip() for s in raw.split(",") if s.strip()}
        target = {k: v for k, v in SYMBOL_MAP.items() if v in names}
        if not target:
            cprint("  None found.", C.RED); input("  Press ENTER..."); return data
    else:
        target = dict(SYMBOL_MAP)

    # ── TF override ───────────────────────────────────────────
    cprint("  Current active TFs : " + ", ".join(active_tfs), C.DIM)
    cprint("  Available TFs      : " + ", ".join(TF_CONFIG.keys()), C.DIM)
    raw_tf = input("  Override TFs (ENTER = keep active): ").strip().upper()
    if raw_tf:
        parsed = [t.strip() for t in raw_tf.split(",") if t.strip() in TF_CONFIG]
        use_tfs = parsed if parsed else list(active_tfs)
    else:
        use_tfs = list(active_tfs)

    # ── Interval ─────────────────────────────────────────────
    raw_int = input("  Scan every N minutes [15]: ").strip()
    interval_min = int(raw_int) if raw_int.isdigit() and int(raw_int) > 0 else 15
    interval_sec = interval_min * 60

    # ── Max cycles ────────────────────────────────────────────
    raw_cyc = input("  Max cycles (ENTER = unlimited): ").strip()
    max_cyc  = int(raw_cyc) if raw_cyc.isdigit() and int(raw_cyc) > 0 else None

    # ── Market hours check ────────────────────────────────────
    has_intraday = any(t in TIMEFRAMES_INTRADAY for t in use_tfs)
    if has_intraday and not is_market_open():
        cprint("⚠  Intraday TFs selected but market is CLOSED.", C.YELLOW)
        cprint("     They will be skipped until 09:15 IST.", C.DIM)

    print()
    div("═")
    cprint("  AUTO-SCHEDULE RUNNING", C.GREEN, bold=True)
    print("  Symbols   : " + C.CYAN + str(len(target)) + C.RESET)
    print("  TFs       : " + C.CYAN + ", ".join(use_tfs) + C.RESET)
    print("  Interval  : " + C.CYAN + str(interval_min) + " min" + C.RESET)
    print("  Max cycles: " + C.CYAN + (str(max_cyc) if max_cyc else "unlimited") + C.RESET)
    cprint("  Press Ctrl+C to stop at any time", C.YELLOW)
    div("═")

    token = load_token()
    if not token: input("  Press ENTER..."); return data
    hdrs = make_headers(token)

    cycle = 0
    try:
        while True:
            if max_cyc and cycle >= max_cyc:
                cprint("✓ Max cycles reached.", C.GREEN); break

            cycle += 1
            now_s  = datetime.now(IST).strftime("%H:%M:%S")
            print()
            cprint("  ── Cycle " + str(cycle) +
                   ("/" + str(max_cyc) if max_cyc else "") +
                   "  started " + now_s + "  " + "─" * 25, C.CYAN)

            # Skip intraday TFs outside market hours
            if has_intraday and not is_market_open():
                scan_tfs = [t for t in use_tfs if t not in TIMEFRAMES_INTRADAY]
                if not scan_tfs:
                    cprint("  Market closed — no swing TFs to scan. Waiting...", C.YELLOW)
                    _schedule_countdown(interval_sec)
                    continue
                cprint("  Market closed — scanning swing TFs only: " +
                       ", ".join(scan_tfs), C.YELLOW)
            else:
                scan_tfs = use_tfs

            # ── Scan ─────────────────────────────────────────
            prev_sigs = {sym: {tf: as_dict(as_dict(data.get(sym)).get(tf)).get("signal", "NONE")
                                for tf in scan_tfs}
                         for sym in target.values()}

            start_scan_pass()
            for inst_key, sym in target.items():
                try:
                    data = _scan_one_symbol(inst_key, sym, data, scan_tfs, hdrs)
                except TokenError as exc:
                    # A revoked token fails for every symbol left in the
                    # cycle — stop rather than hammering the API all day.
                    cprint("\n  ✗ AUTO-SCHEDULE STOPPED: " + str(exc),
                           C.RED, bold=True)
                    save_data(data)
                    input("Press ENTER to return to menu...")
                    return data
                time.sleep(0.8)

            save_data(data)

            # ── Cycle summary ─────────────────────────────────
            done_s = datetime.now(IST).strftime("%H:%M:%S")
            cprint("  ✓ Cycle " + str(cycle) + " done  [" + done_s + "]", C.GREEN)

            bo = bd = sw = changes = 0
            new_bo_syms = []
            new_bd_syms = []
            for sym in target.values():
                for tf in scan_tfs:
                    sig = as_dict(as_dict(data.get(sym)).get(tf)).get("signal", "NONE")
                    old = as_dict(prev_sigs.get(sym)).get(tf, "NONE")
                    if sig == "BREAKOUT":  bo += 1
                    if sig == "BREAKDOWN": bd += 1
                    if sig == "SIDEWAYS":  sw += 1
                    if sig != old and old != "NONE":
                        changes += 1
                        if sig == "BREAKOUT":  new_bo_syms.append(sym + "/" + tf)
                        if sig == "BREAKDOWN": new_bd_syms.append(sym + "/" + tf)

            print("  " + C.GREEN + "BO: " + str(bo)   + C.RESET + "   " +
                  C.RED   + "BD: " + str(bd)   + C.RESET + "   " +
                  C.YELLOW + "SW: " + str(sw)   + C.RESET + "   " +
                  C.YELLOW + "⚡ Changes: " + str(changes) + C.RESET)

            if new_bo_syms:
                cprint("  NEW BREAKOUTS  : " + ", ".join(new_bo_syms), C.GREEN, bold=True)
            if new_bd_syms:
                cprint("  NEW BREAKDOWNS : " + ", ".join(new_bd_syms), C.RED,   bold=True)

            if max_cyc and cycle >= max_cyc:
                break

            _schedule_countdown(interval_sec)

    except KeyboardInterrupt:
        print()
        cprint("  ✓ Auto-schedule stopped.", C.YELLOW)

    input("Press ENTER to return to menu...")
    return data


# ─────────────────────────────────────────────────────────────
#  DB FILE SELECTOR  —  called once at startup
#  Scans current folder for DD-MM-YYYY.db backup files.
#  If found → prompts user to pick one (or keep default).
#  If none found → silently uses master_scanner.db.
# ─────────────────────────────────────────────────────────────

_DATE_DB_RE = re.compile(r'^\d{2}-\d{2}-\d{4}\.db$')

def momentum_screener(data, tfs=None):
    """
    Momentum Screener: rank all symbols by a combined momentum score
    using ADX, RSI, MFI, Williams %R, MACD histogram, and relative volume.

    Score breakdown (0-100):
      ADX strength      25pts  — trending market confirmation
      RSI positioning   20pts  — is RSI in the bull/bear momentum zone?
      MFI reading       20pts  — volume-backed money flow
      Williams %R       15pts  — fast momentum direction
      MACD hist size    10pts  — how strong/building is the MACD move?
      Relative Volume   10pts  — conviction behind the move

    A score >= 65 = strong directional momentum  (best entry timing)
    A score 40-65 = moderate momentum            (building, watch)
    A score < 40  = weak / ranging               (avoid / wait)
    """
    tfs = tfs or TIMEFRAMES_SWING
    header("MOMENTUM SCREENER  —  Ranked by Momentum Quality")
    cprint("  Score: ADX(25) + RSI(20) + MFI(20) + Williams%R(15) + MACD(10) + Volume(10)", C.DIM)
    div()

    rows = []
    for sym, entry in data.items():
        tf_key = _score_tf_key(entry, tfs)
        e      = entry.get(tf_key, {})
        sig    = e.get("signal", "NONE")
        price  = safe_float(e.get("price", 0))
        rsi    = safe_float(e.get("rsi",   50))
        adx_v  = safe_float(e.get("adx",   0))
        pdi    = safe_float(e.get("plus_di",  0))
        mdi    = safe_float(e.get("minus_di", 0))
        mfi_v  = safe_float(e.get("mfi",  50))
        wr_v   = safe_float(e.get("williams_r", -50))
        rel_v  = safe_float(e.get("rel_vol", 1.0))

        # Determine directional bias from signal
        is_bull = sig == "BREAKOUT"
        is_bear = sig == "BREAKDOWN"
        if not (is_bull or is_bear):
            # For non-signal symbols, infer from ADX DI.
            # Bug fix: when +DI == -DI (perfectly balanced, e.g. all-zero ADX
            # on an unscanned symbol) neither flag was set and every band
            # below silently fell through to the BEARISH branch, reporting
            # "strong bearish momentum" for a flat, directionless stock.
            is_bull = pdi > mdi
            is_bear = mdi > pdi
            neutral = not (is_bull or is_bear)
        else:
            neutral = False

        # ADX strength score (0-25)
        if adx_v >= 50:   adx_s = 25
        elif adx_v >= 40: adx_s = 20
        elif adx_v >= 30: adx_s = 15
        elif adx_v >= 25: adx_s = 10
        elif adx_v >= 20: adx_s = 5
        else:             adx_s = 0

        # RSI positioning (0-20): momentum zone vs extremes
        if neutral:
            # No directional bias — reward how far from the midline RSI sits,
            # in EITHER direction.  Previously this fell through to the bearish
            # branch and reported momentum for a completely flat symbol.
            d = abs(rsi - 50)
            rsi_s = 20 if d >= 20 else (15 if d >= 15 else (10 if d >= 10 else
                                                            (5 if d >= 5 else 0)))
        elif is_bull:
            if 55 <= rsi <= 65:   rsi_s = 20
            elif 50 <= rsi < 55:  rsi_s = 15
            elif 65 < rsi <= 70:  rsi_s = 10  # slightly overbought but still trending
            elif 45 <= rsi < 50:  rsi_s = 5
            else:                 rsi_s = 0   # oversold or extremely OB
        else:
            if 35 <= rsi <= 45:   rsi_s = 20
            elif 45 < rsi <= 50:  rsi_s = 15
            elif 30 <= rsi < 35:  rsi_s = 10
            elif 50 < rsi <= 55:  rsi_s = 5
            else:                 rsi_s = 0

        # MFI (0-20): volume-backed money flow
        if neutral:
            d = abs(mfi_v - 50)
            mfi_s = 20 if d >= 30 else (15 if d >= 20 else (10 if d >= 10 else
                                                            (5 if d >= 5 else 0)))
        elif is_bull:
            if mfi_v >= 70:   mfi_s = 20
            elif mfi_v >= 60: mfi_s = 15
            elif mfi_v >= 50: mfi_s = 10
            elif mfi_v >= 40: mfi_s = 5
            else:             mfi_s = 0
        else:
            if mfi_v <= 30:   mfi_s = 20
            elif mfi_v <= 40: mfi_s = 15
            elif mfi_v <= 50: mfi_s = 10
            elif mfi_v <= 60: mfi_s = 5
            else:             mfi_s = 0

        # Williams %R (0-15)
        if neutral:
            d = abs(wr_v + 50)
            wr_s = 15 if d >= 40 else (10 if d >= 25 else (5 if d >= 10 else 0))
        elif is_bull:
            if -30 < wr_v <= -10:   wr_s = 15  # strong bullish momentum
            elif -50 < wr_v <= -30: wr_s = 10
            elif wr_v > -10:        wr_s = 5   # overbought
            else:                   wr_s = 0
        else:
            if -90 <= wr_v < -70:   wr_s = 15  # strong bearish momentum
            elif -70 <= wr_v < -50: wr_s = 10
            elif wr_v < -90:        wr_s = 5   # oversold
            else:                   wr_s = 0

        # Relative volume (0-10)
        if rel_v >= 3.0:   vol_s = 10
        elif rel_v >= 2.0: vol_s = 7
        elif rel_v >= 1.5: vol_s = 5
        elif rel_v >= 1.2: vol_s = 3
        else:              vol_s = 0

        macd_hist  = safe_float(e.get("macd_hist_val", 0))

        # MACD histogram score (0-10): how strong/building is the MACD move?
        # Bug fix: was `int(cs / 10)` — composite_score proxy.  Now uses
        # actual stored MACD histogram value for correct momentum measurement.
        # Positive hist = bullish momentum building; negative = bearish.
        if is_bull:
            macd_s = min(10, max(0, int(macd_hist * 500))) if macd_hist > 0 else 0
        elif is_bear:
            macd_s = min(10, max(0, int(-macd_hist * 500))) if macd_hist < 0 else 0
        else:
            macd_s = min(10, max(0, int(abs(macd_hist) * 500)))

        total_mom = adx_s + rsi_s + mfi_s + wr_s + vol_s + macd_s
        rows.append((sym, sig, total_mom, adx_v, rsi, mfi_v, wr_v, rel_v, price, tf_key,
                     adx_s, rsi_s, mfi_s, wr_s))

    rows.sort(key=lambda r: r[2], reverse=True)
    top = rows[:25]

    if not top:
        cprint("  No data. Run a scan first.\n", C.YELLOW)
        return

    cprint("  " +
           _ljust("SYM",    12) + "  " +
           _ljust("SIG",     9) + "  " +
           _rjust("MOM",     4) + "  " +
           _rjust("ADX",     5) + "  " +
           _rjust("RSI",     5) + "  " +
           _rjust("MFI",     5) + "  " +
           _rjust("W%R",     6) + "  " +
           _rjust("RVol",    5) + "  " +
           _rjust("PRICE",   9) + "  " +
           "MOMENTUM BREAKDOWN", C.DIM)
    div()

    for (sym, sig, total_mom, adx_v, rsi, mfi_v, wr_v, rel_v, price, tf_key,
         adx_s, rsi_s, mfi_s, wr_s) in top:
        col    = signal_color(sig)
        mom_col = C.GREEN if total_mom >= 65 else (C.YELLOW if total_mom >= 40 else C.DIM)
        adx_col = C.GREEN if adx_v > 40 else (C.YELLOW if adx_v > 25 else C.DIM)
        mfi_col = C.GREEN if mfi_v > 60 else (C.RED if mfi_v < 40 else C.YELLOW)
        wr_col  = C.GREEN if wr_v > -30 else (C.RED if wr_v < -70 else C.YELLOW)
        rv_col  = C.GREEN if rel_v >= 2 else (C.YELLOW if rel_v >= 1.5 else C.DIM)
        price_s = fmt_price(price)
        bar_len  = 15
        filled   = int(total_mom / 100 * bar_len)
        bar_str  = "[" + mom_col + "█" * filled + C.DIM + "░" * (bar_len - filled) + C.RESET + "]"

        print("  " +
              _ljust(col + sym + C.RESET, 12) + "  " +
              _ljust(col + (sig if sig != "NONE" else "—") + C.RESET, 9) + "  " +
              _rjust(mom_col + str(total_mom) + C.RESET, 4) + "  " +
              _rjust(adx_col + str(adx_v) + C.RESET, 5) + "  " +
              _rjust(str(rsi), 5) + "  " +
              _rjust(mfi_col + str(mfi_v) + C.RESET, 5) + "  " +
              _rjust(wr_col + str(wr_v) + C.RESET, 6) + "  " +
              _rjust(rv_col + str(round(rel_v, 1)) + C.RESET, 5) + "  " +
              _rjust(price_s, 9) + "  " +
              bar_str +
              "  ADX:" + str(adx_s) + " RSI:" + str(rsi_s) +
              " MFI:" + str(mfi_s) + " WR:" + str(wr_s))

    div()
    cprint("  ✦ MOM >= 65 = strong directional momentum  |  40-65 = building  |  < 40 = ranging", C.DIM)
    print()
    input("  Press ENTER...")


def trend_strength_view(data, tfs=None):
    """
    Trend Strength View: compare ADX, +DI/-DI, Williams %R, MFI, CCI
    across all scanned symbols. Reveals which stocks are in genuine trends
    (high ADX) vs ranging (low ADX) — prevents chasing fakeout breakouts.

    Key reading guide:
      ADX > 40, +DI > -DI = strong uptrend (ride it, set trailing stop)
      ADX > 40, -DI > +DI = strong downtrend (avoid longs, short only)
      ADX 25-40, +/-DI separating = trend developing (early entry zone)
      ADX < 20             = ranging / no trend (mean-reversion plays only)
      ADX falling from >40 = trend exhausting (take profit, tighten stops)
    """
    tfs = tfs or TIMEFRAMES_SWING
    header("TREND STRENGTH VIEW  —  ADX | Williams %R | MFI | CCI")
    cprint("  ADX > 40=STRONG TREND  |  25-40=DEVELOPING  |  < 20=RANGING", C.DIM)
    div()

    rows = []
    for sym, entry in sorted(data.items()):
        tf_key = _score_tf_key(entry, tfs)
        e      = entry.get(tf_key, {})
        sig    = e.get("signal", "NONE")
        adx_v  = safe_float(e.get("adx",        0))
        pdi    = safe_float(e.get("plus_di",     0))
        mdi    = safe_float(e.get("minus_di",    0))
        wr_v   = safe_float(e.get("williams_r", -50))
        mfi_v  = safe_float(e.get("mfi",        50))
        cci_v  = safe_float(e.get("cci",         0))
        price  = safe_float(e.get("price",       0))
        rsi    = safe_float(e.get("rsi",        50))
        rows.append((sym, sig, adx_v, pdi, mdi, wr_v, mfi_v, cci_v, price, rsi))

    rows.sort(key=lambda r: r[2], reverse=True)  # sort by ADX descending

    cprint("  " +
           _ljust("SYM",    12) + "  " +
           _ljust("SIG",     9) + "  " +
           _rjust("ADX",     5) + "  " +
           _rjust("+DI",     5) + "  " +
           _rjust("-DI",     5) + "  " +
           _ljust("TREND",   12) + "  " +
           _rjust("W%R",     6) + "  " +
           _rjust("MFI",     5) + "  " +
           _rjust("CCI",     7) + "  " +
           _rjust("RSI",     5) + "  " +
           "PRICE", C.DIM)
    div()

    for sym, sig, adx_v, pdi, mdi, wr_v, mfi_v, cci_v, price, rsi in rows:
        col     = signal_color(sig)
        # ADX colour + label
        if adx_v >= 40:
            adx_col = C.GREEN;  adx_lbl = "STRONG  "
        elif adx_v >= 30:
            adx_col = C.YELLOW; adx_lbl = "TRENDING"
        elif adx_v >= 20:
            adx_col = C.YELLOW; adx_lbl = "WEAK TRD"
        else:
            adx_col = C.DIM;    adx_lbl = "RANGING "

        # Directional bias
        if pdi > mdi:
            di_col = C.GREEN; di_sym = "↑"
        elif mdi > pdi:
            di_col = C.RED;   di_sym = "↓"
        else:
            di_col = C.DIM;   di_sym = "—"
        adx_lbl = di_col + di_sym + " " + adx_col + adx_lbl + C.RESET

        wr_col  = C.GREEN if wr_v > -30 else (C.RED if wr_v < -70 else C.YELLOW)
        mfi_col = C.GREEN if mfi_v > 60 else (C.RED if mfi_v < 40 else C.YELLOW)
        cci_col = C.GREEN if cci_v > 100 else (C.RED if cci_v < -100 else C.YELLOW)
        price_s = fmt_price(price)

        print("  " +
              _ljust(col + sym + C.RESET, 12) + "  " +
              _ljust(col + (sig if sig != "NONE" else "—") + C.RESET, 9) + "  " +
              _rjust(adx_col + str(adx_v) + C.RESET, 5) + "  " +
              _rjust(str(pdi), 5) + "  " +
              _rjust(str(mdi), 5) + "  " +
              _ljust(adx_lbl, 12) + "  " +
              _rjust(wr_col + str(wr_v) + C.RESET, 6) + "  " +
              _rjust(mfi_col + str(mfi_v) + C.RESET, 5) + "  " +
              _rjust(cci_col + str(cci_v) + C.RESET, 7) + "  " +
              _rjust(str(rsi), 5) + "  " +
              price_s)

    div()
    cprint("  W%R: >-30=overbought  -30/-50=bull  -50/-80=bear  <-80=oversold", C.DIM)
    cprint("  MFI: >80=overbought   60-80=bullish  20-40=bearish  <20=oversold", C.DIM)
    cprint("  CCI: >+100=OB/uptrend  0=neutral  <-100=OS/downtrend", C.DIM)
    print()
    input("  Press ENTER...")


def select_db_file(silent=False):
    """
    Detect date-named DB files (DD-MM-YYYY.db) in the current working
    directory, present a numbered menu, and return the chosen path.
    Falls back to master_scanner.db if no dated files are found.

    silent=True  → suppress the "no dated files" notice (used at startup).
    silent=False → show it (used when called from the menu via option ~).
    """
    global DB_FILE, DATA_FILE

    # Collect & sort dated DB files (newest date first)
    dated = sorted(
        [f for f in os.listdir('.') if _DATE_DB_RE.match(f)],
        key=lambda f: datetime.strptime(f[:-3], '%d-%m-%Y'),
        reverse=True
    )

    if not dated:
        # Bug fix: only show the warning when called interactively from the
        # menu (silent=False). At startup (silent=True) this fired every launch
        # even for users who never use dated backup files — noisy and confusing.
        if not silent:
            cprint("  No dated DB files found (DD-MM-YYYY.db) in this folder.", C.YELLOW)
            cprint("  Currently using: " + DB_FILE, C.DIM)
        return

    # ── Show selection menu ──────────────────────────────────
    clr()
    cprint('═' * W, C.CYAN)
    cprint('  SELECT DATABASE FILE', C.WHITE, bold=True)
    cprint('  Found ' + str(len(dated)) + ' dated backup file(s) in this folder:', C.DIM)
    cprint('  Current: ' + C.YELLOW + DB_FILE + C.RESET, C.DIM)
    cprint('═' * W, C.CYAN)
    print()

    # Option 0 → default master_scanner.db
    cur_marker = C.GREEN + ' ◀ active' + C.RESET if DB_FILE == 'master_scanner.db' else ''
    print('  ' + C.BOLD + '0' + C.RESET + '  ' +
          C.GREEN + 'master_scanner.db' + C.RESET +
          C.DIM + '  (default — live scan data)' + C.RESET + cur_marker)

    for i, fname in enumerate(dated, 1):
        try:
            sz = os.path.getsize(fname)
            sz_str = ' ({:.1f} KB)'.format(sz / 1024)
        except OSError:
            sz_str = ''
        cur_marker = C.GREEN + ' ◀ active' + C.RESET if DB_FILE == fname else ''
        print('  ' + C.BOLD + str(i) + C.RESET + '  ' +
              C.YELLOW + fname + C.RESET +
              C.DIM + sz_str + C.RESET + cur_marker)

    print()
    cprint('  Enter number to load that DB  (ENTER = keep current / default):', C.DIM)
    try:
        raw = input('  Choice: ').strip()
    except EOFError:
        # No stdin (piped/non-interactive run) — keep the default DB instead of
        # crashing before the first screen is ever drawn.
        cprint('  No input available — keeping ' + DB_FILE, C.DIM)
        return

    chosen = None
    if raw == '':
        # Bug fix: ENTER must keep the CURRENT database. It used to fall
        # through to master_scanner.db, so a user who had loaded a dated
        # backup (say 01-09-2026.db) and pressed ENTER to keep it was silently
        # switched to the live scan DB — losing their place and, worse,
        # pointing the next save at a different file than they expected.
        # Option 0 is still the explicit way to pick the default.
        return
    elif raw == '0':
        chosen = 'master_scanner.db'
    elif raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(dated):
            chosen = dated[idx]
        else:
            cprint('  Invalid choice — keeping current: ' + DB_FILE, C.YELLOW)
            return
    else:
        cprint('  Invalid input — keeping current: ' + DB_FILE, C.YELLOW)
        return

    DB_FILE   = chosen
    DATA_FILE = chosen

    if chosen != 'master_scanner.db':
        cprint('  ✓ Selected: ' + chosen, C.GREEN, bold=True)
        time.sleep(0.6)


# ─────────────────────────────────────────────────────────────
#  MAIN MENU
# ─────────────────────────────────────────────────────────────

ACTIVE_TFS = list(TIMEFRAMES_SWING)  # runtime TF selection

def _warn_stale_holiday_calendar():
    """Nag once if NSE_HOLIDAYS has no entry for the current year.

    Bug fix: the holiday list has to be refreshed by hand each December.
    Silently running a year past its end made every holiday look like a
    trading day again — the exact failure the list exists to prevent.
    """
    this_year = _today_ist().year
    if this_year in _NSE_HOLIDAY_YEARS:
        return
    cprint("  ⚠  NSE_HOLIDAYS has no entries for " + str(this_year) + ".", C.YELLOW)
    cprint("     Weekends are still handled, but market holidays this year will be")
    cprint("     treated as trading days. Add the list to NSE_HOLIDAYS.", C.YELLOW)
    print()


def main_menu_loop(data, tfs):
    """
    Drive the interactive main menu until the user chooses 0 (Quit).

    Split out of main() so the whole CLI can be driven by tests without
    re-running start-up (DB pick, schema init, load) every time.
    Returns (data, active_tfs) so callers can persist the result.
    """
    global ACTIVE_TFS
    ACTIVE_TFS = list(tfs) or list(ACTIVE_TFS)

    while True:
        dashboard(data, ACTIVE_TFS)

        # ══════════════════════════════════════════════════════
        #  MAIN MENU  —  expanded with descriptions
        # ══════════════════════════════════════════════════════
        mkt_status = (C.GREEN + "● MARKET OPEN" if is_market_open()
                      else C.RED + "● MARKET CLOSED") + C.RESET
        cprint("  MENU" + "  " * 28 + mkt_status, C.WHITE, bold=True)
        div("─", W)

        print("  " + C.CYAN + C.BOLD + "── SCAN ──────────────────────────────────────────────────────────" + C.RESET)
        print("  S  Auto Scan ALL       Scan every symbol across active TFs")
        print("  I  Individual Scan     Scan ONE symbol  (pick symbol + TFs)")
        print("  2  Scan specific TFs   Scan all symbols on custom TF set")
        print("  K  Set active TFs      Change default TF set for scans & views")
        div("─", W)

        print("  " + C.CYAN + C.BOLD + "── ANALYSIS VIEWS ────────────────────────────────────────────────" + C.RESET)
        print("  V  View symbol         Full detail — all TFs, indicators, Fib, 52W")
        print("  O  Stock Summary       Plain-English WHY BUY / WHY SELL analysis")
        print("  B  Best Setups ★       Top 20 ranked by Composite Score")
        print("  G  Sector View         Signals grouped by Nifty sector")
        print("  M  Heatmap             Color-coded signal grid across all sectors")
        print("  F  Filter by signal    Show only BO / BD / SW / NONE symbols")
        print("  C  Candle Patterns     All detected candlestick patterns")
        print("  D  RSI Divergence      Regular & hidden divergences across symbols")
        print("  T  Statistics          Signal counts, averages, confluence ranking")
        print("  X  Alert Scanner       Conflicts, MACD crosses, ST flips, vol spikes")
        div("─", W)
        print("  " + C.CYAN + C.BOLD + "── NEW FEATURES ──────────────────────────────────────────────────" + C.RESET)
        print("  J  Gap Scanner         Today open vs yesterday close — gap up/down")
        print("  Y  Volume Profile      HVN / LVN / POC chart for any symbol + TF")
        print("  U  Auto-Schedule       Repeat scans on a timer (Ctrl+C to stop)")
        print("  Q  Next Day Gap ★      Predict tomorrow's gap-up/down before open")
        print("  3  Momentum Screener ★ Rank by ADX+RSI+MFI+Williams%%R momentum score")
        print("  4  Trend Strength      ADX | +DI/-DI | Williams%%R | MFI | CCI table")
        div("─", W)

        print("  " + C.CYAN + C.BOLD + "── HISTORY & WATCHLIST ───────────────────────────────────────────" + C.RESET)
        print("  H  History log         Signal history for a symbol + TF  (last 20)")
        print("  W  Toggle Watchlist ★  Star / unstar a symbol")
        print("  L  Watchlist View      See only your starred symbols")
        div("─", W)

        print("  " + C.CYAN + C.BOLD + "── MANAGE & EXPORT ───────────────────────────────────────────────" + C.RESET)
        print("  A  Add symbol          Add a custom NSE symbol & instrument key")
        print("  R  Remove symbol       Remove a custom symbol from the list")
        print("  N  Edit note           Attach a personal note to any symbol / TF")
        print("  E  Export TXT          Save formatted report  → master_report.txt")
        print("  Z  Export CSV          Save machine-readable data → master_export.csv")
        print("  P  Export HTML ★       Full visual browser report → master_report.html")
        print("  ~  Change DB File      Switch to a different dated backup DB")
        div("─", W)

        print("  0  Quit")
        div("═", W)
        print("  Active TFs : " + C.YELLOW + ", ".join(ACTIVE_TFS) + C.RESET +
              "     Data file: " + C.DIM + DATA_FILE + C.RESET)
        div("─", W)

        choice = input("  Choice: ").strip().upper()

        if choice == "S":
            data = auto_scan(data, ACTIVE_TFS)

        elif choice == "I":
            data = scan_single_symbol(data, ACTIVE_TFS)

        elif choice == "2":
            cprint("\n  Available TFs: " + ", ".join(TF_CONFIG.keys()), C.DIM)
            cprint("  Intraday (need open market): 5MIN, 15MIN, 1HR", C.DIM)
            cprint("  Swing (any time):            DAY, WEEK, MONTH", C.DIM)
            raw = input("  Enter TF(s) comma-separated e.g. DAY,WEEK : ").strip().upper()
            tfs = [t.strip() for t in raw.split(",") if t.strip() in TF_CONFIG]
            if tfs:
                data = auto_scan(data, tfs)
            else:
                cprint("  Invalid — no changes made.", C.RED)
                input("  Press ENTER...")

        elif choice == "K":
            cprint("\n  Available: " + ", ".join(TF_CONFIG.keys()), C.DIM)
            cprint("  Current  : " + ", ".join(ACTIVE_TFS), C.YELLOW)
            raw = input("  Set active TFs (comma-separated): ").strip().upper()
            tfs = [t.strip() for t in raw.split(",") if t.strip() in TF_CONFIG]
            if tfs:
                ACTIVE_TFS = tfs
                cprint("  ✓ Active TFs updated: " + ", ".join(ACTIVE_TFS), C.GREEN)
            else:
                cprint("  Invalid — keeping current: " + ", ".join(ACTIVE_TFS), C.YELLOW)
            input("  Press ENTER...")

        elif choice == "G":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            sector_view(data)
            input("  Press ENTER...")

        elif choice == "M":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            heatmap_view(data, ACTIVE_TFS)
            input("  Press ENTER...")

        elif choice == "J":
            if not data:
                cprint("  No data. Run a scan first (include DAY tf).", C.YELLOW); input("  Press ENTER..."); continue
            gap_scanner_view(data)

        elif choice == "Y":
            volume_profile_view(data, ACTIVE_TFS)

        elif choice == "U":
            data = auto_schedule_scan(data, ACTIVE_TFS)

        elif choice == "Q":
            if not data:
                cprint("  No data. Run a scan (S) with DAY tf first.", C.YELLOW)
                input("  Press ENTER..."); continue
            next_day_gap_view(data)

        elif choice == "3":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            momentum_screener(data, ACTIVE_TFS)

        elif choice == "4":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            trend_strength_view(data, ACTIVE_TFS)

        elif choice == "B":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            best_setups_view(data, ACTIVE_TFS)
            input("  Press ENTER...")

        elif choice == "C":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            candle_pattern_view(data, ACTIVE_TFS)
            input("  Press ENTER...")

        elif choice == "D":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            header("RSI DIVERGENCE VIEW")
            found = False
            for sym, entry in sorted(data.items()):
                tf_key = _score_tf_key(entry, ACTIVE_TFS)
                e      = entry.get(tf_key, {})
                div_   = as_dict(e.get("rsi_divergence"))
                if not any(div_.values()):
                    continue
                found = True
                sig   = e.get("signal", "NONE")
                col   = signal_color(sig)
                price = e.get("price", 0)
                rsi   = e.get("rsi", 0)
                print("  " + C.BOLD + _ljust(sym, 14) + C.RESET +
                      col + "{:<10}".format(sig) + C.RESET +
                      "  ₹{:<8}  RSI:{:<5}  ".format(int(price) if price else 0, rsi) +
                      (C.GREEN + "Reg.Bull▲ " + C.RESET if div_.get("regular_bull") else "") +
                      (C.GREEN + "Hid.Bull▲ " + C.RESET if div_.get("hidden_bull")  else "") +
                      (C.RED   + "Reg.Bear▼ " + C.RESET if div_.get("regular_bear") else "") +
                      (C.RED   + "Hid.Bear▼ " + C.RESET if div_.get("hidden_bear")  else ""))
            if not found:
                cprint("  No divergences detected in current scan data.\n", C.YELLOW)
            print()
            input("  Press ENTER...")

        elif choice == "V":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            # Show full symbol list for easy picking
            all_syms = sorted(data.keys())
            cols = 6
            print()
            cprint("  Available symbols:", C.DIM)
            for _i in range(0, len(all_syms), cols):
                print("    " + "  ".join(_ljust(s, 12) for s in all_syms[_i:_i+cols]))
            print()
            sym = input("  Symbol (or ENTER to cancel): ").strip().upper()
            if sym and sym in data:
                view_detail(sym, data)
                input("  Press ENTER...")
            elif sym:
                cprint("  Not found: " + sym, C.RED)
                input("  Press ENTER...")

        elif choice == "O":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            all_syms = sorted(data.keys())
            cols = 6
            print()
            cprint("  Available symbols:", C.DIM)
            for _i in range(0, len(all_syms), cols):
                print("    " + "  ".join(_ljust(s, 12) for s in all_syms[_i:_i+cols]))
            print()
            sym = input("  Symbol for summary (or ENTER to cancel): ").strip().upper()
            if sym and sym in data:
                summary_view(sym, data, ACTIVE_TFS)
                input("  Press ENTER...")
            elif sym:
                cprint("  Not found: " + sym, C.RED)
                input("  Press ENTER...")

        elif choice == "W":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            sym = pick_symbol(data, "  Symbol to star/unstar: ")
            if sym:
                toggle_watchlist(sym)
                input("  Press ENTER...")

        elif choice == "L":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            watchlist_view(data, ACTIVE_TFS)
            input("  Press ENTER...")

        elif choice == "F":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            print("\n  1. BREAKOUT   2. BREAKDOWN   3. SIDEWAYS   4. NONE")
            sm  = {"1": "BREAKOUT", "2": "BREAKDOWN", "3": "SIDEWAYS", "4": "NONE"}
            sig = sm.get(input("  Choose [1-4]: ").strip())
            if sig:
                filter_view(data, sig, ACTIVE_TFS)
                input("  Press ENTER...")

        elif choice == "T":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            statistics_view(data)
            input("  Press ENTER...")

        elif choice == "H":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            sym = pick_symbol(data)
            if sym:
                cprint("  TFs: " + ", ".join(TF_CONFIG.keys()), C.DIM)
                tf = input("  Choose TF: ").strip().upper()
                if tf in TF_CONFIG:
                    history_view(sym, tf)
                input("  Press ENTER...")

        elif choice == "X":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            alert_scanner(data, ACTIVE_TFS)

        elif choice == "E":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            export_report(data, ACTIVE_TFS)

        elif choice == "Z":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            export_csv(data, ACTIVE_TFS)

        elif choice == "P":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            export_html(data, ACTIVE_TFS)

        elif choice == "A":
            add_custom_symbol()

        elif choice == "R":
            remove_custom_symbol()

        elif choice == "N":
            if not data:
                cprint("  No data. Run a scan first.", C.YELLOW); input("  Press ENTER..."); continue
            sym = pick_symbol(data)
            if sym:
                edit_note(sym, data)

        elif choice == "~":
            old_db = DB_FILE
            select_db_file(silent=False)
            if DB_FILE != old_db:
                cprint("  Reloading data from " + DB_FILE + "…", C.CYAN)
                data = load_data()
                for sym in SYMBOL_MAP.values():
                    ensure_symbol(data, sym)
                cprint("  ✓ Switched to: " + DB_FILE, C.GREEN, bold=True)
            else:
                cprint("  DB unchanged: " + DB_FILE, C.DIM)
            input("  Press ENTER...")

        elif choice == "0":
            cprint("\n  Goodbye. Trade safe!\n", C.CYAN)
            break

        else:
            cprint("  Unknown option '" + choice + "' — try again.", C.YELLOW)
            time.sleep(0.6)

    return data, ACTIVE_TFS


def main():
    select_db_file(silent=True)   # silent at startup; menu option ~ is not
    _db_connect()                 # initialise DB + tables on first run
    _warn_stale_holiday_calendar()
    data = load_data()
    for sym in SYMBOL_MAP.values():
        ensure_symbol(data, sym)

    main_menu_loop(data, ACTIVE_TFS)

if __name__ == "__main__":
    main()
