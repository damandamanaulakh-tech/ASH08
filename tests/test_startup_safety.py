import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import api


class _Metrics:
    def __init__(self, **values):
        self.values = values


class _Snapshot:
    select_count = 1
    watch_count = 0
    reject_count = 0

    def to_dict(self):
        return {"rows": [{"symbol": "TEST", "decision": "SELECT", "ltp": 100.0}]}


class _Store:
    def save_universe(self, bucket, payload):
        self.universe = payload

    def save_scan(self, payload):
        self.scan = payload


class StartupSafetyTests(unittest.TestCase):
    def test_main_does_not_seed_demo_data(self):
        server = Mock()
        with patch.object(api, "initialize_runtime") as initialize:
            with patch.object(api, "seed_demo_local") as seed:
                with patch.object(api, "ThreadingHTTPServer", return_value=server):
                    api.main()
        initialize.assert_called_once_with()
        seed.assert_not_called()
        server.serve_forever.assert_called_once_with()

    def test_demo_is_disabled_by_default(self):
        with patch.object(api, "DEMO_ENABLED", False):
            result = api.seed_demo_local()
        self.assertFalse(result["ok"])
        self.assertTrue(result["synthetic"])

    def test_demo_never_deletes_paper_state_or_auto_buys(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            paper_state = data_dir / "paper_state.json"
            original = '{"orders":[{"order_id":"keep-me"}],"positions":[]}'
            paper_state.write_text(original)
            engine = Mock()
            engine.open_symbols.return_value = set()
            engine.auto_buy_selects.return_value = {"bought": 1}
            mods = {
                "store": _Store,
                "Metrics": _Metrics,
                "run_scan": lambda metrics, universe_bucket: _Snapshot(),
            }
            with patch.object(api, "DEMO_ENABLED", True):
                with patch.object(api, "DATA_DIR", data_dir):
                    with patch.object(api, "CORE_SYMBOLS", ["TEST"]):
                        with patch.object(api, "MODS", mods):
                            with patch.object(api, "get_engine", return_value=engine):
                                result = api.seed_demo_local()
            self.assertEqual(original, paper_state.read_text())
            self.assertTrue(result["synthetic"])
            self.assertEqual("SYNTHETIC_SCAN", result["auto_paper"]["blocked"])
            engine.auto_buy_selects.assert_not_called()


if __name__ == "__main__":
    unittest.main()
