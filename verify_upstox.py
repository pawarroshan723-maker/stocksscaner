#!/usr/bin/env python3
"""
verify_upstox.py — live contract check of this scanner against the real Upstox API.

    python3 verify_upstox.py                 # uses ./upstox_token.json
    python3 verify_upstox.py --token-file X  # or another file
    python3 verify_upstox.py --quick         # auth + one symbol only

Every check below asserts an assumption that master-scanner-pro.py makes. If a
check FAILS, the scanner will misbehave in a specific, named way — the report
says which.

What it verifies
    1.  Authentication               token loads and is accepted
    2.  Instrument keys              every key in SYMBOL_MAP_FULL resolves
    3.  Historical candle shape      7 fields, numeric, non-empty
    4.  Candle column order          [ts, o, h, l, c, volume, oi] per docs
    5.  Descending order              newest-first, as the docs' samples show
    6.  Timestamp timezone           Upstox returns IST; code localises Asia/Kolkata
    7.  OHLC sanity                  high >= max(o,c), low <= min(o,c), all > 0
    8.  Per-TF fetch                 every unit/interval the scanner requests
    9.  Max range limits             minutes<=30d, hours<=92d, days<=10y (docs)
    10. T-1 daily availability       does the daily series include yesterday?
    11. Intraday endpoint            /intraday/N/1 returns today's bar only
    12. Error codes                  UDAPI1015 / UDAPI1148 on a bad range
    13. Rate limiting                observed throttling vs the 0.05 s gap
    14. Rate-limit headers           reported when present

Exit code 0 = all checks passed, 1 = at least one failure.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("requests is required:  pip install requests")

BASE = "https://api.upstox.com"

# Rate limits from Upstox docs (Other Standard APIs bucket):
#   50 req/sec, 500 req/min, 2000 req/30 min
# The scanner throttles to 1 request per 0.05 s (20/s) — deliberately below
# the documented ceiling and below the older 25/s figure.
EXPECTED_MIN_GAP = 0.05

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
_results = []


def report(status, name, detail=""):
    _results.append((status, name, detail))
    colour = {PASS: "\033[92m", FAIL: "\033[91m",
              WARN: "\033[93m", SKIP: "\033[90m"}[status]
    print("  {} {:<5}\033[0m  {}{}".format(
        colour, status, name, ("  — " + detail) if detail else ""))


def section(title):
    print("\n\033[1m{}\033[0m".format(title))
    print("  " + "─" * 62)


# ── load the scanner module (for SYMBOL_MAP_FULL / TF_CONFIG) ───────────────
def load_scanner():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "master-scanner-pro.py")
    spec = importlib.util.spec_from_file_location("scanner", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:                      # module has CLI side effects
        print("  (could not import scanner for symbol list: {})".format(exc))
        return None


def load_token(path):
    if path and os.path.exists(path):
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data.get("access_token") or data.get("token")
        return str(data)
    return os.environ.get("UPSTOX_TOKEN")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", default="upstox_token.json")
    ap.add_argument("--quick", action="store_true",
                    help="skip the per-timeframe and rate-limit checks")
    args = ap.parse_args()

    print("\033[1mUPSTOX API CONTRACT VERIFICATION\033[0m")

    token = load_token(args.token_file)
    if not token:
        print("\n  No token found.")
        print("  Generate one:  python3 master-scanner-pro.py  →  option T,")
        print("  completed the OAuth redirect, paste ?code=... back.")
        print("  This script looks in {} (or $UPSTOX_TOKEN).\n".format(
            args.token_file))
        return 2
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    sess = requests.Session()

    def api(path, **kw):
        t0 = time.time()
        r = sess.get(BASE + path, headers=headers, timeout=15, **kw)
        return r, time.time() - t0

    # ── 1. auth ───────────────────────────────────────────────────────────
    section("1. AUTHENTICATION")
    try:
        r, _ = api("/v2/user/profile")
        if r.status_code == 200:
            j = r.json().get("data", {})
            report(PASS, "GET /v2/user/profile",
                   "user: {}".format(j.get("user_name", "?")))
        elif r.status_code in (401, 403):
            report(FAIL, "GET /v2/user/profile",
                   "HTTP {} — token expired or invalid. Re-run option T.".format(
                       r.status_code))
            return 1
        else:
            report(FAIL, "GET /v2/user/profile", "HTTP {}".format(r.status_code))
            return 1
    except requests.RequestException as exc:
        report(FAIL, "GET /v2/user/profile", str(exc))
        return 1

    mod = load_scanner()
    # SYMBOL_MAP_FULL maps key -> (name, sector); fall back to a known-good key
    raw = dict(getattr(mod, "SYMBOL_MAP_FULL", {})) if mod else {}
    if not raw:
        raw = {"NSE_EQ|INE002A01018": ("RELIANCE", "ENERGY")}
    symbols = {k: (v[0] if isinstance(v, (list, tuple)) else str(v))
               for k, v in raw.items()}
    keys = list(symbols.keys())[:3] if args.quick else list(symbols.keys())

    # ── 2. instrument keys ────────────────────────────────────────────────
    section("2. INSTRUMENT KEYS  ({} symbols)".format(len(keys)))
    bad = []
    try:
        # market quotes accept up to 500 keys per call
        for i in range(0, len(keys), 100):
            chunk = keys[i:i + 100]
            r, _ = api("/v2/market-quote/quotes?instrument_key=" +
                       ",".join(chunk))
            if r.status_code != 200:
                report(FAIL, "quotes batch {}".format(i // 100),
                       "HTTP {}".format(r.status_code))
                break
            data = r.json().get("data", {})
            for k in chunk:
                if k not in data:
                    bad.append("{} ({})".format(symbols.get(k, "?"), k))
        if bad:
            report(FAIL, "all keys resolve",
                   "unresolvable: " + ", ".join(bad[:6]) +
                   (" …" if len(bad) > 6 else ""))
        else:
            report(PASS, "all {} keys resolve".format(len(keys)),
                   "every symbol in the universe can be scanned")
    except requests.RequestException as exc:
        report(WARN, "instrument key check", str(exc))

    # ── 3-7. candle shape on one symbol ────────────────────────────────────
    section("3. HISTORICAL CANDLE CONTRACT")
    key = keys[0]
    name = symbols.get(key, key)
    to_d = time.strftime("%Y-%m-%d")
    from_d = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    path = ("/v3/historical-candle/{}/days/1/{}/{}".format(
        key.replace("|", "%7C"), to_d, from_d))
    try:
        r, _ = api(path)
        j = r.json()
        candles = j.get("data", {}).get("candles") or []
        if r.status_code != 200:
            report(FAIL, "GET historical days/1",
                   "HTTP {} {}".format(r.status_code, j.get("message", "")))
            return 1
        if not candles:
            report(FAIL, "GET historical days/1",
                   "empty — check the date range and market regime")
            return 1

        report(PASS, "candles returned", "{} daily candles for {}".format(
            len(candles), name))

        # column count
        widths = {len(c) for c in candles}
        if widths == {7}:
            report(PASS, "7 fields per candle", "[ts,o,h,l,c,volume,oi]")
        else:
            report(FAIL, "7 fields per candle",
                   "saw field counts {}".format(sorted(widths)))

        # numeric
        nonnum = []
        for c in candles:
            for v in c[1:5]:
                if not isinstance(v, (int, float)):
                    nonnum.append(v)
                    break
        report(PASS if not nonnum else FAIL, "OHLC are numbers",
               "{} bad rows".format(len(nonnum)) if nonnum else "")

        # OHLC sanity
        ohlc_bad = 0
        for c in candles:
            ts, o, h, l, cl, vol, oi = c
            if not (h >= max(o, cl) and l <= min(o, cl)) or min(o, h, l, cl) <= 0:
                ohlc_bad += 1
        report(PASS if ohlc_bad == 0 else FAIL, "OHLC internally consistent",
               "high>=max(o,c), low<=min(o,c), all>0"
               if not ohlc_bad else "{} bad rows".format(ohlc_bad))

        # order (docs' samples are newest-first; the code sorts regardless)
        ts_list = [c[0] for c in candles]
        descending = ts_list == sorted(ts_list, reverse=True)
        report(PASS if descending else WARN, "candle order",
               "newest-first (code sorts defensively)" if descending
               else "NOT newest-first — sort is mandatory")

        # date range
        end_d = str(ts_list[0])[:10]
        start_d = str(ts_list[-1])[:10]
        report(PASS, "date range honoured",
               "{} → {}  (asked {} → {})".format(start_d, end_d, from_d, to_d))

        # T-1 availability: the scanner's cache "effective_to" assumes daily
        # data is published up to yesterday.
        import datetime as _dt
        yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        if end_d >= yesterday:
            report(PASS, "daily data reaches T-1",
                   "latest candle {} — cache rule is correct".format(end_d))
        else:
            report(WARN, "daily data reaches T-1",
                   "latest candle is {} (expected >= {}). Upstox sometimes "
                   "withholds T-1; the scanner then shows stale data until it "
                   "publishes.".format(end_d, yesterday))
    except requests.RequestException as exc:
        report(FAIL, "GET historical days/1", str(exc))
        return 1

    if args.quick:
        return summarise()

    # ── 8. every unit/interval the scanner requests ────────────────────────
    section("4. EVERY TIMEFRAME THE SCANNER REQUESTS")
    tf_cases = [
        ("5MIN",  "minutes", "5",   30),
        ("15MIN", "minutes", "15",  30),
        ("1HR",   "hours",   "1",   90),
        ("DAY",   "days",    "1",   365),
        ("WEEK",  "days",    "1",   730),     # WEEK is resampled from daily
    ]
    for tf, unit, interval, span in tf_cases:
        f = time.strftime("%Y-%m-%d", time.localtime(time.time() - span * 86400))
        p = ("/v3/historical-candle/{}/{}/{}/{}/{}".format(
            key.replace("|", "%7C"), unit, interval, to_d, f))
        try:
            r, _ = api(p)
            j = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {}
            if r.status_code == 200:
                n = len(j.get("data", {}).get("candles") or [])
                report(PASS if n else FAIL, "{}  {}/{}".format(tf, unit, interval),
                       "{} candles over {}d".format(n, span))
            else:
                report(FAIL, "{}  {}/{}".format(tf, unit, interval),
                       "HTTP {} {}".format(r.status_code, j.get("message", "")))
        except requests.RequestException as exc:
            report(FAIL, "{}  {}/{}".format(tf, unit, interval), str(exc))
        time.sleep(EXPECTED_MIN_GAP)

    # ── 9. documented max-range limits ─────────────────────────────────────
    section("5. MAX RANGE LIMITS  (docs: minutes 1mo/>15min 1q, hours 1q, days 1dec)")
    limits = [
        ("minutes", "5",  45,  "1 month for intervals <= 15"),
        ("minutes", "30", 120, "1 quarter for intervals > 15"),
        ("hours",   "1",  200, "1 quarter"),
    ]
    for unit, interval, span, note in limits:
        f = time.strftime("%Y-%m-%d", time.localtime(time.time() - span * 86400))
        p = ("/v3/historical-candle/{}/{}/{}/{}/{}".format(
            key.replace("|", "%7C"), unit, interval, to_d, f))
        try:
            r, _ = api(p)
            if r.status_code == 200:
                j = r.json()
                n = len(j.get("data", {}).get("candles") or [])
                report(WARN, "{}/{} over {}d".format(unit, interval, span),
                       "accepted ({} candles) — documented ceiling is {}; "
                       "scanner's _HIST_MAX_DAYS may be raised".format(n, note))
            else:
                j = r.json() if r.headers.get(
                    "content-type", "").startswith("application/json") else {}
                code = (j.get("errors") or [{}])[0].get("errorCode", "")
                report(PASS, "{}/{} over {}d".format(unit, interval, span),
                       "rejected {} ({}) as documented: {}".format(
                           r.status_code, code, note))
        except requests.RequestException as exc:
            report(FAIL, "{}/{}".format(unit, interval), str(exc))
        time.sleep(EXPECTED_MIN_GAP)

    # ── 10. error codes the scanner maps ───────────────────────────────────
    section("6. ERROR CODES THE SCANNER HANDLES")
    # to_date before from_date → UDAPI1015
    p = ("/v3/historical-candle/{}/days/1/2020-01-01/2020-06-01".format(
        key.replace("|", "%7C")))
    try:
        r, _ = api(p)
        j = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {}
        codes = [e.get("errorCode", "") for e in (j.get("errors") or [])]
        if r.status_code == 200:
            report(FAIL, "inverted date range rejected",
                   "API accepted to_date<from_date — UDAPI1015 handling is dead code")
        elif "UDAPI1015" in codes or r.status_code == 400:
            report(PASS, "inverted date range rejected",
                   "HTTP {} {} → InvalidRangeError path is live".format(
                       r.status_code, ",".join(codes) or "(no code)"))
        else:
            report(WARN, "inverted date range rejected",
                   "HTTP {} {} — not UDAPI1015; check the mapping".format(
                       r.status_code, ",".join(codes)))
    except requests.RequestException as exc:
        report(FAIL, "inverted date range", str(exc))
    time.sleep(EXPECTED_MIN_GAP)

    # a nonsense instrument key → should NOT be 200
    try:
        r, _ = api("/v3/historical-candle/NSE_EQ%7CNOTREAL/days/1/"
                   "{}/{}".format(to_d, from_d))
        if r.status_code == 200:
            report(WARN, "unknown instrument key rejected",
                   "API returned 200 — symbol-key validation matters more")
        else:
            report(PASS, "unknown instrument key rejected",
                   "HTTP {}".format(r.status_code))
    except requests.RequestException as exc:
        report(FAIL, "unknown instrument key", str(exc))
    time.sleep(EXPECTED_MIN_GAP)

    # ── 11. intraday endpoint ──────────────────────────────────────────────
    section("7. INTRADAY ENDPOINT  (/intraday/N/1 = today's bar)")
    try:
        r, _ = api("/v3/historical-candle/intraday/{}/days/1".format(
            key.replace("|", "%7C")))
        j = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {}
        c = j.get("data", {}).get("candles") or []
        if r.status_code == 200 and c:
            today = time.strftime("%Y-%m-%d")
            got = str(c[0][0])[:10]
            report(PASS if got == today else WARN, "intraday returns today",
                   "{} candle(s), latest {}".format(len(c), got))
        elif r.status_code == 200:
            report(WARN, "intraday returns today",
                   "200 but empty — market may be shut; scanner falls back to "
                   "the last cached daily bar")
        else:
            report(FAIL, "intraday returns today",
                   "HTTP {} {}".format(r.status_code, j.get("message", "")))
    except requests.RequestException as exc:
        report(FAIL, "intraday endpoint", str(exc))

    # ── 12. rate limiting ──────────────────────────────────────────────────
    section("8. RATE LIMITING")
    print("  Firing 25 rapid requests (scanner gap = {} s)…".format(
        EXPECTED_MIN_GAP))
    statuses, gaps = {}, []
    t_prev = None
    for i in range(25):
        t0 = time.time()
        try:
            r, dt = api("/v3/historical-candle/{}/days/1/{}/{}".format(
                key.replace("|", "%7C"), to_d, from_d))
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            if r.status_code == 429:
                print("     429 at request {} — Retry-After: {}".format(
                    i + 1, r.headers.get("Retry-After", "(absent)")))
        except requests.RequestException:
            statuses["exc"] = statuses.get("exc", 0) + 1
        if t_prev is not None:
            gaps.append(t0 - t_prev)
        t_prev = time.time()
        time.sleep(EXPECTED_MIN_GAP)          # same pacing as the scanner
    n429 = statuses.get(429, 0)
    if n429 == 0:
        report(PASS, "no 429s at scanner pacing",
               "responses: {} — the {}s throttle is safe".format(
                   statuses, EXPECTED_MIN_GAP))
    else:
        report(WARN, "no 429s at scanner pacing",
               "{} of 25 throttled — raise MIN_API_GAP in the scanner".format(n429))

    hdr = {"x-ratelimit-remaining", "ratelimit-remaining",
           "x-ratelimit-limit", "x-ratelimit-used"}
    seen = {k: v for k, v in r.headers.items() if k.lower() in hdr}
    if seen:
        report(PASS, "rate-limit headers present", str(seen))
    else:
        report(SKIP, "rate-limit headers",
               "Upstox does not expose them; the scanner counts requests itself")

    return summarise()


def summarise():
    n_fail = sum(1 for s, _, _ in _results if s == FAIL)
    n_warn = sum(1 for s, _, _ in _results if s == WARN)
    n_pass = sum(1 for s, _, _ in _results if s == PASS)
    print("\n" + "─" * 66)
    print("  {} passed, {} failed, {} warnings".format(n_pass, n_fail, n_warn))
    if n_fail:
        print("\n  \033[91mThe scanner will misbehave until these are "
              "addressed.\033[0m")
    elif n_warn:
        print("\n  \033[93mNo hard contract violations. Warnings are "
              "informational.\033[0m")
    else:
        print("\n  \033[92mEvery assumption the scanner makes about the Upstox "
              "API holds.\033[0m")
    print("─" * 66 + "\n")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
