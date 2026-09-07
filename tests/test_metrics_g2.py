"""G2: measured metrics or UNKNOWN. No synthetic scan formula."""
import tempfile
import unittest
from datetime import date, timedelta

from ash08.history import HistoryStore, normalize_bars
from ash08.metrics import build_metrics_for_core, metrics_from_bars
from ash08.scanner import StockMetrics, evaluate_stock, run_scan


def make_bars(n=140, start=100.0, volume=300_000.0, last=None):
    last = last or date.today()
    rows = []
    px = start
    for i in range(n):
        d = last - timedelta(days=n - 1 - i)
        px = start * (1.15 ** (i / max(n - 1, 1)))
        rows.append({
            "date": d.isoformat(),
            "open": px * 0.99,
            "high": px * 1.01,
            "low": px * 0.98,
            "close": px,
            "volume": volume,
        })
    return rows


class MetricsG2Tests(unittest.TestCase):
    def test_missing_metrics_are_unknown(self):
        row = evaluate_stock(StockMetrics("MISS", mom_6m=0.2, quality_score=80))
        self.assertEqual(row.decision, "UNKNOWN")
        self.assertFalse(row.hard_pass)
        self.assertLess(row.coverage, 1.0)

    def test_no_synthetic_index_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            metrics = build_metrics_for_core(["AAA", "BBB"], directory)
            self.assertEqual(len(metrics), 2)
            for m in metrics:
                self.assertIsNone(m.mom_6m)
                self.assertIsNone(m.adv20)
            snap = run_scan(metrics)
            self.assertEqual(snap.unknown_count, 2)
            self.assertEqual(snap.select_count, 0)
            self.assertTrue(all(r["decision"] == "UNKNOWN" for r in snap.rows))
            self.assertFalse(store.path_for("AAA").exists())

    def test_bars_produce_measured_hits(self):
        bars = make_bars(140, start=100.0, volume=400_000)
        m = metrics_from_bars("TCS", bars, ltp=None)
        self.assertIsNotNone(m.adv20)
        self.assertGreaterEqual(m.adv20, 200_000)
        self.assertIsNotNone(m.turnover_cr_5d)
        self.assertIsNotNone(m.mom_6m)
        self.assertGreater(m.mom_6m, 0)
        self.assertIsNotNone(m.quality_score)
        self.assertIsNone(m.ltp)
        row = evaluate_stock(m)
        self.assertIn(row.decision, ("SELECT", "WATCH", "REJECT"))
        self.assertNotEqual(row.decision, "UNKNOWN")
        ids = {h.param_id: h for h in row.hits}
        self.assertIn("P-ADV20", ids)
        self.assertTrue(ids["P-ADV20"].passed)
        self.assertIn("adv20=", ids["P-ADV20"].detail)

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            bars = make_bars(80)
            store.save("INFY", bars, source="fixture")
            loaded = store.load("INFY")
            self.assertEqual(len(loaded), 80)
            self.assertEqual(loaded[0]["date"], normalize_bars(bars)[0]["date"])

    def test_empty_book_corr_zero_not_unknown_if_other_fields_ok(self):
        m = metrics_from_bars("TCS", make_bars(140))
        self.assertEqual(m.max_corr_vs_book, 0.0)
        row = evaluate_stock(m)
        self.assertNotEqual(row.decision, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
