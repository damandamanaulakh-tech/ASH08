"""G0 contract tests — must match the engine that actually runs on main."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ash08 import upstox_client
from ash08.config import (
    BOOK_VALUE,
    CASH_RESERVE_PCT,
    CONSEC_LOSS_MAX,
    CORR_MAX,
    GOVERNOR_EXPOSURE,
    KILL_DAILY_PCT,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    POSITION_SIZE_VALUE,
    SCORE_NEAR_MISS,
    SCORE_SELECT,
    SCORE_WATCH,
    SECTOR_MAX,
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
        self.assertEqual(BOOK_VALUE, 50_000_000)
        self.assertEqual(SCORE_SELECT, 70.0)
        self.assertEqual(SCORE_NEAR_MISS, 68.0)
        self.assertEqual(SCORE_WATCH, 55.0)
        self.assertEqual(CORR_MAX, 0.70)
        self.assertEqual(MAX_OPEN_POSITIONS, 500)
        self.assertEqual(POSITION_SIZE_VALUE, 100_000.0)
        self.assertEqual(MAX_NAME_PCT, 2.5)
        self.assertEqual(STOP_PCT, 3.0)
        self.assertEqual(TARGET_PCT, 6.0)
        self.assertEqual(GOVERNOR_EXPOSURE, {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0})
        self.assertEqual(KILL_DAILY_PCT, 2.0)
        self.assertEqual(CASH_RESERVE_PCT, 30.0)
        self.assertEqual(CONSEC_LOSS_MAX, 2)
        self.assertEqual(SECTOR_MAX, 2)
        cfg = public_config()
        self.assertEqual(cfg["risk"]["kill_daily_pct"], 2.0)
        self.assertEqual(cfg["chitty"]["adopted"], 31)
        self.assertFalse(cfg["chitty"]["decision_impact"])
        self.assertEqual(cfg["scanner"]["score_select"], 70.0)
        self.assertEqual(cfg["scanner"]["corr_max"], 0.70)
        self.assertNotEqual(cfg["scanner"]["score_select"], 67)
        self.assertNotEqual(cfg["scanner"]["score_watch"], 60)

    def test_select_at_70(self):
        row = evaluate_stock(StockMetrics("OK", 800_000, 25, 1, 0.18, 75, 0.4))
        self.assertTrue(row.hard_pass)
        self.assertGreaterEqual(row.score, SCORE_SELECT)
        self.assertEqual(row.decision, "SELECT")

    def test_missing_is_unknown_not_pass(self):
        row = evaluate_stock(StockMetrics("GAP"))
        self.assertEqual(row.decision, "UNKNOWN")
        self.assertFalse(row.hard_pass)

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

    def test_near_miss_at_68_is_live_gate(self):
        row = evaluate_stock(StockMetrics("NEAR", 800_000, 25, 1, 0.0885, 70.0, 0.4))
        self.assertEqual(row.decision, "NEAR_MISS")
        self.assertGreaterEqual(row.score, SCORE_NEAR_MISS)
        self.assertLess(row.score, SCORE_SELECT)
        self.assertTrue(row.hard_pass)

    def test_order_sell_blocks_select(self):
        row = evaluate_stock(StockMetrics("DUMP", 800_000, 25, 1, 0.18, 75, 0.4, order_signal="sell"))
        self.assertEqual(row.decision, "REJECT")
        self.assertIn("P-ORDER", [h.param_id for h in row.hits if h.status == "FAIL"])

    def test_5cr_sizing_is_26_shares_at_3840(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=50_000_000)
            qty = engine.size_qty(50, 3840)
            self.assertEqual(qty, 26)
            self.assertEqual(int(100_000 // 3840), 26)

    def test_max_open_skips_past_500(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(directory, book_value=50_000_000)
            rows = [{"symbol": f"SYM{n}", "ltp": 100, "score": 80} for n in range(502)]
            result = engine.auto_buy_selects(rows, price_map={f"SYM{n}": 100 for n in range(502)})
            self.assertEqual(result["bought"], 500)
            self.assertGreaterEqual(result["skipped"], 2)
            self.assertEqual(result["open_count"], 500)

    def test_governor_shape(self):
        l0 = evaluate_governor()
        self.assertEqual(l0.level, "L0_NORMAL")
        self.assertEqual(l0.exposure_pct, 100.0)
        l4 = evaluate_governor(damage=True, q10=True, sell=True)
        self.assertEqual(l4.level, "L4_EXTREME")
        self.assertEqual(l4.exposure_pct, 15.0)
        kill = evaluate_governor(day_pnl_pct=-2.0)
        self.assertEqual(kill.level, "L4_EXTREME")
        self.assertEqual(kill.rationale, "kill_daily")
        consec = evaluate_governor(consec_losses=2)
        self.assertEqual(consec.level, "L2_CONFIRMED")
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

    def test_upstox_sends_browser_user_agent(self):
        seen = {}

        def fake_urlopen(request, timeout=0):
            seen["ua"] = request.get_header("User-agent") or request.headers.get("User-Agent")
            return FakeResponse({"data": {}})

        with patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "token"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                upstox_client.fetch_quotes(["NSE_EQ|INE467B01029"])
        self.assertIn("Chrome", seen.get("ua") or "")


if __name__ == "__main__":
    unittest.main()
