"""G4: index tiles are a quote or failed. Never a silent fake print."""
import unittest

from ash08.indices import INDEX_SPECS, fetch_index_tiles, parse_quote_blob


class IndicesG4Tests(unittest.TestCase):
    def test_documented_keys(self):
        ids = [s["id"] for s in INDEX_SPECS]
        self.assertIn("NIFTY50", ids)
        self.assertIn("SENSEX", ids)
        self.assertIn("INDIAVIX", ids)
        for spec in INDEX_SPECS:
            self.assertIn("|", spec["instrument_key"])

    def test_no_token_is_failed_not_number(self):
        out = fetch_index_tiles(lambda keys: (_ for _ in ()).throw(AssertionError("must not fetch")), False)
        self.assertEqual(out["status"], "failed")
        for t in out["tiles"]:
            self.assertIsNone(t["ltp"])
            self.assertEqual(t["status"], "failed")
            self.assertEqual(t["detail"], "no token")

    def test_http_403_is_failed(self):
        def boom(_keys):
            raise RuntimeError("Upstox quotes 403: Error 1010 Access denied")
        out = fetch_index_tiles(boom, True)
        self.assertEqual(out["status"], "failed")
        self.assertIn("403", out["detail"])
        for t in out["tiles"]:
            self.assertIsNone(t["ltp"])
            self.assertEqual(t["status"], "failed")
            self.assertNotIsInstance(t["ltp"], (int, float))

    def test_quotes_fill_ltp(self):
        def fetch(keys):
            return {
                "NSE_INDEX|Nifty 50": {"last_price": 22410.5, "net_change": 35.2},
                "BSE_INDEX|SENSEX": {"last_price": 73800.0, "net_change": -12.0},
                "NSE_INDEX|Nifty Bank": {"last_price": 48100.0, "net_change": 10.0},
                "NSE_INDEX|India VIX": {"last_price": 13.4, "net_change": -0.2},
            }
        out = fetch_index_tiles(fetch, True)
        self.assertEqual(out["status"], "ok")
        by_id = {t["id"]: t for t in out["tiles"]}
        self.assertEqual(by_id["NIFTY50"]["ltp"], 22410.5)
        self.assertEqual(by_id["SENSEX"]["status"], "ok")
        self.assertEqual(by_id["INDIAVIX"]["ltp"], 13.4)

    def test_empty_payload_failed(self):
        out = fetch_index_tiles(lambda keys: {}, True)
        self.assertEqual(out["status"], "failed")
        self.assertTrue(all(t["ltp"] is None for t in out["tiles"]))

    def test_parse_quote(self):
        self.assertEqual(parse_quote_blob({"last_price": "100", "net_change": "1.5"})["ltp"], 100.0)
        self.assertIsNone(parse_quote_blob({})["ltp"])
        self.assertEqual(parse_quote_blob({"ohlc": {"close": 50}})["ltp"], 50.0)


if __name__ == "__main__":
    unittest.main()
