import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import api


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        api._REQUEST_TIMES.clear()
        self.server = api.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", token=None, origin=None, data=None):
        headers = {}
        if token:
            headers["X-ASH08-Token"] = token
        if origin:
            headers["Origin"] = origin
        encoded = None if data is None else json.dumps(data).encode()
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=encoded, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_mutating_get_is_rejected(self):
        status, _, headers = self.request("/api/paper/buy?symbol=TCS")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")

    def test_public_config_exposes_the_same_reviewed_runtime_values(self):
        status, raw, _ = self.request("/api/config")
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(payload["config"]["book_value"], 5_000_000)
        self.assertEqual(payload["config"]["max_open_positions"], 10)
        self.assertEqual(payload["config"]["scanner"]["score_select"], 67)
        self.assertEqual(payload["config"]["scanner"]["score_watch"], 60)

    def test_mutation_requires_configured_valid_token(self):
        with patch.object(api.CONFIG, "API_TOKEN", ""):
            status, _, _ = self.request("/api/scan/run", method="POST", data={})
        self.assertEqual(status, 503)
        with patch.object(api.CONFIG, "API_TOKEN", "secret"):
            status, _, _ = self.request("/api/scan/run", method="POST", token="wrong", data={})
            self.assertEqual(status, 401)
            status, _, _ = self.request("/api/scan/run", method="POST", token="secret", data={})
            self.assertEqual(status, 409)

    def test_cross_origin_mutation_is_rejected_without_wildcard_cors(self):
        with patch.object(api.CONFIG, "API_TOKEN", "secret"):
            with patch.object(api.CONFIG, "TRUSTED_ORIGINS", ()):
                status, _, headers = self.request(
                    "/api/scan/run",
                    method="POST",
                    token="secret",
                    origin="https://attacker.example",
                    data={},
                )
        self.assertEqual(status, 403)
        self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*")

    def test_request_body_limit_is_enforced(self):
        with patch.object(api.CONFIG, "API_TOKEN", "secret"):
            with patch.object(api.CONFIG, "MAX_BODY_BYTES", 2):
                status, _, _ = self.request("/api/scan/run", method="POST", token="secret", data={"x": 1})
        self.assertEqual(status, 413)

    def test_symbol_validation_and_dashboard_escape_guard(self):
        self.assertIsNone(api.SYMBOL_RE.fullmatch("<IMG SRC=X ONERROR=ALERT(1)>"))
        dashboard = (Path(api.ROOT) / "desk" / "ASH08_Desk_Dashboard.html").read_text(encoding="utf-8")
        self.assertIn("function h(value)", dashboard)
        self.assertNotIn("onclick=\\'openDetail(", dashboard)
        self.assertNotIn("≥ 70 auto-buy", dashboard)
        self.assertNotIn("ref_seed", dashboard)
        self.assertIn("fetch('/api/config')", dashboard)


if __name__ == "__main__":
    unittest.main()
