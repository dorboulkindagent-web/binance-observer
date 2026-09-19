import copy
import json
import math
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import app
import collector as c

NOW = 1_789_804_800_000


def candles(interval="5m", now=NOW, count=60):
    step = c.INTERVAL_SECONDS[interval] * 1000
    # Last candle has closed 1 ms before now; the next candle is still open.
    return [[now - (count - i) * step, "100", "101", "99", str(100 + i), "2",
             now - (count - i - 1) * step - 1] for i in range(count)]


def good(symbol, interval):
    return candles(interval)


class CollectorTests(unittest.TestCase):
    def test_ema_constant(self):
        self.assertEqual(c.ema([10.] * 30, 9), 10.)

    def test_ema_known_values(self):
        self.assertEqual(c.ema([1., 2., 3.], 3), 2.25)

    def test_ema_empty_rejected(self):
        with self.assertRaises(ValueError):
            c.ema([], 9)

    def test_closed_candles_only(self):
        rows = candles() + [[NOW, "1", "1", "1", "999999", "1", NOW + 299999]]
        value = c.observation(rows, NOW, "5m")
        self.assertEqual(value["price"], 159.)
        self.assertEqual(value["candle_close"], NOW - 1)

    def test_insufficient_candles(self):
        with self.assertRaises(ValueError):
            c.observation(candles(count=39), NOW, "5m")

    def test_stale_candles_rejected(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            c.observation(candles(now=NOW - 3600000), NOW, "5m")

    def test_invalid_prices_rejected(self):
        for price in ("NaN", "Infinity", "-1", "0"):
            with self.subTest(price=price):
                rows = candles()
                rows[-1][4] = price
                with self.assertRaises(ValueError):
                    c.observation(rows, NOW, "5m")

    def test_candle_order_rejected(self):
        rows = candles()
        rows[-2], rows[-1] = rows[-1], rows[-2]
        with self.assertRaises(ValueError):
            c.observation(rows, NOW, "5m")

    def test_malformed_payload_isolated(self):
        snapshot = c.collect_snapshot(now_ms=NOW, fetcher=lambda s, i: {"code": -1})
        self.assertEqual(snapshot["status"], "error")
        self.assertEqual(set(snapshot["errors"]), set(c.SYMBOLS))
        self.assertIsNone(snapshot["updated_at"])

    def test_snapshot_succeeds(self):
        snapshot = c.collect_snapshot(now_ms=NOW, fetcher=good)
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["execution"], "NOT_IMPLEMENTED")
        self.assertEqual(len(snapshot["symbols"]), 2)

    def test_partial_failure_preserves_failed_symbol_and_timestamp(self):
        previous = c.collect_snapshot(now_ms=NOW, fetcher=good)
        untouched = copy.deepcopy(previous)
        def partial(symbol, interval):
            if symbol == "BTCUSDT":
                raise TimeoutError()
            return candles(interval, now=NOW + 300000)
        result = c.collect_snapshot(previous, now_ms=NOW + 300000, fetcher=partial)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["symbols"]["BTCUSDT"]["stale"])
        self.assertEqual(result["symbols"]["BTCUSDT"]["updated_at"], previous["updated_at"])
        self.assertEqual(previous, untouched)
        self.assertFalse(result["symbols"]["ETHUSDT"]["stale"])

    def test_total_failure_does_not_advance_success_time(self):
        previous = c.collect_snapshot(now_ms=NOW, fetcher=good)
        def fail(s, i):
            raise urllib.error.URLError("private diagnostic not exposed")
        result = c.collect_snapshot(previous, now_ms=NOW + 300000, fetcher=fail)
        self.assertEqual(result["updated_at"], previous["updated_at"])
        self.assertNotEqual(result["last_attempt_at"], previous["last_attempt_at"])
        self.assertTrue(all(v["stale"] for v in result["symbols"].values()))
        self.assertNotIn("private diagnostic", json.dumps(result))

    def test_never_mixes_intervals(self):
        previous = c.collect_snapshot(now_ms=NOW, fetcher=good)
        result = c.collect_snapshot(previous, interval="1m", now_ms=NOW, fetcher=lambda s, i: [])
        self.assertEqual(result["symbols"], {})

    def test_first_run_failure_creates_both_output_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            snapshot = c.collect_snapshot(now_ms=NOW, fetcher=lambda s, i: [])
            c.write_outputs(snapshot, path)
            self.assertTrue((path / "history.jsonl").exists())
            self.assertEqual(c.load_snapshot(path / "data.json")["status"], "error")
            self.assertEqual((path / "history.jsonl").read_text(), "")

    def test_history_deduplicates_same_candle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            snapshot = c.collect_snapshot(now_ms=NOW, fetcher=good)
            c.write_outputs(snapshot, path)
            c.write_outputs(snapshot, path)
            self.assertEqual(len((path / "history.jsonl").read_text().splitlines()), 1)

    def test_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for index in range(4):
                now = NOW + index * 300000
                snapshot = c.collect_snapshot(now_ms=now, fetcher=lambda s, i: candles(i, now))
                c.write_outputs(snapshot, path, history_limit=2)
            self.assertEqual(len((path / "history.jsonl").read_text().splitlines()), 2)

    def test_corrupt_snapshot_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(c.load_snapshot(path), {})

    def test_public_get_without_auth_headers(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(candles()).encode()
        with mock.patch("collector.urllib.request.urlopen", return_value=response) as urlopen:
            c.fetch_klines("BTCUSDT", "5m")
            request = urlopen.call_args.args[0]
            self.assertTrue(request.full_url.startswith(c.PUBLIC_API + "/api/v3/klines?"))
            self.assertEqual(request.get_method(), "GET")
            self.assertFalse(any("key" in k.lower() or "authorization" in k.lower()
                                 for k in request.headers))

    def test_access_restriction_and_rate_limit_not_retried(self):
        for code in (401, 403, 418, 429, 451):
            error = urllib.error.HTTPError(c.PUBLIC_API, code, "restricted", {}, None)
            with self.subTest(code=code), mock.patch("collector.urllib.request.urlopen", side_effect=error) as urlopen:
                with self.assertRaises(urllib.error.HTTPError):
                    c.fetch_klines("BTCUSDT", "5m")
                self.assertEqual(urlopen.call_count, 1)

    def test_transient_failure_retried_with_bound(self):
        with mock.patch("collector.urllib.request.urlopen", side_effect=TimeoutError()) as urlopen, mock.patch("collector.time.sleep"):
            with self.assertRaises(TimeoutError):
                c.fetch_klines("BTCUSDT", "5m")
            self.assertEqual(urlopen.call_count, 3)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observer = app.Observer(":memory:", fetcher=good)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(cls.observer))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.observer.close()

    def request(self, path, method="GET"):
        request = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as response:
            return response.code, response.headers, response.read()

    def test_health_distinguishes_liveness_from_data(self):
        code, _, body = self.request("/api/health")
        health = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(health["ok"])
        self.assertFalse(health["data_ok"])
        self.assertFalse(health["account_access"])
        self.assertEqual(health["execution"], "NOT_IMPLEMENTED")

    def test_home_page_contains_real_ui(self):
        code, headers, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn('dir="rtl"'.encode(), body)
        self.assertIn(b'id="refresh"', body)
        self.assertIn("text/html", headers["Content-Type"])

    def test_snapshot_route_supports_cache_buster(self):
        code, headers, body = self.request("/data.json?t=123")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["mode"], "OBSERVE_ONLY")

    def test_head_has_no_body(self):
        code, headers, body = self.request("/", "HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_write_methods_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                code, headers, body = self.request("/api/order", method)
                self.assertEqual(code, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_unknown_and_traversal_routes_not_served(self):
        for path in ("/unknown", "/../collector.py", "/.env"):
            self.assertEqual(self.request(path)[0], 404)

    def test_sqlite_history_deduplicates_candles(self):
        with app_observer() as observer:
            observer.refresh(NOW)
            observer.refresh(NOW)
            self.assertEqual(len(observer.rows()), 2)
            self.assertEqual(observer.rows()[0][0], NOW - 1)

    def test_snapshot_is_a_copy(self):
        with app_observer() as observer:
            observer.refresh(NOW)
            snapshot = observer.read()
            snapshot["symbols"].clear()
            self.assertEqual(len(observer.read()["symbols"]), 2)


class app_observer:
    def __enter__(self):
        self.observer = app.Observer(":memory:", fetcher=good)
        return self.observer

    def __exit__(self, *args):
        self.observer.close()


if __name__ == "__main__":
    unittest.main()
