"""Live quotes: Upstox first, Yahoo last, never REF_LTP."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ash08.quotes import quotes_pack, yahoo_symbol


class QuotesTests(unittest.TestCase):
    def test_yahoo_symbol_ns(self):
        self.assertEqual(yahoo_symbol("BHARATFORG"), "BHARATFORG.NS")
        self.assertEqual(yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(yahoo_symbol("M.M"), "M&M.NS")
        self.assertEqual(yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")

    def test_module_does_not_invent_ref_ltp(self):
        src = (Path(__file__).resolve().parents[1] / "ash08" / "quotes.py").read_text(encoding="utf-8")
        self.assertNotIn("REF_LTP", src)
        self.assertNotIn("2190.5", src)

    def test_yahoo_fills_when_upstox_empty(self):
        fake = {
            "BHARATFORG": 1975.6,
            "ADANIENSOL": 1428.6,
            "FEDERALBNK": 343.25,
            "ABCAPITAL": 400.1,
        }

        def yfn(sym):
            return fake.get(str(sym).upper())

        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}):
            pack = quotes_pack(list(fake), data_dir=d, yahoo_fn=yfn, use_cache=False)
        self.assertEqual(pack["source"], "yahoo")
        self.assertEqual(pack["yahoo_n"], 4)
        self.assertEqual(pack["upstox_n"], 0)
        self.assertAlmostEqual(pack["prices"]["BHARATFORG"], 1975.6)
        self.assertNotAlmostEqual(pack["prices"]["BHARATFORG"], 2190.5)

    def test_missing_yahoo_is_empty_not_tape(self):
        def yfn(_sym):
            return None

        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}):
            pack = quotes_pack(["NATIONALUM"], data_dir=d, yahoo_fn=yfn, use_cache=False)
        self.assertEqual(pack["prices"], {})
        self.assertEqual(pack["source"], "no_live_ltp")


if __name__ == "__main__":
    unittest.main()
