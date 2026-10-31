"""HTTP API and web UI for the rebuilt core scanner."""
from __future__ import annotations

import html
import io
import math
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from . import demo, legacy
from .storage import Store

ROOT = Path(__file__).resolve().parents[1]


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def create_app(config=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(
        DATA_DIR=os.environ.get("SCANNER_WEB_DATA_DIR", str(ROOT / ".scanner-web")),
        LEGACY_DB=os.environ.get("SCANNER_LEGACY_DB", str(ROOT / "master_scanner.db")),
        TOKEN_FILE=os.environ.get("UPSTOX_TOKEN_FILE", str(ROOT / "upstox_token.json")),
    )
    if config:
        app.config.update(config)
    data_dir = Path(app.config["DATA_DIR"])
    store = Store(data_dir / "dashboard.db")
    app.extensions["scanner_store"] = store
    jobs, lock = {}, threading.Lock()

    @app.before_request
    def same_origin_writes():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() != request.host.lower():
                return jsonify(error="Cross-origin request rejected."), 403

    def valid_mode_tf():
        mode = request.args.get("mode", "demo")
        tf = request.args.get("tf", "DAY")
        if mode not in ("demo", "real") or tf not in legacy.TIMEFRAMES:
            raise ValueError("Invalid mode or timeframe")
        return mode, tf

    def load_rows(mode, tf):
        rows = demo.snapshots(tf) if mode == "demo" else store.rows(tf)
        favorites = store.favorites(mode)
        sectors = {item["symbol"]: item["sector"] for item in legacy.catalog()}
        out = []
        for row in rows:
            e = row["entry"]
            signal = e.get("signal") if e.get("signal") in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE") else "NONE"
            targets = e.get("targets") if isinstance(e.get("targets"), dict) else {}
            close = number(e.get("price"))
            setups = legacy.engine().get_conditional_entries(e, tf)
            def positive(value):
                value = number(value)
                return value if value is not None and value > 0 else None
            def levels(value):
                if not isinstance(value, (list, tuple)):
                    return []
                return [v for v in (number(x) for x in value) if v is not None and v > 0]
            out.append({
                "symbol": row["symbol"], "sector": row.get("sector") or sectors.get(row["symbol"], "OTHER"),
                "signal": signal, "price": close if close and close > 0 else None,
                "score": number(e.get("composite_score")), "rsi": number(e.get("rsi")),
                "rvol": number(e.get("rel_vol")), "atr": number(e.get("atr")),
                "risk_reward": positive(e.get("risk_reward")),
                "updated": str(e.get("updated") or ""), "candle_date": str(e.get("candle_date") or ""),
                "reason": str(e.get("reason") or ""),
                "targets": {k: positive(targets.get(k)) for k in ("target1", "target2", "stop")},
                "support": levels(e.get("support")), "resistance": levels(e.get("resistance")),
                "setups": setups, "candles": row.get("candles", []),
                "source": row["source"], "favorite": row["symbol"] in favorites,
            })
        return out

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/download-app")
    def download_app():
        """Package source only; never include local databases or credentials."""
        files = [ROOT / name for name in (
            "README.md", "requirements.txt", "run_dashboard.py",
            "master-scanner-pro.py", "verify_upstox.py", ".gitignore")]
        files.extend((ROOT / "scanner_web").glob("*.py"))
        files.extend(p for p in (ROOT / "scanner_web" / "static").iterdir()
                     if p.suffix in {".html", ".css", ".js"})
        files.extend((ROOT / "tests").glob("*.py"))
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for path in files:
                if path.is_file() and path.resolve().is_relative_to(ROOT.resolve()):
                    archive.write(path, "stockscanner-dashboard/" + path.relative_to(ROOT).as_posix())
        buffer.seek(0)
        response = send_file(buffer, mimetype="application/zip", as_attachment=True,
                             download_name="stockscanner-dashboard.zip", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/meta")
    def meta():
        counts = {tf: len(store.rows(tf)) for tf in legacy.TIMEFRAMES}
        return jsonify(timeframes=legacy.TIMEFRAMES, symbols=legacy.catalog(),
                       token_configured=bool(legacy.read_token(Path(app.config["TOKEN_FILE"]))),
                       legacy_available=Path(app.config["LEGACY_DB"]).is_file(),
                       available_timeframes=[tf for tf in legacy.TIMEFRAMES if counts[tf]],
                       real_count=sum(counts.values()))

    @app.get("/api/snapshots")
    def snapshots():
        try:
            mode, tf = valid_mode_tf()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        rows = load_rows(mode, tf)
        counts = {s: sum(r["signal"] == s for r in rows)
                  for s in ("BREAKOUT", "BREAKDOWN", "SIDEWAYS", "NONE")}
        return jsonify(mode=mode, timeframe=tf, rows=rows, counts=counts,
                       note="Fictional examples, not market data." if mode == "demo" else
                            "Stored snapshots, not a live feed. The latest candle may be unfinished.")

    @app.post("/api/favorites")
    def favorite():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Invalid favorite request"), 400
        mode, symbol, value = payload.get("mode"), payload.get("symbol"), payload.get("favorite")
        if mode not in ("demo", "real") or not isinstance(symbol, str) or not isinstance(value, bool):
            return jsonify(error="Invalid favorite request"), 400
        # Only allow symbols that are actually displayed in this workspace.
        known = ({x[0] for x in demo.SAMPLES} if mode == "demo" else
                 {r["symbol"] for tf in legacy.TIMEFRAMES for r in store.rows(tf)})
        if symbol not in known:
            return jsonify(error="Unknown symbol"), 404
        store.set_favorite(mode, symbol, value)
        return jsonify(ok=True, favorite=value)

    @app.post("/api/import-legacy")
    def import_legacy():
        try:
            return jsonify(store.import_legacy(Path(app.config["LEGACY_DB"])))
        except FileNotFoundError:
            return jsonify(error="Legacy database not found. Configure SCANNER_LEGACY_DB to its path."), 404
        except (ValueError, OSError):
            return jsonify(error="Could not import this legacy database. The original file was not changed."), 400
        except Exception:
            app.logger.exception("Legacy database import failed")
            return jsonify(error="Could not import this legacy database. The original file was not changed."), 400

    def run_job(job_id, symbols, tf, token):
        scan_fn = app.config.get("SCAN_FUNCTION") or legacy.scan
        saved, failed, first_saved, last_error = 0, [], None, ""
        for symbol in symbols:
            with lock:
                jobs[job_id]["current"] = symbol
            try:
                entry, bars = scan_fn(symbol, tf, token, data_dir / "candle-cache.db")
                store.save(symbol, tf, entry, bars, "Upstox / cache")
                saved += 1
                first_saved = first_saved or symbol
            except Exception as exc:
                # Never expose upstream response bodies, headers, token or stack trace.
                rejected = isinstance(exc, legacy.engine().TokenError)
                last_error = ("Upstox rejected the token. Update the local token and try again."
                              if rejected else str(exc) if isinstance(exc, legacy.ScanError) else
                              "Scan failed. Check the provider connection, token and timeframe.")
                failed.append(symbol)
                if rejected:
                    break
            with lock:
                jobs[job_id].update(processed=saved + len(failed), saved=saved, failed=len(failed))
        processed = saved + len(failed)
        remaining = len(symbols) - processed
        status = "completed" if saved == len(symbols) else "partial" if saved else "failed"
        message = (f"Saved {saved} of {len(symbols)} snapshots. {len(failed)} failed"
                   + (f"; {remaining} not attempted" if remaining else "") + ".")
        if last_error:
            message += " " + last_error
        with lock:
            jobs[job_id].update(status=status, symbol=first_saved, tf=tf, current=None,
                                processed=processed, saved=saved, failed=len(failed),
                                failure_symbols=failed, message=message)

    @app.post("/api/scans")
    def start_scan():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Choose a supported symbol and timeframe."), 400
        symbol, tf = payload.get("symbol"), payload.get("tf")
        catalog = [x["symbol"] for x in legacy.catalog()]
        if (not isinstance(symbol, str) or not isinstance(tf, str) or
                (symbol != "ALL" and symbol not in catalog) or tf not in legacy.TIMEFRAMES):
            return jsonify(error="Choose a supported symbol and timeframe."), 400
        symbols = catalog if symbol == "ALL" else [symbol]
        token = legacy.read_token(Path(app.config["TOKEN_FILE"]))
        if not token:
            return jsonify(error="Upstox is not configured. Set UPSTOX_ACCESS_TOKEN or use the existing local token file."), 400
        with lock:
            if any(j["status"] == "running" for j in jobs.values()):
                return jsonify(error="A scan is already running. Please wait for it to finish."), 409
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"status": "running", "symbol": symbol, "tf": tf,
                            "total": len(symbols), "processed": 0, "saved": 0, "failed": 0}
        thread = threading.Thread(target=run_job, args=(job_id, symbols, tf, token), daemon=True)
        thread.start()
        return jsonify(id=job_id, status="running"), 202

    @app.get("/api/scans/<job_id>")
    def scan_status(job_id):
        with lock:
            result = dict(jobs[job_id]) if job_id in jobs else None
        return jsonify(result) if result else (jsonify(error="Scan not found"), 404)

    @app.get("/api/report")
    def report():
        try:
            mode, tf = valid_mode_tf()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        symbol = request.args.get("symbol", "")
        row = next((r for r in load_rows(mode, tf) if r["symbol"] == symbol), None)
        if not row:
            return jsonify(error="Snapshot not found"), 404
        esc = lambda v: html.escape(str(v), quote=True)
        def money(value):
            return "—" if value is None else "₹{:,.2f}".format(value)
        setups = row["setups"]
        items = "".join("<article><h3>" + esc(setups[k]["label"]) + "</h3><p>" + esc(setups[k]["when"]) + "</p></article>"
                        for k in ("breakout", "pullback"))
        targets = row["targets"]
        demo_banner = "Fictional demo data — not market prices." if mode == "demo" else "Stored snapshot — not live market data."
        doc = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(symbol)} · Snapshot report</title><style>body{{font:16px/1.6 system-ui;margin:2rem auto;max-width:850px;padding:0 1rem;color:#1d2b37}}header{{border-bottom:2px solid #0b8a73}}article{{padding:1rem;border:1px solid #d9e1e6;border-radius:8px;margin:.75rem 0}}.note{{background:#fff4d6;padding:1rem;border-radius:8px}}dt{{font-weight:bold}}dd{{margin:0 0 .5rem}}</style><header><h1>{esc(symbol)} <small>· {esc(tf)}</small></h1><p>{esc(demo_banner)}</p></header><p>Candle: {esc(row['candle_date'] or 'unavailable')} · Scan: {esc(row['updated'] or 'unavailable')} IST</p><h2>Reference snapshot</h2><dl><dt>Latest stored close</dt><dd>{money(row['price'])}</dd><dt>Signal</dt><dd>{esc(row['signal'])}</dd><dt>Reference target 1 / target 2 / stop</dt><dd>{money(targets['target1'])} / {money(targets['target2'])} / {money(targets['stop'])}</dd><dt>Reference R:R</dt><dd>{esc(row['risk_reward'] if row['risk_reward'] is not None else '—')}</dd></dl><h2>Conditional entries</h2>{items}<p class="note">{esc(legacy.engine().ENTRY_CONDITION_NOTE)} Reference targets and R:R assume the stored candle close, not an actual entry. These conditions are not monitored live.</p></html>'''
        response = Response(doc, mimetype="text/html")
        # The filename is intentionally fixed, not derived from a custom symbol.
        response.headers["Content-Disposition"] = 'attachment; filename="scanner-snapshot.html"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app
