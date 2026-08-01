import json
import tempfile
import unittest
from pathlib import Path

from ash08.chitty_adopted import (
    ADOPTED_PARAMETERS,
    ChittyAdoptedStore,
    compute_telemetry,
    registry_payload,
)


def bars(count=220, start=100.0):
    rows = []
    for index in range(count):
        close = start + index
        rows.append({
            "date": f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}",
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000 + index,
        })
    return rows


class ChittyAdoptedTests(unittest.TestCase):
    def test_registry_is_exactly_31_and_non_decision(self):
        registry = registry_payload()
        self.assertEqual(31, len(ADOPTED_PARAMETERS))
        self.assertEqual(31, registry["adopted_count"])
        self.assertFalse(registry["decision_impact"])
        self.assertEqual(0, registry["discussion_queue_included"])
        self.assertEqual(31, len({item["id"] for item in ADOPTED_PARAMETERS}))
        self.assertFalse(any(item["id"].startswith("DQ-") for item in ADOPTED_PARAMETERS))

    def test_deterministic_features_are_computed_without_thresholds(self):
        payload = {
            "symbol": "TEST",
            "bars": bars(),
            "benchmark_bars": bars(start=90.0),
            "expected_sessions": 220,
            "metadata": {
                "instrument_key": "NSE_EQ|TEST",
                "isin": "INE000000001",
                "exchange": "NSE",
                "series": "EQ",
                "listing_status": "LISTED",
                "active": True,
                "tradable": True,
                "nifty_200_member": False,
                "nifty_500_member": True,
                "membership_effective_date": "2025-01-01",
                "sector": "Test Sector",
                "adjusted_data": True,
                "adjustment_version": "ca-v1",
            },
            "open_positions": [{"symbol": "OTHER", "sector": "Test Sector"}],
        }
        result = compute_telemetry(payload)
        self.assertFalse(result["decision_impact"])
        self.assertEqual("AVAILABLE", result["features"]["CN-010"]["status"])
        self.assertEqual("AVAILABLE", result["features"]["CN-016"]["status"])
        self.assertEqual("UPTREND", result["features"]["CN-016"]["value"]["state"])
        self.assertEqual(1, result["features"]["CN-024"]["value"])
        self.assertEqual("AVAILABLE", result["features"]["CN-025"]["status"])

    def test_missing_evidence_is_unknown_not_fabricated(self):
        result = compute_telemetry({
            "symbol": "TEST",
            "bars": bars(count=2),
            "metadata": {},
        })
        self.assertEqual("UNKNOWN", result["features"]["CN-008"]["status"])
        self.assertIsNone(result["features"]["CN-008"]["value"])
        self.assertEqual("UNKNOWN", result["features"]["CN-016"]["status"])
        self.assertEqual("UNKNOWN", result["features"]["CN-025"]["status"])

    def test_source_hash_and_duplicate_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChittyAdoptedStore(directory)
            first = store.register_source({"source_name": "a.csv", "content": "same"})
            second = store.register_source({"source_name": "b.csv", "content": "same"})
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(first["sha256"], second["duplicate_of_hash"])
            self.assertEqual("a.csv", second["canonical_source_name"])

    def test_synthetic_lock_and_rule_override_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChittyAdoptedStore(directory)
            with self.assertRaisesRegex(ValueError, "synthetic promotion lock"):
                store.record_audit({
                    "event_type": "research_evidence",
                    "source_class": "synthetic",
                    "production_enabled": True,
                    "matched_cases": 40,
                    "failed_cases": 5,
                })
            with self.assertRaisesRegex(ValueError, "actor and reason"):
                store.record_audit({"event_type": "rule_followed", "rule_followed": False})
            event = store.record_audit({
                "event_type": "research_evidence",
                "source_class": "synthetic",
                "production_enabled": False,
                "matched_cases": 40,
                "failed_cases": 5,
            })
            self.assertTrue(event["promotion_locked"])

    def test_persistence_is_additive_and_idempotent_by_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChittyAdoptedStore(directory)
            payload = {"symbol": "TEST", "bars": bars(count=21), "metadata": {}}
            store.compute_and_save(payload)
            store.compute_and_save(payload)
            status = store.status()
            self.assertEqual(1, len(status["telemetry"]))
            self.assertEqual("TEST", status["telemetry"][0]["symbol"])


if __name__ == "__main__":
    unittest.main()
