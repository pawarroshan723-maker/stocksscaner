# Stocks Scanner

A local web workspace for the existing Upstox scanner. The rebuilt core lets you scan one symbol or the 50-symbol configured universe in one timeframe, review saved snapshots, inspect conditional entries, maintain a watchlist, and download a single-snapshot HTML report. The legacy `master-scanner-pro.py` CLI remains available and unchanged.

## Run locally

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run_dashboard.py
```

Open **http://127.0.0.1:5000**. With no saved snapshots the dashboard opens in **demo mode**; its symbols, prices, signals and charts are fictional. Change the source selector to **My snapshots** to see your own data.

### Set up scanning

Use the existing `upstox_token.json` in the repository folder, or set the `UPSTOX_ACCESS_TOKEN` environment variable on the machine running the app. The existing token file uses the format `{ "access_token": "..." }`. Keep tokens private, do not commit them, and never paste them into a public issue or chat. Tokens are not sent to the browser. A configured token may still be expired or rejected by Upstox.

Select **Run a scan**, a symbol (or all configured symbols), and one timeframe. The configured universe is inherited from the legacy scanner and is not automatically updated against the current Nifty constituent list. Batch scans run sequentially and report partial failures. A failed symbol retains any older snapshot, so check each snapshot's scan time.

### Import previous snapshots

Place the existing `master_scanner.db` alongside the scanner, then select **Import old data** in the sidebar. For a database elsewhere, set `SCANNER_LEGACY_DB` to its path before starting the app. Import reads the old database without modifying it, copies valid snapshots and the watchlist, and does not overwrite existing web snapshots. Imported snapshots do not include chart history. Unsupported or invalid rows are skipped. Legacy history, notes and candle caches are not imported in this first version.

The new app stores its data separately in `.scanner-web/dashboard.db` and its candle cache in `.scanner-web/candle-cache.db`; the old database and CLI are not replaced. `SCANNER_WEB_DATA_DIR` changes the new data directory, `UPSTOX_TOKEN_FILE` changes the token-file path, and `PORT` changes the listening port. These local files can contain sensitive data; back them up and keep them out of source control.

## How to read a setup

- A scan is a stored snapshot, **not a live quote**. Provider requests may fall back to cached candles and the most recent candle may still be forming. Check the displayed candle date and scan time.
- For a bullish signal, a breakout requires a completed candle to close above the nearest resistance; a pullback requires a touch of support and a close back above it in the same candle. Bearish breakdown and pullback conditions are the inverse. These are price-only checks, with no additional volume filter.
- Review a **full candle starting after the displayed scan time**. The app does not monitor these conditions, place orders, or guarantee a fill. A later scan recalculates the levels; it does not track an old trigger. A pullback/retest here is not proof that a prior breakout occurred.
- Target, stop and R:R values are **references based on the stored close**, not the actual fill. Reassess the signal and calculate risk again at any real entry. This is research software, not financial advice.

The dashboard uses the existing indicator and signal calculations through an adapter, with a single-timeframe score. It intentionally does not reuse the old retrospective retest bonus. This first core release is not complete feature parity with the CLI; it has no live alerts, automatic trading, backtesting, or multi-timeframe batch selection.

## Safety and testing

The built-in Flask server is for local development. It has no login and should not be exposed to the public internet or an untrusted network. The runner listens on `127.0.0.1` by default; set `HOST` only if you understand the exposure risk. Use one app process because scans share the legacy engine's in-process state.

```bash
python -m pip install pytest
python -m pytest tests/ -q
```
