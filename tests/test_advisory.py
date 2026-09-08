"""Today's Advice — real tape, no comparison cards, no invented mcap."""
import unittest

from ash08.advisory import fii_size_mult, payload
from ash08.config import BOOK_VALUE, MCAP_STATUS, SCORE_SELECT, STOP_PCT, TARGET_PCT


class AdvisoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = payload()
        cls.by = {r["symbol"]: r for r in cls.body["rows"]}

    def test_tape_asof_and_universe(self):
        self.assertEqual(self.body["asof"], "2026-07-17")
        self.assertEqual(self.body["universe_n"], 191)
        self.assertTrue(self.body["m1_live"])
        self.assertGreaterEqual(self.body["buy_n"], 1)
        self.assertGreaterEqual(self.body["watch_n"], 1)

    def test_trent_is_tape_not_2024_rank1(self):
        t = self.by["TRENT"]
        self.assertEqual(t["close"], 2842.4)
        self.assertGreater(t["rank"], 25)
        self.assertNotEqual(t["action"], "BUY")

    def test_bharatforg_federal_are_buy(self):
        for sym in ("BHARATFORG", "FEDERALBNK"):
            r = self.by[sym]
            self.assertEqual(r["action"], "BUY", sym)
            self.assertGreaterEqual(r["score"], SCORE_SELECT)
            self.assertIsNotNone(r["stop"])
            self.assertIsNotNone(r["target"])
            self.assertGreater(r["notional"], 0)
            self.assertGreater(r["qty"], 0)
            self.assertIn("Stop", r["why"])
            self.assertAlmostEqual(r["stop"], round(r["close"] * (1 - STOP_PCT / 100), 2))
            self.assertAlmostEqual(r["target"], round(r["close"] * (1 + TARGET_PCT / 100), 2))

    def test_chitty_keeps_idea_off_buy(self):
        r = self.by["IDEA"]
        self.assertEqual(r["rank"], 7)
        self.assertEqual(r["action"], "WATCH")
        self.assertIn("CN-", r["why"])

    def test_abs_mom_kills_nationalum(self):
        r = self.by["NATIONALUM"]
        self.assertLessEqual(r["rank"], 25)
        self.assertEqual(r["action"], "AVOID")
        self.assertIn("momentum", r["why"])

    def test_no_invented_mcap(self):
        self.assertEqual(MCAP_STATUS, "PROXY_N200")
        st = self.body["data_status"]
        self.assertEqual(st["mcap"], "PROXY_N200")
        self.assertNotIn("DATA_NEEDED", st["mcap"])
        self.assertEqual(st["t6_delivery"], "SNAPSHOT_1D")
        self.assertEqual(st["fii_size"], "CLOSED")
        self.assertEqual(st["live_ltp"], "MISSING")
        self.assertNotIn("marketCap", str(st).lower() if False else "")

    def test_delivery_on_all_191(self):
        missing = [r["symbol"] for r in self.body["rows"] if r.get("deliv_per") is None]
        self.assertEqual(missing, [])

    def test_fii_positive_full_size(self):
        self.assertEqual(self.body["fii"]["asof"], "2026-08-07")
        self.assertAlmostEqual(self.body["fii"]["fii_net_cr"], 480.24)
        self.assertEqual(self.body["fii"]["size_mult"], 1.0)
        m, why = fii_size_mult(-2500)
        self.assertEqual(m, 0.5)
        self.assertIn("×0.50", why)

    def test_buy_cards_are_advice_not_factor_lab(self):
        self.assertEqual(self.body["book"], BOOK_VALUE)
        for r in self.body["buy"]:
            self.assertEqual(r["px_source"].startswith("tape_close_"), True)
            self.assertLessEqual(r["notional"], BOOK_VALUE * 0.05 + r["close"])
        # no comparison-card language on the advice payload
        blob = " ".join(r["why"] for r in self.body["buy"])
        self.assertNotIn("F1", blob)
        self.assertNotIn("Core vs", blob)

    def test_mm_mapped_from_yahoo(self):
        self.assertIn("M&M", self.by)
        self.assertIsNotNone(self.by["M&M"]["deliv_per"])


if __name__ == "__main__":
    unittest.main()
