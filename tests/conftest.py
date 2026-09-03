"""
Shared test scaffolding for auditing master-scanner-pro.py.

The scanner is a single-file script with a hyphenated filename, so it is loaded
via importlib rather than a normal `import`.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "master-scanner-pro.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("master_scanner_pro", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["master_scanner_pro"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def mod():
    return _load_module()


@pytest.fixture(autouse=True)
def isolated_db(mod, tmp_path, monkeypatch):
    """Every test gets a throwaway SQLite DB in tmp_path."""
    db = tmp_path / "test_scanner.db"
    monkeypatch.setattr(mod, "DB_FILE", str(db), raising=False)
    monkeypatch.setattr(mod, "DATA_FILE", str(db), raising=False)
    yield str(db)


@pytest.fixture(autouse=True)
def fake_token(mod, tmp_path, monkeypatch):
    """A token file so scan entry points don't bail out looking for one."""
    import json
    tok = tmp_path / "upstox_token.json"
    tok.write_text(json.dumps({"access_token": "test-token"}))
    monkeypatch.setattr(mod, "TOKEN_FILE", str(tok), raising=False)
    yield str(tok)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Never actually sleep in tests."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)


# ──────────────────────────────────────────────────────────────
#  Synthetic OHLCV generation
# ──────────────────────────────────────────────────────────────

def make_ohlcv(n=300, seed=7, start="2023-01-02", freq="D", trend=0.0,
               vol=1.0, start_price=1000.0):
    """Deterministic random-walk OHLCV frame with an IST-aware `ts` column."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(trend, vol, n)
    close = start_price * np.exp(np.cumsum(steps) / 100.0)
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    op = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum.reduce([high, close, op])
    low = np.minimum.reduce([low, close, op])
    volume = np.abs(rng.normal(1e6, 2e5, n)) + 1000
    ts = pd.date_range(start=start, periods=n, freq=freq, tz="Asia/Kolkata")
    return pd.DataFrame({
        "ts": ts, "open": op, "high": high, "low": low,
        "close": close, "vol": volume, "oi": 0.0,
    })


@pytest.fixture
def df():
    return make_ohlcv()


@pytest.fixture
def df_uptrend():
    """Strong, persistent uptrend — should read as bullish everywhere."""
    return make_ohlcv(n=400, seed=11, trend=0.9, vol=0.5, start_price=500.0)


@pytest.fixture
def df_downtrend():
    return make_ohlcv(n=400, seed=13, trend=-0.9, vol=0.5, start_price=1500.0)


@pytest.fixture
def df_flat():
    """Perfectly flat series — exercises divide-by-zero / zero-range guards."""
    n = 300
    ts = pd.date_range("2023-01-02", periods=n, freq="D", tz="Asia/Kolkata")
    return pd.DataFrame({
        "ts": ts, "open": 100.0, "high": 100.0, "low": 100.0,
        "close": 100.0, "vol": 1000.0, "oi": 0.0,
    })
