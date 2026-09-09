"""Paper robot: auto-buy Today's BUY, auto-sell on rails, no fake LTP."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from ash08.advisory import payload as advise
from ash08.paper_engine import PaperEngine
from ash08.robot import session_now, tick


class RobotTests(unittest.TestCase):
    def test_no_ltp_buys_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            body = tick(eng, quote_fn=lambda _s: {}, force_buy=True)
            self.assertEqual(body["bought"], 0)
            self.assertEqual(body["sold"], 0)
            self.assertEqual(body["ltp_source"], "no_live_ltp")
            self.assertGreaterEqual(len(body["skipped_no_upstox_ltp"]), 1)
            self.assertEqual(eng.cash, 50_000_000)

    def test_live_ltp_buys_advice_and_sets_rails(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            buys = advise()["buy"]
            self.assertGreaterEqual(len(buys), 1)
            prices = {r["symbol"]: float(r["close"]) for r in buys}

            def qfn(syms):
                return {s: prices[s] for s in syms if s in prices}

            body = tick(eng, quote_fn=qfn, force_buy=True)
            self.assertGreaterEqual(body["bought"], 1)
            self.assertIn(body["ltp_source"], ("live", "upstox", "yahoo"))
            opens = [p for p in eng.positions if p.get("status") == "OPEN"]
            self.assertGreaterEqual(len(opens), 1)
            p = opens[0]
            entry = float(p["entry"])
            self.assertAlmostEqual(p["stop"], round(entry * 0.97, 2), places=2)
            self.assertAlmostEqual(p["target"], round(entry * 1.06, 2), places=2)
            self.assertEqual(p["hold_days"], 15)
            self.assertLess(eng.cash, 50_000_000)

    def test_yahoo_pack_fills_at_yahoo_last_not_tape(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            buys = advise()["buy"]
            yahoo = {r["symbol"]: round(float(r["close"]) * 0.9, 2) for r in buys}

            def qfn(_syms):
                return {"prices": yahoo, "source": "yahoo"}

            body = tick(eng, quote_fn=qfn, force_buy=True)
            self.assertGreaterEqual(body["bought"], 1)
            self.assertEqual(body["ltp_source"], "yahoo")
            opens = [p for p in eng.positions if p.get("status") == "OPEN"]
            self.assertGreaterEqual(len(opens), 1)
            p = opens[0]
            self.assertAlmostEqual(p["entry"], yahoo[p["symbol"]])
            self.assertNotAlmostEqual(p["entry"], next(r["close"] for r in buys if r["symbol"] == p["symbol"]))

    def test_stop_sells_on_live_ltp(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            order = eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            self.assertEqual(order["status"], "FILLED")
            body = tick(
                eng,
                quote_fn=lambda _s: {"TCS": 960.0},
                force_buy=True,
            )
            closed = [p for p in eng.positions if p.get("status") != "OPEN" and p.get("symbol") == "TCS"]
            self.assertTrue(closed)
            self.assertEqual(closed[0]["exit_reason"], "STOP_HIT")
            self.assertEqual(closed[0]["exit_price"], 960.0)
            self.assertGreater(body["sold"], 0)
            self.assertIn("realized_pnl", closed[0])

    def test_target_sells_on_live_ltp(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            tick(eng, quote_fn=lambda _s: {"TCS": 1070.0}, force_buy=True)
            closed = [p for p in eng.positions if p.get("symbol") == "TCS" and p.get("status") != "OPEN"]
            self.assertEqual(closed[0]["exit_reason"], "TARGET_HIT")

    def test_no_live_does_not_fake_a_stop(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            tick(eng, quote_fn=lambda _s: {}, force_buy=True)
            opens = [p for p in eng.positions if p.get("status") == "OPEN" and p.get("symbol") == "TCS"]
            self.assertEqual(len(opens), 1)

    def test_max_hold_needs_live(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            pos = eng.positions[0]
            pos["opened_at"] = (datetime.now(timezone.utc) - timedelta(days=16)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            tick(eng, quote_fn=lambda _s: {}, force_buy=True)
            self.assertEqual(eng.positions[0]["status"], "OPEN")
            tick(eng, quote_fn=lambda _s: {"TCS": 1000.0}, force_buy=True)
            self.assertEqual(eng.positions[0]["status"], "CLOSED")
            self.assertEqual(eng.positions[0]["exit_reason"], "MAX_HOLD")

    def test_after_hours_buys_when_last_exists(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            buys = advise()["buy"]
            prices = {r["symbol"]: float(r["close"]) for r in buys}

            def qfn(syms):
                return {s: prices[s] for s in syms if s in prices}

            body = tick(
                eng,
                quote_fn=qfn,
                now=datetime(2026, 9, 9, 16, 40),
                force_buy=False,
            )
            self.assertGreaterEqual(body["bought"], 1)
            self.assertEqual(len([p for p in eng.positions if p.get("status") == "OPEN"]), body["open_count"])
            self.assertTrue(eng.journal)
            self.assertEqual(eng.journal[-1]["event"], "BUY")

    def test_session_helper_weekday_shape(self):
        s = session_now(datetime(2026, 9, 9, 10, 0))  # Wed
        self.assertIn("buy_window", s)
        self.assertIn("sell_window", s)


if __name__ == "__main__":
    unittest.main()
