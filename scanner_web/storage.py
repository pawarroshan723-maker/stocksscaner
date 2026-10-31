"""Isolated SQLite persistence and non-destructive legacy snapshot import."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .legacy import TIMEFRAMES


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS snapshots (
                symbol TEXT NOT NULL, tf TEXT NOT NULL, entry TEXT NOT NULL,
                candles TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL,
                PRIMARY KEY(symbol, tf));
              CREATE TABLE IF NOT EXISTS favorites (
                mode TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(mode, symbol));
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, symbol, tf, entry, candles, source="upstox"):
        with self.connect() as db:
            db.execute("""INSERT INTO snapshots(symbol,tf,entry,candles,source)
              VALUES(?,?,?,?,?) ON CONFLICT(symbol,tf) DO UPDATE SET
              entry=excluded.entry,candles=excluded.candles,source=excluded.source""",
              (symbol, tf, json.dumps(entry, allow_nan=False),
               json.dumps(candles, allow_nan=False), source))

    def rows(self, tf):
        with self.connect() as db:
            rows = db.execute("SELECT symbol,entry,candles,source FROM snapshots WHERE tf=? ORDER BY symbol", (tf,)).fetchall()
        out = []
        for symbol, entry, candles, source in rows:
            try:
                obj, bars = json.loads(entry), json.loads(candles)
                if isinstance(obj, dict):
                    out.append({"symbol": symbol, "entry": obj,
                                "candles": bars if isinstance(bars, list) else [], "source": source})
            except (TypeError, ValueError):
                continue
        return out

    def favorites(self, mode):
        with self.connect() as db:
            return {r[0] for r in db.execute("SELECT symbol FROM favorites WHERE mode=?", (mode,))}

    def set_favorite(self, mode, symbol, value):
        with self.connect() as db:
            if value:
                db.execute("INSERT OR IGNORE INTO favorites(mode,symbol) VALUES(?,?)", (mode, symbol))
            else:
                db.execute("DELETE FROM favorites WHERE mode=? AND symbol=?", (mode, symbol))

    def import_legacy(self, path: Path):
        """Copy snapshots and watchlist from an old DB without modifying it.

        Existing web snapshots win: importing an old file must not overwrite
        newer web scans. Corrupt rows are skipped and counted.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError("Legacy database not found")
        uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
        imported = skipped = 0
        with sqlite3.connect(uri, uri=True) as old, self.connect() as new:
            tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "scan_data" not in tables:
                raise ValueError("The selected database has no legacy scan_data table")
            for symbol, tf, blob in old.execute("SELECT symbol,tf,data FROM scan_data"):
                try:
                    entry = json.loads(blob)
                    if (not isinstance(entry, dict) or not isinstance(symbol, str) or
                            not symbol.strip() or not isinstance(tf, str) or tf not in TIMEFRAMES):
                        raise ValueError("invalid row")
                    # Do not materialize empty placeholders as scanned data.
                    if isinstance(entry.get("price"), bool):
                        raise ValueError("invalid price")
                    price = float(entry.get("price", 0))
                    if not (0 < price < float("inf")):
                        raise ValueError("no price")
                    clean = json.dumps(entry, allow_nan=False)
                    cur = new.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)",
                                      (symbol, tf, clean, "[]", "legacy import"))
                    imported += cur.rowcount
                    skipped += 1 - cur.rowcount
                except (ValueError, TypeError, OverflowError):
                    skipped += 1
            if "watchlist" in tables:
                for (symbol,) in old.execute("SELECT symbol FROM watchlist"):
                    if isinstance(symbol, str):
                        new.execute("INSERT OR IGNORE INTO favorites VALUES('real',?)", (symbol,))
        return {"imported": imported, "skipped": skipped}
