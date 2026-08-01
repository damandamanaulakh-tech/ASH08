import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ash08 import upstox_client
from ash08.paper_engine import PaperEngine, evaluate_governor
from ash08.scanner import StockMetrics, evaluate_stock


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class ReviewedBaselineTests(unittest.TestCase):
    def test_missing_mandatory_metrics_are_unknown(self):
        row = evaluate_stock(StockMetrics("MISS", mom_6m=0.2, quality_score=80), require_metrics=True)
        self.assertEqual(row.decision, "UNKNOWN")
        self.assertLess(row.coverage, 1.0)
        self.assertFalse(row.hard_pass)

    def test_approved_scanner_case(self):
        row = evaluate_stock(StockMetrics("OK", 800_000, 25, 1, 0.18, 67, 0.4), require_metrics=True)
        self.assertEqual(row.score, 79.35)
        self.assertEqual(row.decision, "SELECT")

    def test_watch_band_starts_at_60(self):
        row = evaluate_stock(StockMetrics("WATCH", 800_000, 25, 1, 0.05, 60.0, 0.4), require_metrics=True)
        self.assertAlmostEqual(row.score, 60.0, places=2)
        self.assertEqual(row.decision, "WATCH")

    def test_50_lakh_sizing_is_32_shares_at_3840(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            sizing = engine.size_qty("TCS", 50, 3840)
            self.assertEqual(sizing["qty"], 32)
            self.assertEqual(sizing["name_headroom"], 125000.0)

    def test_state_reloads_and_idempotency_prevents_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            first = engine.place_order("TCS", "BUY", "MARKET", 50, 3840, idempotency_key="one")
            duplicate = engine.place_order("TCS", "BUY", "MARKET", 50, 3840, idempotency_key="one")
            self.assertEqual(first["order_id"], duplicate["order_id"])
            reloaded = PaperEngine(directory, book_value=5_000_000)
            self.assertEqual(reloaded.open_count(), 1)
            self.assertEqual(len(reloaded.orders), 1)

    def test_missing_mark_does_not_create_profit(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            engine.place_order("TCS", "BUY", "MARKET", 50, 3840)
            before = engine.book_payload()["total_pnl"]
            result = engine.process_marks({})
            after = engine.book_payload()["total_pnl"]
            self.assertEqual(result["marked"], 0)
            self.assertEqual(before, after)

    def test_manual_orders_respect_max_open(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            for number in range(10):
                self.assertEqual(engine.place_order(f"SYM{number}", "BUY", "MARKET", 1, 1000)["status"], "FILLED")
            rejected = engine.place_order("SYM10", "BUY", "MARKET", 1, 1000)
            self.assertEqual(rejected["reason"], "MAX_OPEN_REACHED")

    def test_governor_requires_complete_fresh_evidence(self):
        unverified = evaluate_governor(damage=True, q10=True, sell=True)
        self.assertFalse(unverified.verified)
        l4 = evaluate_governor(damage=True, q10=True, sell=True, any_fii=True, evidence_complete=True, evidence_fresh=True)
        self.assertTrue(l4.verified)
        self.assertEqual(l4.level, "L4")
        self.assertEqual(l4.exposure_pct, 15.0)

    def test_upstox_quote_url_is_encoded_with_urllib_parse(self):
        observed = {}

        def fake_urlopen(request, timeout=0):
            observed["url"] = request.full_url
            return FakeResponse({"data": {"NSE_EQ|INE467B01029": {"last_price": 100}}})

        with patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "token"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = upstox_client.fetch_quotes(["NSE_EQ|INE467B01029"])
        self.assertIn("instrument_key=NSE_EQ|INE467B01029", observed["url"])
        self.assertIn("NSE_EQ|INE467B01029", result)


if __name__ == "__main__":
    unittest.main()
