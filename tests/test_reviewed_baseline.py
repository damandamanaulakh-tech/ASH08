"""G0 contract tests — must match the engine that actually runs on main."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ash08 import upstox_client
from ash08.config import (
    BOOK_VALUE,
    CORR_MAX,
    GOVERNOR_EXPOSURE,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    SCORE_SELECT,
    SCORE_WATCH,
    STOP_PCT,
    TARGET_PCT,
    public_config,
)
from ash08.paper_engine import PaperEngine, evaluate_governor
from ash08.scanner import StockMetrics, compute_final_score, evaluate_stock


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class G0ContractTests(unittest.TestCase):
    def test_locked_numbers(self):
        self.assertEqual(BOOK_VALUE, 5_000_000)
        self.assertEqual(SCORE_SELECT, 70.0)
        self.assertEqual(SCORE_WATCH, 55.0)
        self.assertEqual(CORR_MAX, 0.70)
        self.assertEqual(MAX_OPEN_POSITIONS, 10)
        self.assertEqual(MAX_NAME_PCT, 2.5)
        self.assertEqual(STOP_PCT, 3.0)
        self.assertEqual(TARGET_PCT, 6.0)
        self.assertEqual(GOVERNOR_EXPOSURE, {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0})
        cfg = public_config()
        self.assertEqual(cfg["scanner"]["score_select"], 70.0)
        self.assertEqual(cfg["scanner"]["corr_max"], 0.70)
        self.assertNotEqual(cfg["scanner"]["score_select"], 67)
        self.assertNotEqual(cfg["scanner"]["score_watch"], 60)

    def test_select_at_70(self):
        row = evaluate_stock(StockMetrics("OK", 800_000, 25, 1, 0.18, 75, 0.4))
        self.assertTrue(row.hard_pass)
        self.assertGreaterEqual(row.score, SCORE_SELECT)
        self.assertEqual(row.decision, "SELECT")

    def test_watch_band_55_to_70(self):
        row = evaluate_stock(StockMetrics("WATCH", 800_000, 25, 1, 0.05, 60.0, 0.4))
        self.assertAlmostEqual(row.score, 60.0, places=2)
        self.assertEqual(row.decision, "WATCH")

    def test_corr_070_rejects(self):
        row = evaluate_stock(StockMetrics("CORR", 800_000, 25, 1, 0.18, 80, 0.71))
        self.assertFalse(row.hard_pass)
        self.assertEqual(row.decision, "REJECT")

    def test_score_formula(self):
        # mom 0.18 -> 86; quality 75 -> 0.65*86 + 0.35*75 = 82.15
        self.assertEqual(compute_final_score(0.18, 75), 82.15)

    def test_50_lakh_sizing_is_32_shares_at_3840(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            qty = engine.size_qty(50, 3840)
            self.assertEqual(qty, 32)
            self.assertEqual(int(5_000_000 * 0.025 // 3840), 32)

    def test_max_open_skips_eleventh_auto_buy(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=5_000_000)
            rows = [{"symbol": f"SYM{n}", "ltp": 100, "score": 80} for n in range(12)]
            result = engine.auto_buy_selects(rows, price_map={f"SYM{n}": 100 for n in range(12)})
            self.assertEqual(result["bought"], 10)
            self.assertGreaterEqual(result["skipped"], 2)
            self.assertEqual(result["open_count"], 10)

    def test_governor_shape(self):
        l0 = evaluate_governor()
        self.assertEqual(l0.level, "L0_NORMAL")
        self.assertEqual(l0.exposure_pct, 100.0)
        l4 = evaluate_governor(damage=True, q10=True, sell=True)
        self.assertEqual(l4.level, "L4_EXTREME")
        self.assertEqual(l4.exposure_pct, 15.0)
        l3 = evaluate_governor(damage=True, q10=True, any_fii=True)
        self.assertEqual(l3.exposure_pct, 25.0)

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
