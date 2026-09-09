"""Live quotes: Upstox in session, Yahoo after hours, never REF_LTP."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ash08.quotes import bars_from_yahoo_chart, chart_symbol, quotes_pack, yahoo_symbol

SESSION = datetime(2026, 9, 9, 10, 30)  # Wed in cash session
AFTER = datetime(2026, 9, 9, 16, 40)  # Wed after hours


class QuotesTests(unittest.TestCase):
    def test_yahoo_symbol_ns(self):
        self.assertEqual(yahoo_symbol("BHARATFORG"), "BHARATFORG.NS")
        self.assertEqual(yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(yahoo_symbol("M.M"), "M&M.NS")
        self.assertEqual(yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(chart_symbol("^NSEI"), "^NSEI")

    def test_module_does_not_invent_ref_ltp(self):
        src = (Path(__file__).resolve().parents[1] / "ash08" / "quotes.py").read_text(encoding="utf-8")
        self.assertNotIn("REF_LTP", src)
        self.assertNotIn("2190.5", src)

    def test_yahoo_fills_after_hours_when_upstox_empty(self):
        fake = {
            "BHARATFORG": 1975.6,
            "ADANIENSOL": 1428.6,
            "FEDERALBNK": 343.25,
            "ABCAPITAL": 400.1,
        }

        def yfn(sym):
            return fake.get(str(sym).upper())

        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}):
            pack = quotes_pack(list(fake), data_dir=d, yahoo_fn=yfn, use_cache=False, now=AFTER)
        self.assertEqual(pack["source"], "yahoo")
        self.assertEqual(pack["quote_mode"], "yahoo")
        self.assertEqual(pack["yahoo_n"], 4)
        self.assertEqual(pack["upstox_n"], 0)
        self.assertAlmostEqual(pack["prices"]["BHARATFORG"], 1975.6)
        self.assertNotAlmostEqual(pack["prices"]["BHARATFORG"], 2190.5)

    def test_session_does_not_fall_to_yahoo(self):
        def yfn(_sym):
            return 1975.6

        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}):
            pack = quotes_pack(["BHARATFORG"], data_dir=d, yahoo_fn=yfn, use_cache=False, now=SESSION)
        self.assertEqual(pack["prices"], {})
        self.assertEqual(pack["source"], "no_live_ltp")
        self.assertEqual(pack["quote_mode"], "upstox")
        self.assertEqual(pack["yahoo_n"], 0)

    def test_missing_yahoo_is_empty_not_tape(self):
        def yfn(_sym):
            return None

        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}):
            pack = quotes_pack(["NATIONALUM"], data_dir=d, yahoo_fn=yfn, use_cache=False, now=AFTER)
        self.assertEqual(pack["prices"], {})
        self.assertEqual(pack["source"], "no_live_ltp")

    def test_bars_from_chart_skips_nulls_and_needs_60(self):
        ts = list(range(1_700_000_000, 1_700_000_000 + 70 * 86400, 86400))
        closes = [None] * 5 + [100.0 + i for i in range(65)]
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": ts,
                        "indicators": {
                            "quote": [
                                {
                                    "open": closes,
                                    "high": closes,
                                    "low": closes,
                                    "close": closes,
                                    "volume": [1_000] * len(ts),
                                }
                            ]
                        },
                    }
                ]
            }
        }
        bars = bars_from_yahoo_chart(payload)
        self.assertIsNotNone(bars)
        self.assertEqual(len(bars["closes"]), 65)
        self.assertEqual(bars["closes"][0], 100.0)
        self.assertGreaterEqual(len(bars["dates"][0]), 10)

    def test_bars_from_chart_none_when_thin(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1_700_000_000],
                        "indicators": {"quote": [{"close": [10.0], "open": [10], "high": [10], "low": [10], "volume": [1]}]},
                    }
                ]
            }
        }
        self.assertIsNone(bars_from_yahoo_chart(payload))


if __name__ == "__main__":
    unittest.main()
