"""Core web rebuild: demo isolation, persistence, scans and report safety."""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from scanner_web import create_app
from scanner_web import legacy
from tests.conftest import make_ohlcv


def entry(**overrides):
    row = {"signal": "BREAKOUT", "price": 100.0, "support": [98.0], "resistance": [102.0],
           "targets": {"target1": 102.0, "target2": 105.0, "stop": 98.0},
           "risk_reward": 1.0, "updated": "2026-10-29 16:12", "candle_date": "2026-10-29",
           "composite_score": 72, "rsi": 60, "rel_vol": 1.2}
    row.update(overrides)
    return row


@pytest.fixture
def webapp(tmp_path, monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    return create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "web"),
                       "LEGACY_DB": str(tmp_path / "old.db"), "TOKEN_FILE": str(tmp_path / "token.json")})


def test_demo_is_explicit_and_not_saved_as_real_data(webapp):
    client = webapp.test_client()
    demo = client.get('/api/snapshots?mode=demo&tf=DAY').get_json()
    real = client.get('/api/snapshots?mode=real&tf=DAY').get_json()
    assert demo['mode'] == 'demo' and 'not market data' in demo['note']
    assert len(demo['rows']) == 12 and not real['rows']
    assert all(r['source'] == 'fictional demo' for r in demo['rows'])
    assert not client.get('/api/meta').get_json()['token_configured']


def test_demo_risk_ratio_matches_reference_targets(webapp):
    rows = webapp.test_client().get('/api/snapshots?mode=demo&tf=DAY').get_json()['rows']
    for row in rows:
        t = row['targets']
        if row['signal'] in ('BREAKOUT', 'BREAKDOWN'):
            expected = round(abs(t['target1']-row['price']) / abs(row['price']-t['stop']), 2)
            assert row['risk_reward'] == expected
        else:
            assert row['risk_reward'] is None


def test_setups_are_conditions_not_fills_or_targets(webapp):
    row = webapp.test_client().get('/api/snapshots?mode=demo&tf=DAY').get_json()['rows'][0]
    s = row['setups']
    assert 'completed DAY candle closes above' in s['breakout']['when']
    assert 'trades at or below' in s['pullback']['when']
    assert 'closes above' in s['pullback']['when']
    assert 'entry_price' not in s['breakout'] and 'target1' not in s['breakout']


def test_store_real_snapshots_and_favorites_are_separate_from_demo(webapp):
    store = webapp.extensions['scanner_store']
    store.save('RELIANCE', 'DAY', entry(), [], 'test')
    client = webapp.test_client()
    r = client.post('/api/favorites', json={'mode':'real','symbol':'RELIANCE','favorite':True})
    assert r.status_code == 200
    data = client.get('/api/snapshots?mode=real&tf=DAY').get_json()
    assert len(data['rows']) == 1 and data['rows'][0]['favorite']
    assert data['rows'][0]['symbol'] == 'RELIANCE'
    assert not any(r['symbol']=='RELIANCE' for r in client.get('/api/snapshots?mode=demo&tf=DAY').get_json()['rows'])
    assert client.get('/api/meta').get_json()['available_timeframes'] == ['DAY']


def test_missing_levels_and_bad_legacy_fields_do_not_invent_a_trigger(webapp):
    store = webapp.extensions['scanner_store']
    store.save('CUSTOM', 'DAY', entry(support=None, resistance=[], targets='bad'), [], 'legacy import')
    r = webapp.test_client().get('/api/snapshots?mode=real&tf=DAY')
    assert r.status_code == 200
    row = r.get_json()['rows'][0]
    assert row['setups']['breakout']['level'] is None
    assert row['setups']['pullback']['level'] is None
    assert row['targets']['target1'] is None


def test_import_preserves_original_and_never_overwrites_web_scan(webapp, tmp_path):
    old = Path(webapp.config['LEGACY_DB'])
    with sqlite3.connect(old) as db:
        db.executescript('CREATE TABLE scan_data (symbol TEXT, tf TEXT, data TEXT); CREATE TABLE watchlist (symbol TEXT);')
        db.executemany('INSERT INTO scan_data VALUES (?,?,?)', [
            ('RELIANCE','DAY',json.dumps(entry(price=90))), ('TCS','15MIN',json.dumps(entry())),
            ('BROKEN','DAY','not-json'), ('EMPTY','DAY',json.dumps({'price':0})),
        ])
        db.execute('INSERT INTO watchlist VALUES (?)', ('TCS',))
    before = hashlib.sha256(old.read_bytes()).hexdigest()
    webapp.extensions['scanner_store'].save('RELIANCE','DAY',entry(price=110),[],'upstox')
    client = webapp.test_client()
    result = client.post('/api/import-legacy', json={}).get_json()
    assert result == {'imported':1, 'skipped':3}
    assert hashlib.sha256(old.read_bytes()).hexdigest() == before
    assert client.get('/api/snapshots?mode=real&tf=DAY').get_json()['rows'][0]['price'] == 110
    tcs = client.get('/api/snapshots?mode=real&tf=15MIN').get_json()['rows'][0]
    assert tcs['favorite'] and tcs['candles'] == [] and tcs['source'] == 'legacy import'
    assert client.post('/api/import-legacy', json={}).get_json()['imported'] == 0


def test_invalid_requests_and_cross_origin_writes_are_rejected(webapp):
    client = webapp.test_client()
    assert client.get('/api/snapshots?mode=bogus&tf=DAY').status_code == 400
    assert client.post('/api/favorites', json=[]).status_code == 400
    assert client.post('/api/scans', json={'symbol':[], 'tf':'DAY'}).status_code == 400
    assert client.post('/api/scans', json={'symbol':'RELIANCE','tf':'DAY'}).status_code == 400
    assert client.post('/api/favorites', json={'mode':'demo','symbol':'DEMO-ALPHA','favorite':True},
                       headers={'Origin':'https://evil.example'}).status_code == 403


def wait_for_job(client, job_id):
    for _ in range(300):
        payload = client.get('/api/scans/' + job_id).get_json()
        if payload['status'] != 'running':
            return payload
        threading.Event().wait(.01)
    pytest.fail('scan job did not finish')


def test_scan_job_saves_real_snapshot_and_hides_token(webapp, monkeypatch):
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'secret-token')
    def fake_scan(symbol, tf, token, path):
        assert (symbol, tf, token) == ('RELIANCE', 'DAY', 'secret-token')
        return entry(), [{'time':'2026-10-29', 'close':100.0}]
    webapp.config['SCAN_FUNCTION'] = fake_scan
    client = webapp.test_client()
    response = client.post('/api/scans', json={'symbol':'RELIANCE','tf':'DAY'})
    assert response.status_code == 202 and b'secret-token' not in response.data
    status = wait_for_job(client, response.get_json()['id'])
    assert status['status'] == 'completed' and 'secret-token' not in str(status)
    row = client.get('/api/snapshots?mode=real&tf=DAY').get_json()['rows'][0]
    assert row['symbol'] == 'RELIANCE' and row['candles'][0]['close'] == 100


def test_scan_failure_is_safe_and_does_not_save(webapp, monkeypatch):
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'secret-token')
    def fail(*args):
        raise ValueError('sensitive upstream response secret-token')
    webapp.config['SCAN_FUNCTION'] = fail
    client = webapp.test_client()
    response = client.post('/api/scans', json={'symbol':'RELIANCE','tf':'DAY'})
    status = wait_for_job(client, response.get_json()['id'])
    assert status['status'] == 'failed' and 'secret-token' not in str(status)
    assert not client.get('/api/snapshots?mode=real&tf=DAY').get_json()['rows']


def test_report_is_downloadable_escaped_and_not_a_trade(webapp):
    symbol = '<script>alert(1)</script>'
    webapp.extensions['scanner_store'].save(symbol, 'DAY', entry(), [], 'legacy import')
    r = webapp.test_client().get('/api/report', query_string={'mode':'real','tf':'DAY','symbol':symbol})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and 'attachment' in r.headers['Content-Disposition']
    assert symbol not in body and '&lt;script&gt;' in body
    assert 'not monitored live' in body and 'actual entry' in body
    assert 'completed DAY candle closes above ₹102.00' in body


def test_legacy_engine_adapter_scans_in_memory_without_network(webapp, monkeypatch, tmp_path):
    engine = legacy.engine()
    frame = make_ohlcv(n=300, start_price=1000)
    monkeypatch.setattr(engine, 'fetch_candles', lambda *a, **kw: frame.copy())
    result, bars = legacy.scan('RELIANCE', 'DAY', 'example', tmp_path / 'isolated-cache.db')
    assert result['price'] > 0 and result['updated'] and result['candle_date']
    assert result['signal'] in ('BREAKOUT','BREAKDOWN','SIDEWAYS','NONE')
    assert len(bars) == 60 and bars[-1]['close'] == result['price']
    assert engine.DB_FILE == str(tmp_path / 'isolated-cache.db')


def test_frontend_and_health_endpoints_are_served(webapp):
    client = webapp.test_client()
    assert client.get('/').status_code == 200
    assert client.get('/static/styles.css').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/api/scans/missing').status_code == 404


def test_batch_scan_reports_partial_failures_and_retains_prior_snapshot(webapp, monkeypatch):
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'secret-token')
    monkeypatch.setattr(legacy, 'catalog', lambda: [
        {'symbol':s, 'sector':'TEST', 'key':s} for s in ('AAA', 'BBB', 'CCC')])
    store = webapp.extensions['scanner_store']
    store.save('BBB', 'DAY', entry(price=88), [], 'legacy import')
    calls = []
    def fake_scan(symbol, tf, token, path):
        calls.append(symbol)
        if symbol == 'BBB':
            raise RuntimeError('provider secret-token')
        return entry(), []
    webapp.config['SCAN_FUNCTION'] = fake_scan
    client = webapp.test_client()
    response = client.post('/api/scans', json={'symbol':'ALL','tf':'DAY'})
    status = wait_for_job(client, response.get_json()['id'])
    assert status['status'] == 'partial' and status['saved'] == 2 and status['failed'] == 1
    assert status['failure_symbols'] == ['BBB'] and status['processed'] == status['total'] == 3
    assert calls == ['AAA', 'BBB', 'CCC'] and 'secret-token' not in str(status)
    rows = {r['symbol']: r for r in client.get('/api/snapshots?mode=real&tf=DAY').get_json()['rows']}
    assert rows['BBB']['price'] == 88 and rows['BBB']['source'] == 'legacy import'
    assert rows['AAA']['source'] == 'Upstox / cache'


def test_batch_stops_on_rejected_token_and_reports_unattempted(webapp, monkeypatch):
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'secret-token')
    monkeypatch.setattr(legacy, 'catalog', lambda: [
        {'symbol':s, 'sector':'TEST', 'key':s} for s in ('AAA', 'BBB', 'CCC')])
    calls = []
    def fake_scan(symbol, tf, token, path):
        calls.append(symbol)
        if symbol == 'BBB':
            raise legacy.engine().TokenError('secret-token')
        return entry(), []
    webapp.config['SCAN_FUNCTION'] = fake_scan
    client = webapp.test_client()
    job = client.post('/api/scans', json={'symbol':'ALL','tf':'DAY'}).get_json()
    status = wait_for_job(client, job['id'])
    assert status['status'] == 'partial' and status['processed'] == 2 and status['total'] == 3
    assert '1 not attempted' in status['message'] and 'secret-token' not in str(status)
    assert calls == ['AAA', 'BBB']


def test_only_one_scan_job_runs_at_a_time(webapp, monkeypatch):
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'secret-token')
    entered, release = threading.Event(), threading.Event()
    def slow_scan(*args):
        entered.set()
        release.wait(2)
        return entry(), []
    webapp.config['SCAN_FUNCTION'] = slow_scan
    client = webapp.test_client()
    try:
        first = client.post('/api/scans', json={'symbol':'RELIANCE','tf':'DAY'})
        assert entered.wait(1)
        assert client.post('/api/scans', json={'symbol':'TCS','tf':'DAY'}).status_code == 409
    finally:
        release.set()
    assert wait_for_job(client, first.get_json()['id'])['status'] == 'completed'


def test_app_download_is_a_source_only_archive(webapp, tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    # Local credentials and databases must never be packaged.
    (Path(webapp.config['DATA_DIR']) / 'secret.db').write_bytes(b'private')
    response = webapp.test_client().get('/api/download-app')
    assert response.status_code == 200
    assert response.mimetype == 'application/zip'
    assert 'attachment' in response.headers['Content-Disposition']
    assert response.headers['Cache-Control'] == 'no-store'
    with ZipFile(BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert 'stockscanner-dashboard/run_dashboard.py' in names
        assert 'stockscanner-dashboard/scanner_web/static/index.html' in names
        assert not any('secret.db' in n or 'token' in n.lower() or '.scanner-web' in n for n in names)
