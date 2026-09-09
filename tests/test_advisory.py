"""Today's Advice — real tape, no comparison cards, no invented mcap."""
import unittest

from ash08.advisory import fii_size_mult, payload
from ash08.config import BOOK_VALUE, MCAP_STATUS, SCORE_SELECT, STOP_PCT, TARGET_PCT


class AdvisoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = payload()
        cls.by = {r["symbol"]: r for r in cls.body["rows"]}

    def test_tape_is_not_july_freeze(self):
        self.assertGreaterEqual(self.body["asof"], "2026-09-09")
        self.assertNotEqual(self.body["asof"], "2026-07-17")
        self.assertGreaterEqual(self.body["universe_n"], 180)
        self.assertTrue(self.body["m1_live"])
        self.assertGreaterEqual(self.body["buy_n"], 1)
        self.assertGreaterEqual(self.body["watch_n"], 1)
        b = self.by.get("BHARATFORG")
        if b:
            self.assertNotAlmostEqual(float(b["close"]), 2190.5)

    def test_trent_is_not_2024_lab_rank1(self):
        t = self.by["TRENT"]
        self.assertNotEqual(t["asof"], "2024-07-05")
        if t.get("rank") and t["rank"] > 25:
            self.assertNotEqual(t["action"], "BUY")

    def test_buy_cards_have_size_and_rails(self):
        self.assertGreaterEqual(len(self.body["buy"]), 1)
        for r in self.body["buy"]:
            self.assertGreaterEqual(r["score"], SCORE_SELECT, r["symbol"])
            self.assertIsNotNone(r["stop"])
            self.assertIsNotNone(r["target"])
            self.assertGreater(r["notional"], 0)
            self.assertGreater(r["qty"], 0)
            self.assertIn("Stop", r["why"])
            self.assertAlmostEqual(r["stop"], round(r["close"] * (1 - STOP_PCT / 100), 2))
            self.assertAlmostEqual(r["target"], round(r["close"] * (1 + TARGET_PCT / 100), 2))
            self.assertTrue(str(r["px_source"]).startswith("tape_close_"))

    def test_chitty_failure_cannot_buy(self):
        for r in self.body["buy"]:
            self.assertNotIn("CN-", r["why"], r["symbol"])
        idea = self.by.get("IDEA")
        if idea and "CN-" in (idea.get("why") or ""):
            self.assertNotEqual(idea["action"], "BUY")

    def test_abs_mom_cannot_buy(self):
        for r in self.body["rows"]:
            if r.get("mom6") is not None and r["mom6"] <= 0:
                self.assertNotEqual(r["action"], "BUY", r["symbol"])

    def test_no_invented_mcap(self):
        self.assertEqual(MCAP_STATUS, "PROXY_N200")
        st = self.body["data_status"]
        self.assertEqual(st["mcap"], "PROXY_N200")
        self.assertNotIn("DATA_NEEDED", st["mcap"])
        self.assertEqual(st["t6_delivery"], "SNAPSHOT_1D")
        self.assertEqual(st["fii_size"], "CLOSED")
        self.assertEqual(st["live_ltp"], "MISSING")

    def test_delivery_kept_from_official_bhav(self):
        missing = [r["symbol"] for r in self.body["rows"] if r.get("deliv_per") is None]
        self.assertLessEqual(len(missing), 5, missing[:10])

    def test_fii_positive_full_size(self):
        self.assertEqual(self.body["fii"]["asof"], "2026-08-07")
        self.assertAlmostEqual(self.body["fii"]["fii_net_cr"], 480.24)
        self.assertEqual(self.body["fii"]["size_mult"], 1.0)
        m, why = fii_size_mult(-2500)
        self.assertEqual(m, 0.5)
        self.assertIn("×0.50", why)

    def test_buy_stays_inside_m6(self):
        for r in self.body["buy"]:
            self.assertIsNotNone(r["rank"])
            self.assertLessEqual(r["rank"], 25, r["symbol"])

    def test_buy_cards_are_advice_not_factor_lab(self):
        self.assertEqual(self.body["book"], BOOK_VALUE)
        for r in self.body["buy"]:
            self.assertLessEqual(r["notional"], BOOK_VALUE * 0.05 + r["close"])
        blob = " ".join(r["why"] for r in self.body["buy"])
        self.assertNotIn("F1", blob)
        self.assertNotIn("Core vs", blob)

    def test_mm_mapped_from_yahoo(self):
        self.assertIn("M&M", self.by)


if __name__ == "__main__":
    unittest.main()
