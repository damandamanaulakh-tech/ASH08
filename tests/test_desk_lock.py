"""YoY lock, factor lab, cash book — the bits that must appear on Render."""
import tempfile
import unittest

from ash08.governor_lock import EVENTS, YOY, payload as hist
from ash08.infinity_gaps import payload as gaps
from ash08.momentum_factors import BOOKS, SNAPSHOT, payload as mom
from ash08.paper_engine import PaperEngine


class DeskLockTests(unittest.TestCase):
    def test_yoy_2008_alpha(self):
        row = next(r for r in YOY if r["year"] == 2008)
        self.assertEqual(row["alpha"], 40.27)
        self.assertEqual(row["strat_cagr"], -25.29)
        self.assertEqual(row["defensive_days"], 108)
        gfc = next(r for r in EVENTS if r["event"] == "2008 GFC Crash")
        self.assertEqual(gfc["alpha"], 29.6)
        self.assertEqual(gfc["dd_saved"], 25.8)
        body = hist()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["yoy"]), 16)
        self.assertEqual(len(body["events"]), 10)

    def test_factors_f1_vs_f5(self):
        f1 = next(r for r in BOOKS if r["id"] == "F1")
        f5 = next(r for r in BOOKS if r["id"] == "F5")
        ew = next(r for r in BOOKS if r["id"] == "EW")
        self.assertEqual(f1["cagr"], 34.57)
        self.assertEqual(f5["cagr"], 32.93)
        self.assertEqual(ew["cagr"], 24.87)
        self.assertEqual(SNAPSHOT["overlap_top30"], 9)
        self.assertEqual(SNAPSHOT["select68"], 154)
        body = mom()
        self.assertIn("M1", body["take_ids"])
        self.assertIn("unchanged", body["scanner_lock"])

    def test_gaps_yoy_wired(self):
        body = gaps()
        yoy = next(r for r in body["rows"] if r["id"] == "G-YOY")
        self.assertEqual(yoy["status"], "WIRED_THIS_PASS")

    def test_empty_book_shows_5cr_cash(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            book = eng.book_payload()
            self.assertEqual(book["cash"], 50_000_000)
            self.assertEqual(book["equity"], 50_000_000)
            self.assertEqual(book["open_count"], 0)
            self.assertEqual(book["max_open"], 500)

    def test_buy_debits_cost_and_close_restores(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            order = eng.place_order(
                "TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20
            )
            self.assertEqual(order["status"], "FILLED")
            qty = order["sized_qty"]
            entry_value = qty * 1000
            debit = entry_value * 1.001
            self.assertAlmostEqual(eng.cash, 50_000_000 - debit, places=1)
            pos = eng.positions[0]
            pos["exit_price"] = 1060
            pos["status"] = "CLOSED"
            pos["exit_reason"] = "TARGET_HIT"
            eng._settle_close(pos)
            # proceeds = 1060 * qty * 0.999 ; cash = start - debit + proceeds
            proceeds = 1060 * qty * 0.999
            expected = 50_000_000 - debit + proceeds
            self.assertAlmostEqual(eng.cash, expected, places=1)
            self.assertGreater(pos["exit_value"], pos["entry_value"])
            self.assertIn("realized_pnl", pos)

    def test_auto_buy_skips_without_price(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            result = eng.auto_buy_selects(
                [{"symbol": "AAA", "score": 80, "vol_sigma": 0.20}],
                price_map={},
            )
            self.assertEqual(result["bought"], 0)
            self.assertEqual(result["skipped_detail"][0]["reason"], "no_live_ltp")


if __name__ == "__main__":
    unittest.main()
