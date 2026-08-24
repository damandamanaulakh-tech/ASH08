import gzip
import json
import os
import unittest
from unittest.mock import Mock, patch

import api
from ash08 import upstox_client
from ash08.universe import InstrumentRow, build_core, normalize_upstox_row


EXACT_TCS_KEY = "NSE_EQ|INE467B01029"


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class _UniverseStore:
    def __init__(self, rows):
        self.rows = rows

    def load_universe(self, bucket):
        return {"rows": self.rows} if bucket == "core" else {"rows": []}


class InstrumentIntegrityTests(unittest.TestCase):
    def setUp(self):
        api._INSTRUMENT_KEYS.clear()
        api._INSTRUMENT_MASTER_LOADED = False

    def test_symbol_derived_key_is_not_an_exact_instrument_key(self):
        self.assertFalse(upstox_client.is_exact_nse_equity_key("NSE_EQ|TCS"))
        self.assertFalse(upstox_client.is_exact_nse_equity_key("NSE_EQ|RELIANCE"))
        self.assertTrue(upstox_client.is_exact_nse_equity_key(EXACT_TCS_KEY))

    def test_master_loader_drops_rows_without_exact_keys(self):
        payload = gzip.compress(json.dumps([
            {
                "exchange": "NSE", "segment": "NSE_EQ", "instrument_type": "EQ",
                "trading_symbol": "TCS", "instrument_key": EXACT_TCS_KEY,
            },
            {
                "exchange": "NSE", "segment": "NSE_EQ", "instrument_type": "EQ",
                "trading_symbol": "FAKE",
            },
        ]).encode())
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            rows = upstox_client.fetch_nse_equity_instruments()
        self.assertEqual([row["symbol"] for row in rows], ["TCS"])
        self.assertEqual(rows[0]["instrument_key"], EXACT_TCS_KEY)

    def test_core_liquidity_missing_values_fail_closed(self):
        missing = InstrumentRow("TCS", instrument_key=EXACT_TCS_KEY)
        complete = InstrumentRow(
            "INFY", instrument_key="NSE_EQ|INE009A01021",
            adv20=500_000, turnover_cr_5d=20,
        )
        snapshot = build_core([missing, complete], target_min=1, target_max=10)
        self.assertEqual(snapshot.symbols, ["INFY"])
        self.assertNotIn("TCS", snapshot.symbols)

    def test_normalizer_rejects_missing_instrument_key(self):
        self.assertIsNone(normalize_upstox_row({"trading_symbol": "TCS"}))

    def test_quote_resolution_uses_persisted_exact_key(self):
        observed = []

        def fetch_quotes(keys):
            observed.extend(keys)
            return {EXACT_TCS_KEY: {"last_price": 3840}}

        mods = {
            "store": lambda: _UniverseStore([{"symbol": "TCS", "instrument_key": EXACT_TCS_KEY}]),
            "fetch_quotes": fetch_quotes,
        }
        with patch.object(api, "MODS", mods):
            with patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "token"}, clear=False):
                quotes = api.quotes_for_symbols(["TCS"])
        self.assertEqual(observed, [EXACT_TCS_KEY])
        self.assertEqual(quotes, {"TCS": 3840.0})

    def test_quote_resolution_refuses_unmapped_symbol(self):
        fetch_quotes = Mock()
        mods = {"store": lambda: _UniverseStore([]), "fetch_quotes": fetch_quotes}
        with patch.object(api, "MODS", mods):
            with patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "token"}, clear=False):
                quotes = api.quotes_for_symbols(["RELIANCE"])
        self.assertEqual(quotes, {})
        fetch_quotes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
