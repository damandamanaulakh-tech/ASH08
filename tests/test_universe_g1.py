"""G1: Core 150–250, persist across restart, no 1401 dump."""
import json
import tempfile
import unittest
from pathlib import Path

from ash08.config import CORE_MAX, CORE_MIN, CORE_TTL_DAYS
from ash08.core_seed import CORE_SYMBOLS
from ash08.universe import (
    UniverseManager,
    build_core,
    core_count_ok,
    core_is_fresh,
    rows_from_symbols,
)


class UniverseG1Tests(unittest.TestCase):
    def test_seed_pool_is_larger_than_core(self):
        self.assertGreater(len(CORE_SYMBOLS), CORE_MAX)

    def test_core_from_seed_is_in_band(self):
        rows = rows_from_symbols(CORE_SYMBOLS)
        snap = build_core(rows, prefer_symbols=CORE_SYMBOLS)
        self.assertGreaterEqual(snap.count, CORE_MIN)
        self.assertLessEqual(snap.count, CORE_MAX)
        self.assertEqual(snap.count, len(snap.symbols))
        want, seen = [], set()
        for s in CORE_SYMBOLS:
            u = str(s).upper()
            if u in seen:
                continue
            seen.add(u)
            want.append(u)
            if len(want) == CORE_MAX:
                break
        self.assertEqual(snap.symbols, want)
        self.assertTrue(any("pending_G2" in n for n in snap.notes))
        self.assertTrue(all(r.get("adv20") is None for r in snap.rows))

    def test_does_not_invent_liquidity(self):
        rows = rows_from_symbols(["AAA", "BBB"])
        self.assertIsNone(rows[0].adv20)
        self.assertIsNone(rows[0].turnover_cr_5d)

    def test_restart_keeps_fresh_core(self):
        with tempfile.TemporaryDirectory() as directory:
            mgr = UniverseManager(directory)
            first, rebuilt = mgr.ensure_core(CORE_SYMBOLS, force=False)
            self.assertTrue(rebuilt)
            self.assertTrue(CORE_MIN <= first["count"] <= CORE_MAX)
            asof = first["asof"]
            second, rebuilt2 = mgr.ensure_core(CORE_SYMBOLS, force=False)
            self.assertFalse(rebuilt2)
            self.assertEqual(second["asof"], asof)
            self.assertEqual(second["symbols"], first["symbols"])

    def test_stale_1401_dump_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            mgr = UniverseManager(directory)
            Path(directory, "universe_core.json").write_text(json.dumps({
                "asof": "2020-01-01T00:00:00Z",
                "bucket": "core",
                "count": len(CORE_SYMBOLS),
                "symbols": list(CORE_SYMBOLS),
                "rows": [],
                "notes": ["legacy_dump"],
            }))
            self.assertFalse(core_is_fresh(mgr.load_core()))
            snap, rebuilt = mgr.ensure_core(CORE_SYMBOLS, force=False)
            self.assertTrue(rebuilt)
            self.assertTrue(core_count_ok(snap))
            self.assertLessEqual(snap["count"], CORE_MAX)

    def test_force_refresh_rewrites_asof(self):
        with tempfile.TemporaryDirectory() as directory:
            mgr = UniverseManager(directory)
            a, _ = mgr.ensure_core(CORE_SYMBOLS)
            b, rebuilt = mgr.ensure_core(CORE_SYMBOLS, force=True)
            self.assertTrue(rebuilt)
            self.assertGreaterEqual(b["asof"], a["asof"])
            self.assertTrue(CORE_MIN <= b["count"] <= CORE_MAX)

    def test_discovery_is_capped_and_not_core(self):
        with tempfile.TemporaryDirectory() as directory:
            mgr = UniverseManager(directory)
            disc = mgr.rebuild_discovery(CORE_SYMBOLS)
            self.assertLessEqual(disc["count"], 5000)
            self.assertEqual(disc["bucket"], "discovery")
            self.assertIn("not_for_auto_buy", disc["notes"])

    def test_ttl_constant(self):
        self.assertEqual(CORE_TTL_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
