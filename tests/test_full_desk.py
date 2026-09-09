"""ASH08 full desk: clock, shadow, reports, notes, square-off. Formula locked."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime

from ash08.clock import due_jobs, pulse, schedule_payload
from ash08.config import BOOK_VALUE, STOP_PCT, TARGET_PCT
from ash08.ops import reports, risk, strategy
from ash08.paper_engine import PaperEngine


class FullDeskTests(unittest.TestCase):
    def test_formula_still_ash08(self):
        self.assertEqual(BOOK_VALUE, 50_000_000)
        self.assertEqual(STOP_PCT, 3.0)
        self.assertEqual(TARGET_PCT, 6.0)
        rows = {r["id"]: r for r in strategy()["rows"]}
        self.assertIn("5", rows["BOOK"]["value"].replace(",", "") or "50000000")
        self.assertIn("68", rows["SELECT"]["value"])

    def test_weekday_morning_is_due_once(self):
        wed = datetime(2026, 9, 9, 9, 22)  # IST naive
        due = due_jobs(wed, {})
        self.assertEqual([j["id"] for j in due], ["morning"])
        due2 = due_jobs(wed, {"morning": "2026-09-09"})
        self.assertEqual(due2, [])

    def test_weekend_no_jobs(self):
        sat = datetime(2026, 9, 12, 9, 22)
        self.assertEqual(due_jobs(sat, {}), [])

    def test_notes_and_mode_on_open(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20, mode="intraday")
            self.assertEqual(eng.positions[0]["mode"], "intraday")
            self.assertTrue(eng.set_notes("TCS", "earnings play", "owner,swing"))
            self.assertEqual(eng.positions[0]["notes"], "earnings play")
            self.assertEqual(eng.positions[0]["tags"], ["owner", "swing"])

    def test_expire_limits_and_intraday_square(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("INFY", "BUY", "LIMIT", 1, 1800, source="manual", score=80, sigma=0.20)
            self.assertEqual(eng.expire_day_limits(), 1)
            self.assertEqual(eng.pending_orders, [])
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20, mode="intraday")
            out = eng.square_off_mode("intraday", {"TCS": 1005.0})
            self.assertEqual(out["closed"], ["TCS"])
            self.assertEqual(eng.positions[0]["exit_reason"], "INTRADAY_SQUARE")

    def test_shadow_tracks_skipped_buy(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            rows = [{"symbol": "AAA", "score": 80, "close": 100, "why": "select"}]
            eng.ingest_shadow(rows, [{"symbol": "AAA", "reason": "no_live_ltp"}], {})
            self.assertIn("AAA", eng.shadow)
            eng.mark_shadow({"AAA": 107.0})
            self.assertEqual(eng.shadow["AAA"]["status"], "target")

    def test_reports_by_reason(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            eng.place_order("TCS", "BUY", "MARKET", 1, 1000, source="test", score=80, sigma=0.20)
            eng.close_position("TCS", 1080, reason="OWNER_SELL")
            body = reports(eng)
            self.assertGreaterEqual(body["closed_n"], 1)
            self.assertTrue(any(r["reason"] == "OWNER_SELL" for r in body["by_reason"]))

    def test_risk_has_drawdown_and_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)
            body = risk(eng)
            self.assertEqual(body["reserve_pct"], 5.0)
            self.assertIn("governor", body)

    def test_clock_pulse_marks_job(self):
        with tempfile.TemporaryDirectory() as d:
            eng = PaperEngine(d, book_value=50_000_000)

            def tick_fn(*_a, **_k):
                return {"bought": 0}

            out = pulse(eng, quote_fn=lambda _s: {}, now=datetime(2026, 9, 9, 9, 21), tick_fn=tick_fn)
            self.assertIn("morning", out["ran"])
            self.assertEqual(eng.clock_last.get("morning"), "2026-09-09")
            sched = schedule_payload(eng.clock_last, datetime(2026, 9, 9, 10, 0))
            morning = next(j for j in sched["jobs"] if j["id"] == "morning")
            self.assertTrue(morning["ran_today"])


if __name__ == "__main__":
    unittest.main()
