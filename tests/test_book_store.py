"""Paper book persist. Temp dirs never touch the packaged git file."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ash08.book_store import dump_state, load, save
from ash08.paper_engine import PaperEngine
from ash08.robot import tick


class BookStoreTests(unittest.TestCase):
    def test_temp_dir_roundtrip_does_not_write_packaged(self):
        packaged = Path(__file__).resolve().parents[1] / "ash08" / "data" / "paper_book.json"
        before = packaged.read_text(encoding="utf-8") if packaged.exists() else None
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            st, src = load(d)
            self.assertEqual(src, "local")
            self.assertTrue(st["positions"])
            self.assertEqual(st["positions"][0]["symbol"], "TCS")
        after = packaged.read_text(encoding="utf-8") if packaged.exists() else None
        self.assertEqual(before, after)

    def test_empty_temp_does_not_pull_github(self):
        with tempfile.TemporaryDirectory() as d:
            st, src = load(d)
            self.assertIsNone(st)
            self.assertEqual(src, "empty")

    def test_save_restore_keeps_cash_and_journal(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            buys = {"BHARATFORG": 1900.0}

            def qfn(_s):
                return {"prices": buys, "source": "yahoo"}

            # only one name in price map — others skip no_live_ltp
            from ash08.advisory import payload as advise

            names = [r["symbol"] for r in advise()["buy"]]
            self.assertIn("BHARATFORG", names)
            tick(eng, quote_fn=qfn, force_buy=True)
            cash = eng.cash
            self.assertLess(cash, 50_000_000)
            st = dump_state(eng)
            save(st, d)
            eng2 = PaperEngine(d, book_value=50_000_000)
            self.assertAlmostEqual(eng2.cash, cash)
            self.assertTrue(eng2.open_symbols())
            self.assertTrue(any(j.get("event") == "BUY" for j in eng2.journal))


if __name__ == "__main__":
    unittest.main()
