"""Two-sided desk: SELL, close-all, closed-trade stats, limit rest. Formula unchanged."""
from __future__ import annotations

import tempfile
import unittest

from ash08.config import BOOK_VALUE, STOP_PCT, TARGET_PCT, MAX_HOLD_SESSIONS
from ash08.paper_engine import PaperEngine


class DeskOpsTests(unittest.TestCase):
    def test_formula_lock(self):
        self.assertEqual(BOOK_VALUE, 50_000_000)
        self.assertEqual(STOP_PCT, 3.0)
        self.assertEqual(TARGET_PCT, 6.0)
        self.assertEqual(MAX_HOLD_SESSIONS, 15)

    def test_owner_sell_lands_in_closed_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            buy = eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            self.assertEqual(buy["status"], "FILLED")
            sell = eng.close_position("TCS", 1040, reason="OWNER_SELL", source="manual")
            self.assertEqual(sell["status"], "FILLED")
            self.assertEqual(sell["side"], "SELL")
            closed = [p for p in eng.positions if p.get("status") != "OPEN"]
            self.assertEqual(len(closed), 1)
            self.assertEqual(closed[0]["exit_reason"], "OWNER_SELL")
            self.assertEqual(closed[0]["exit_price"], 1040)
            self.assertIn("realized_pnl", closed[0])
            self.assertIn("hold_days_closed", closed[0])
            book = eng.book_payload()
            self.assertEqual(book["closed_count"], 1)
            self.assertEqual(book["open_count"], 0)
            self.assertGreaterEqual(book["closed_stats"]["total"], 1)
            sides = [o["side"] for o in book["orders"]]
            self.assertIn("SELL", sides)
            self.assertTrue(any(j.get("event") == "SELL" for j in eng.journal))

    def test_sell_without_price_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            out = eng.close_position("TCS", 0, reason="OWNER_SELL")
            self.assertEqual(out["status"], "REJECTED")
            self.assertEqual(out["reason"], "no_live_ltp")
            self.assertEqual(len(eng.open_symbols()), 1)

    def test_close_all_skips_missing_quote(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            eng.place_order("INFY", "BUY", "MARKET", 1, 1800, source="test", score=80, sigma=0.20)
            result = eng.close_all({"TCS": 1010.0})
            self.assertEqual(result["closed"], 1)
            self.assertEqual(result["skipped"][0]["symbol"], "INFY")
            self.assertIn("INFY", eng.open_symbols())
            self.assertNotIn("TCS", eng.open_symbols())

    def test_place_order_sell_is_close(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            out = eng.place_order("TCS", "SELL", "MARKET", 1, 990, source="manual")
            self.assertEqual(out["side"], "SELL")
            self.assertEqual(eng.positions[0]["exit_reason"], "OWNER_SELL")

    def test_limit_rests_then_fills_at_live(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            rest = eng.place_order("TCS", "BUY", "LIMIT", 1, 1000, source="manual", score=80, sigma=0.20)
            self.assertEqual(rest["status"], "OPEN")
            self.assertEqual(len(eng.open_symbols()), 0)
            eng.mark_to_market({"TCS": 1010.0})
            self.assertEqual(len(eng.open_symbols()), 0)
            eng.mark_to_market({"TCS": 995.0})
            self.assertIn("TCS", eng.open_symbols())
            pos = [p for p in eng.positions if p["symbol"] == "TCS"][0]
            self.assertAlmostEqual(pos["entry"], 995.0)
            self.assertAlmostEqual(pos["stop"], round(995.0 * 0.97, 2), places=2)
            self.assertAlmostEqual(pos["target"], round(995.0 * 1.06, 2), places=2)

    def test_rail_sell_writes_sell_order(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            eng.mark_to_market({"TCS": 1065.0})
            closed = [p for p in eng.positions if p.get("status") != "OPEN"]
            self.assertEqual(closed[0]["exit_reason"], "TARGET_HIT")
            sells = [o for o in eng.orders if o.get("side") == "SELL"]
            self.assertTrue(sells)

    def test_closed_stats_win_rate(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            eng.close_position("TCS", 1080, reason="OWNER_SELL")
            eng.place_order("INFY", "BUY", "MARKET", 1, 1800, source="test", score=80, sigma=0.20)
            eng.close_position("INFY", 1700, reason="OWNER_SELL")
            st = eng.book_payload()["closed_stats"]
            self.assertEqual(st["total"], 2)
            self.assertEqual(st["wins"], 1)
            self.assertEqual(st["losses"], 1)
            self.assertEqual(st["win_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
