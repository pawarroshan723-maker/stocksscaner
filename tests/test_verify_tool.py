"""
Self-test for verify_upstox.py: the verification tool is itself code that must
be correct, and it is normally only exercised against the live API (which this
sandbox cannot reach).  These tests point it at a local stub that speaks enough
of the Upstox contract to walk every code path.
"""
import io
import json
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

import pytest

import verify_upstox

CANDLES = []
for i in range(60):
    ts = "2024-03-%02dT09:15:00+05:30" % (1 + i % 28)
    CANDLES.append([ts, 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 10000, 0])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = unquote(self.path)
        if "user/profile" in p:
            if self.headers.get("Authorization") != "Bearer GOOD":
                return self._send(401, {"status": "error"})
            return self._send(200, {"data": {"user_name": "tester"}})
        if "market-quote/quotes" in p:
            keys = p.split("instrument_key=")[1].split(",")
            return self._send(200, {"data": {k: {"last_price": 1} for k in keys}})
        if "intraday" in p:
            return self._send(200, {"data": {"candles": CANDLES[-1:]}})
        if "historical-candle" in p:
            parts = p.split("/")
            # /v3/historical-candle/<key>/<unit>/<interval>/<to>/<from>
            key = parts[3]
            if "NOTREAL" in key:
                return self._send(400, {"status": "error",
                                        "errors": [{"errorCode": "UDAPI1000"}]})
            unit = parts[4]
            to_d, from_d = parts[6], parts[7]
            if from_d > to_d:                     # inverted range
                return self._send(400, {"status": "error", "errors": [
                    {"errorCode": "UDAPI1015",
                     "message": "to_date should be greater than from_date"}]})
            span_days = (_d(to_d) - _d(from_d)).days
            cap = {"minutes": 30, "hours": 92, "days": 3650}[unit]
            if span_days > cap:
                return self._send(400, {"status": "error", "errors": [
                    {"errorCode": "UDAPI1148", "message": "invalid date range"}]})
            return self._send(200, {"data": {"candles": CANDLES}})
        return self._send(404, {"status": "error"})


def _d(s):
    import datetime
    return datetime.date(*map(int, s.split("-")))


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def run_tool(tmp_path, server, extra_args=()):
    tok = tmp_path / "tok.json"
    tok.write_text(json.dumps({"access_token": "GOOD"}))
    argv = ["verify_upstox.py", "--token-file", str(tok)] + list(extra_args)
    old_base, old_argv = verify_upstox.BASE, __import__("sys").argv
    verify_upstox.BASE = server
    __import__("sys").argv = argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = verify_upstox.main()
    finally:
        verify_upstox.BASE = old_base
        __import__("sys").argv = old_argv
    return rc, buf.getvalue()


def test_full_run_passes_against_conforming_stub(server, tmp_path):
    rc, out = run_tool(tmp_path, server)
    print(out)
    assert rc == 0, out
    assert "AUTHENTICATION" in out
    assert "RATE LIMITING" in out
    assert "\033[91m  FAIL" not in out


def test_quick_run(server, tmp_path):
    rc, out = run_tool(tmp_path, server, ["--quick"])
    assert rc == 0, out
    assert "EVERY TIMEFRAME" not in out      # skipped in quick mode


def test_bad_token_is_reported(server, tmp_path):
    tok = tmp_path / "tok.json"
    tok.write_text(json.dumps({"access_token": "BAD"}))
    import sys
    old_base, old_argv = verify_upstox.BASE, sys.argv
    verify_upstox.BASE = server
    sys.argv = ["verify_upstox.py", "--token-file", str(tok)]
    try:
        rc = verify_upstox.main()
    finally:
        verify_upstox.BASE = old_base
        sys.argv = old_argv
    assert rc == 1


def test_no_token_returns_2(tmp_path):
    import sys
    old_argv = sys.argv
    sys.argv = ["verify_upstox.py", "--token-file", str(tmp_path / "missing.json")]
    try:
        assert verify_upstox.main() == 2
    finally:
        sys.argv = old_argv
