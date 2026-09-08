"""G5: five documented segments. Unmapped names stay out. Counts from Core + scan."""
import unittest

from ash08.segments import LOOKUP, SEGMENT_ORDER, segment_of, segment_snapshot


class SegmentsG5Tests(unittest.TestCase):
    def test_five_segments(self):
        self.assertEqual(SEGMENT_ORDER, ("Oil", "Gold", "Metals", "IT", "Finance"))

    def test_documented_membership(self):
        self.assertEqual(segment_of("TCS"), "IT")
        self.assertEqual(segment_of("HDFCBANK"), "Finance")
        self.assertEqual(segment_of("ONGC"), "Oil")
        self.assertEqual(segment_of("TATASTEEL"), "Metals")
        self.assertEqual(segment_of("TITAN"), "Gold")
        self.assertIsNone(segment_of("MARUTI"))
        self.assertIsNone(segment_of("HAL"))
        self.assertIsNone(segment_of("NOTATICKER"))

    def test_exclusive_lookup(self):
        self.assertEqual(len(LOOKUP), len(set(LOOKUP)))

    def test_core_and_scan_counts(self):
        core = ["TCS", "INFY", "HDFCBANK", "MARUTI", "ONGC", "TITAN"]
        scan = [
            {"symbol": "TCS", "decision": "SELECT", "score": 80},
            {"symbol": "INFY", "decision": "WATCH", "score": 60},
            {"symbol": "HDFCBANK", "decision": "UNKNOWN", "score": 0},
            {"symbol": "ONGC", "decision": "REJECT", "score": 40},
        ]
        out = segment_snapshot(core, scan)
        self.assertEqual(len(out["segments"]), 5)
        by = {s["segment"]: s for s in out["segments"]}
        self.assertEqual(by["IT"]["core_count"], 2)
        self.assertEqual(by["IT"]["select_count"], 1)
        self.assertEqual(by["IT"]["watch_count"], 1)
        self.assertEqual(by["IT"]["select"][0]["symbol"], "TCS")
        self.assertEqual(by["Finance"]["unknown_count"], 1)
        self.assertEqual(by["Oil"]["reject_count"], 1)
        self.assertEqual(by["Gold"]["unscanned_count"], 1)
        self.assertEqual(by["Metals"]["core_count"], 0)
        self.assertEqual(out["unmapped_core"], 1)  # MARUTI
        self.assertNotIn("MARUTI", by["IT"]["symbols"])

    def test_empty_is_zeros_not_essays(self):
        out = segment_snapshot([], [])
        self.assertTrue(all(s["core_count"] == 0 and s["select_count"] == 0 for s in out["segments"]))
        self.assertIn("not_an_analysis_essay", out["notes"])


if __name__ == "__main__":
    unittest.main()
