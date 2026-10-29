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


# ═════════════════════════════════════════════════════════════
#  DB picker: ENTER must keep the current database
# ═════════════════════════════════════════════════════════════
def _install_dated_db(mod, monkeypatch, tmp_path, name="01-09-2026.db"):
    (tmp_path / name).write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    mod.DB_FILE = name
    mod.DATA_FILE = name


def test_db_picker_enter_keeps_the_current_database(mod, monkeypatch, tmp_path):
    """ENTER must keep the DB you are already on.

    Regression: the prompt read "ENTER = keep current / default" but the code
    was `if raw == '' or raw == '0': chosen = 'master_scanner.db'`. Pressing
    ENTER after loading a dated backup silently switched you to the live scan
    DB — losing your place, and pointing the next save at a different file
    than the one you thought you were reading.
    """
    _install_dated_db(mod, monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    mod.select_db_file(silent=False)
    assert mod.DB_FILE == "01-09-2026.db"
    assert mod.DATA_FILE == "01-09-2026.db"


def test_db_picker_zero_still_selects_the_default(mod, monkeypatch, tmp_path):
    """Option 0 is the explicit way to choose master_scanner.db."""
    _install_dated_db(mod, monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "0")
    mod.select_db_file(silent=False)
    assert mod.DB_FILE == "master_scanner.db"


def test_db_picker_number_selects_that_dated_file(mod, monkeypatch, tmp_path):
    (tmp_path / "05-08-2026.db").write_bytes(b"")
    (tmp_path / "01-09-2026.db").write_bytes(b"")   # newest → listed first
    monkeypatch.chdir(tmp_path)
    mod.DB_FILE = "master_scanner.db"
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    mod.select_db_file(silent=False)
    assert mod.DB_FILE == "01-09-2026.db"


def test_db_picker_invalid_input_keeps_current(mod, monkeypatch, tmp_path):
    _install_dated_db(mod, monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "not-a-number")
    mod.select_db_file(silent=False)
    assert mod.DB_FILE == "01-09-2026.db"


def test_db_picker_out_of_range_keeps_current(mod, monkeypatch, tmp_path):
    _install_dated_db(mod, monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "99")
    mod.select_db_file(silent=False)
    assert mod.DB_FILE == "01-09-2026.db"


def test_divergence_screen_survives_junk_price_and_corrupt_row(mod, wired_menu):
    """Menu option D formatted int(price) raw and read entry[tf_key]
    without coercion — 'junk' price or a scalar TF row killed the loop."""
    first = sorted(wired_menu)[0]
    e = mod.empty_entry()
    e.update({"price": "junk", "signal": "BREAKOUT",
              "rsi_divergence": {"regular_bull": True}})
    wired_menu[first]["DAY"] = e
    wired_menu[first]["WEEK"] = "totally-corrupt"
    out = _walk(mod, wired_menu, ["D", "0"])
    assert "Traceback" not in out


def test_menu_status_line_fits_terminal_width(mod, scanned):
    """Regression: the MENU header was 77 visible cols ("  MENU" + 28 pads +
    "● MARKET CLOSED") against W=74 — it wrapped in a W-wide terminal and the
    divider line visually attached to its tail."""
    data, _ = scanned
    out = _walk(mod, data, ["0"])
    menu_lines = [ln for ln in out.splitlines()
                  if "MENU" in ln and "MARKET" in ln]
    assert menu_lines, "menu status line not rendered"
    for ln in menu_lines:
        assert mod._vlen(ln) <= mod.W, \
            "menu status line is {} cols (W={}): {!r}".format(
                mod._vlen(ln), mod.W, ln)


def test_scan_tf_headers_pluralise_singular_units(mod, scanned):
    """Regression: TF headers concatenated the fixed plural unit from
    TF_CONFIG, printing "1HR (1 hours, 90d lookback)" and
    "DAY (1 days, 2500d lookback)".  Singular intervals must read
    "1 hour" / "1 day"; plural ones ("5 minutes") stay as they are."""
    data, log = scanned
    assert "1 hours" not in log, "singular 'hours' printed in scan log"
    assert "1 days" not in log, "singular 'days' printed in scan log"
    assert "(1 hour, 90d lookback)" in log
    assert "(1 day, 2500d lookback)" in log
    assert "(5 minutes, 30d lookback)" in log   # plural unchanged
