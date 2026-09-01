"""
End-to-end CLI smoke test.

Runs a real scan against the fake Upstox API, then walks EVERY menu option with
populated data.  This is the last line of defence: it catches the class of bug
that unit tests miss — a view that only crashes once it has actual numbers in
it (negative prices, empty target dicts, a TF that produced no signal, …).
"""
import io
from contextlib import redirect_stdout

import pytest

from .fake_upstox import FakeUpstox, make_daily_series

MENU = [
    "S",        # auto scan all
    "I",        # individual scan
    "V",        # view symbol
    "O",        # stock summary
    "B",        # best setups
    "G",        # sector view
    "M",        # heatmap
    "F",        # filter
    "C",        # candle patterns
    "D",        # divergence
    "T",        # statistics
    "X",        # alerts
    "J",        # gap scanner
    "Y",        # volume profile
    "Q",        # next-day gap
    "3",        # momentum screener
    "4",        # trend strength
    "W",        # watchlist
    "H",        # history
    "E",        # export
    "Z",        # notes
    "P",        # custom symbols
    "~",        # switch DB
]


@pytest.fixture
def scanned(mod, monkeypatch):
    """Run a real scan of a few symbols over all TFs via the fake API."""
    api = FakeUpstox(daily=make_daily_series(n=400, seed=5))
    api.intraday = {("days", "1"): make_daily_series(n=2, seed=5).tail(1)}
    monkeypatch.setattr(mod, "requests", api)
    syms = list(mod.SYMBOL_MAP.keys())[:3]
    monkeypatch.setattr(mod, "ACTIVE_KEYS", syms)
    monkeypatch.setattr(mod, "SYMBOL_MAP", {k: mod.SYMBOL_MAP[k] for k in syms})
    # auto_scan ends with input("  Press ENTER to continue...") — under pytest
    # stdin is closed, so swap in a stub that always accepts the default.
    monkeypatch.setattr(mod, "input", lambda prompt="": "", raising=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        data = mod.auto_scan({}, list(mod.TF_CONFIG.keys()))
    assert data, "scan produced no data:\n" + buf.getvalue()
    return data, buf.getvalue()


def _walk(mod, data, seq):
    answers = iter(seq)
    calls = []

    def fake_input(prompt=""):
        try:
            v = next(answers)
        except StopIteration:
            v = "0"
        calls.append((prompt, v))
        return v

    mod.input = fake_input
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main_menu_loop(data, list(mod.TF_CONFIG.keys()))
    return buf.getvalue()


def test_every_menu_option_with_data(mod, scanned):
    data, scan_log = scanned
    if not hasattr(mod, "main_menu_loop"):
        pytest.skip("main() has no separately callable menu loop yet")
    out = _walk(mod, data, MENU + ["0"])
    assert "Traceback" not in out


def test_scan_output_is_sane(mod, scanned):
    data, log = scanned
    assert "Traceback" not in log
    for sym, entry in data.items():
        for tf, e in entry.items():
            if tf == "symbol":
                continue
            assert e["signal"] in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE")
            # no negative or nonsensical prices anywhere
            for k in ("price", "atr", "target1", "target2", "stop"):
                v = (e["targets"] or {}).get(k) if k in (
                    "target1", "target2", "stop") else e.get(k)
                if v is None:
                    continue
                assert mod.safe_float(v, 0.0) >= 0, \
                    "{} {}/{}: {} = {}".format(sym, tf, k, k, v)


def test_scan_then_roundtrip_through_db(mod, scanned):
    data, _ = scanned
    mod.save_data(data)
    back = mod.load_data()
    assert set(back) == set(data)
    for sym in data:
        for tf in data[sym]:
            if tf == "symbol":
                continue
            assert back[sym][tf]["signal"] == data[sym][tf]["signal"]


def test_exports_produce_parseable_files(mod, scanned, tmp_path):
    import csv
    import os
    data, _ = scanned
    os.chdir(str(tmp_path))
    mod.export_csv(data)
    mod.export_report(data, list(mod.TF_CONFIG.keys()))
    mod.export_html(data, list(mod.TF_CONFIG.keys()))
    csvs = [f for f in os.listdir(".") if f.endswith(".csv")]
    if csvs:
        with open(csvs[0]) as fh:
            rows = list(csv.DictReader(fh))
        assert rows, "CSV export produced no rows"
    htmls = [f for f in os.listdir(".") if f.endswith(".html")]
    assert htmls, "no HTML written"
    with open(htmls[0]) as fh:
        body = fh.read()
    assert body.count("<html") == 1
    assert body.rstrip().endswith("</html>")
    # no raw un-escaped script from a note
    assert "<script>alert" not in body


@pytest.fixture
def wired_menu(mod, monkeypatch):
    """Menu-driving fixture with the fake API installed but no scan run."""
    api = FakeUpstox(daily=make_daily_series(n=400, seed=7))
    api.intraday = {("days", "1"): make_daily_series(n=2, seed=7).tail(1)}
    monkeypatch.setattr(mod, "requests", api)
    syms = list(mod.SYMBOL_MAP.keys())[:3]
    monkeypatch.setattr(mod, "ACTIVE_KEYS", syms)
    monkeypatch.setattr(mod, "SYMBOL_MAP", {k: mod.SYMBOL_MAP[k] for k in syms})
    monkeypatch.setattr(mod, "input", lambda prompt="": "", raising=False)
    data = {}
    for name in mod.SYMBOL_MAP.values():
        mod.ensure_symbol(data, name)
    return data


def test_every_menu_option_with_empty_data(mod, wired_menu):
    """The same walk, but before any scan has ever run.

    A fresh install has rows for every symbol with NONE signals and zero
    prices — a different code path from "has data" (divisions by zero, empty
    max()/min(), sorted() over nothing) and the one a new user actually hits.
    """
    out = _walk(mod, wired_menu, MENU + ["0"])
    assert "Traceback" not in out


def test_every_menu_option_with_corrupt_entry(mod, wired_menu):
    """A hand-edited/legacy row must not take a whole screen down."""
    first = sorted(wired_menu)[0]
    bad = mod.empty_entry()
    bad.update({"price": "not-a-number", "rsi": None, "volume": [1, 2],
                "targets": "oops", "52w": 7, "candle_patterns": {},
                "signal": 42, "trend_strength": "strong"})
    wired_menu[first]["DAY"] = bad
    wired_menu[first]["WEEK"] = "totally-corrupt"
    out = _walk(mod, wired_menu, MENU + ["0"])
    assert "Traceback" not in out
