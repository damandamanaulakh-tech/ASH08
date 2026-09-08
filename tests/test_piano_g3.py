"""G3: piano lists passed/failed/UNKNOWN from last scan. Empty scan → empty lists."""
import unittest

from ash08.piano import piano_from_scan, piano_summary
from ash08.scanner import StockMetrics, evaluate_stock, run_scan


class PianoG3Tests(unittest.TestCase):
    def test_empty_scan_is_empty_lists_not_demo(self):
        out = piano_from_scan({"rows": []}, "P-ADV20")
        self.assertEqual(out["passed"], [])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["unknown"], [])
        self.assertTrue(out["empty_scan"])
        blob = str(out).upper()
        self.assertNotIn("TCS", blob)
        self.assertNotIn("RELIANCE", blob)

    def test_none_scan_empty(self):
        out = piano_from_scan(None, "adv")
        self.assertTrue(out["empty_scan"])
        self.assertEqual(out["param_id"], "P-ADV20")
        self.assertEqual(out["counts"]["n"], 0)

    def test_splits_pass_fail_unknown(self):
        rows = [
            evaluate_stock(StockMetrics("OK", 800_000, 25, 1, 0.18, 75, 0.4)).to_dict(),
            evaluate_stock(StockMetrics("THIN", 50_000, 25, 1, 0.18, 75, 0.4)).to_dict(),
            evaluate_stock(StockMetrics("GAP")).to_dict(),
        ]
        scan = {"rows": rows, "asof": "2026-09-08T00:00:00Z"}
        adv = piano_from_scan(scan, "P-ADV20")
        self.assertEqual([x["symbol"] for x in adv["passed"]], ["OK"])
        self.assertEqual([x["symbol"] for x in adv["failed"]], ["THIN"])
        self.assertEqual([x["symbol"] for x in adv["unknown"]], ["GAP"])
        sel = piano_from_scan(scan, "P-SELECT")
        self.assertEqual([x["symbol"] for x in sel["passed"]], ["OK"])
        self.assertIn("GAP", [x["symbol"] for x in sel["unknown"]])

    def test_every_row_emits_all_scan_gates(self):
        hits = {h.param_id for h in evaluate_stock(StockMetrics("GAP")).hits}
        for pid in ("P-ADV20", "P-TURNOVER", "P-STALE", "P-MOM", "P-CORR", "P-SCORE", "P-SELECT"):
            self.assertIn(pid, hits)

    def test_gov_is_not_a_name_gate(self):
        snap = run_scan([StockMetrics("OK", 800_000, 25, 1, 0.18, 75, 0.4)])
        out = piano_from_scan(snap.to_dict(), "P-GOV", governor={"level": "L0"})
        self.assertEqual(out["passed"], [])
        self.assertEqual(out["governor"]["level"], "L0")

    def test_summary_counts(self):
        snap = run_scan([
            StockMetrics("OK", 800_000, 25, 1, 0.18, 75, 0.4),
            StockMetrics("GAP"),
        ])
        s = piano_summary(snap.to_dict())
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["gates"]["P-ADV20"]["passed"], 1)
        self.assertEqual(s["gates"]["P-ADV20"]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
