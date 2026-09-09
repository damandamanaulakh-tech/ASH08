"""Desk jobs AM07 has as pages — reports, risk, alerts, register, strategy, settings.

ASH08 formula is imported from config. This module only *shows and books*.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from ash08.config import (
    BOOK_VALUE,
    BUY_COST_PCT,
    CASH_RESERVE_PCT,
    CONSEC_LOSS_MAX,
    GOVERNOR_EXPOSURE,
    KELLY_FRACTION,
    KELLY_IC,
    KELLY_MAX_PCT,
    KILL_DAILY_PCT,
    MAX_HOLD_SESSIONS,
    MAX_OPEN_POSITIONS,
    MOM_WEIGHT,
    PARAMETER_SET_ID,
    QUAL_WEIGHT,
    SCORE_SELECT,
    SCORE_WATCH,
    SELL_COST_PCT,
    STOP_PCT,
    TARGET_PCT,
)
from ash08.clock import JOBS, schedule_payload


HORIZONS = [
    {"id": "intraday", "label": "Intraday", "hold": "Same day · square 15:25 IST", "rails": "−3% / +6%"},
    {"id": "short", "label": "Short", "hold": "2–5 sessions (cap 15d)", "rails": "−3% / +6%"},
    {"id": "swing", "label": "Swing", "hold": "15 sessions (lock)", "rails": "−3% / +6%"},
    {"id": "positional", "label": "Positional", "hold": "15 sessions (lock)", "rails": "−3% / +6%"},
    {"id": "momentum", "label": "N200 Momentum", "hold": "15 sessions · Today's Advice", "rails": "−3% / +6% · ½-Kelly"},
]


def reports(engine) -> dict:
    closed = [p for p in (engine.positions or []) if p.get("status") != "OPEN"]
    by_reason: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    by_symbol: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    by_day: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for p in closed:
        reason = str(p.get("exit_reason") or "UNKNOWN")
        pnl = float(p.get("realized_pnl") or 0)
        by_reason[reason]["n"] += 1
        by_reason[reason]["pnl"] = round(by_reason[reason]["pnl"] + pnl, 2)
        sym = str(p.get("symbol") or "?")
        by_symbol[sym]["n"] += 1
        by_symbol[sym]["pnl"] = round(by_symbol[sym]["pnl"] + pnl, 2)
        day = str(p.get("closed_at") or "")[:10]
        by_day[day]["n"] += 1
        by_day[day]["pnl"] = round(by_day[day]["pnl"] + pnl, 2)
    reasons = [{"reason": k, **v} for k, v in sorted(by_reason.items(), key=lambda kv: -abs(kv[1]["pnl"]))]
    symbols = [{"symbol": k, **v} for k, v in sorted(by_symbol.items(), key=lambda kv: -abs(kv[1]["pnl"]))][:40]
    days = [{"day": k, **v} for k, v in sorted(by_day.items(), reverse=True) if k][:40]
    eq = list(getattr(engine, "equity_history", None) or [])[-120:]
    return {
        "ok": True,
        "by_reason": reasons,
        "by_symbol": symbols,
        "by_day": days,
        "equity_history": eq,
        "closed_n": len(closed),
        "open_n": len(engine.open_symbols()) if hasattr(engine, "open_symbols") else 0,
    }


def risk(engine, book: Optional[dict] = None) -> dict:
    book = book or (engine.book_payload() if hasattr(engine, "book_payload") else {})
    equity = float(book.get("equity") or engine.cash or BOOK_VALUE)
    peak = float(getattr(engine, "peak_equity", 0) or 0)
    if equity > peak:
        peak = equity
        engine.peak_equity = peak
    dd = round((equity / peak - 1.0) * 100.0, 2) if peak else 0.0
    closed = [p for p in engine.positions if p.get("status") != "OPEN"]
    consec = 0
    for p in reversed(closed):
        if float(p.get("realized_pnl") or 0) < 0:
            consec += 1
        else:
            break
    segs: Dict[str, int] = Counter()
    for p in engine.positions:
        if p.get("status") != "OPEN":
            continue
        segs[str(p.get("segment") or p.get("mode") or "unmapped")] += 1
    gov = engine.governor.to_dict() if hasattr(engine.governor, "to_dict") else {}
    return {
        "ok": True,
        "governor": gov,
        "ladder": GOVERNOR_EXPOSURE,
        "equity": equity,
        "peak_equity": peak,
        "drawdown_pct": dd,
        "cash": book.get("cash"),
        "reserve_pct": CASH_RESERVE_PCT,
        "reserve_floor": book.get("reserve_floor"),
        "deployed_pct": book.get("deployed_pct"),
        "kill_daily_pct": KILL_DAILY_PCT,
        "consec_losses": consec,
        "consec_max": CONSEC_LOSS_MAX,
        "open_n": book.get("open_count") or 0,
        "max_open": MAX_OPEN_POSITIONS,
        "segments": [{"name": k, "n": v} for k, v in segs.items()],
        "kill_armed": dd <= -KILL_DAILY_PCT or consec >= CONSEC_LOSS_MAX or str(gov.get("level") or "").startswith("L4"),
    }


def alerts(engine, robot: Optional[dict] = None) -> dict:
    hot = ("SELL", "STOP", "KILL", "FAILED", "STALE", "REJECT", "CLOCK_FAILED", "LIMIT_EXPIRED", "SQUARE")
    rows = []
    for j in reversed(list(getattr(engine, "journal", None) or [])):
        ev = str(j.get("event") or "")
        reason = str(j.get("reason") or "")
        if any(h in ev.upper() or h in reason.upper() for h in hot) or ev in ("SELL", "CLOCK_FAILED", "LIMIT_EXPIRED"):
            rows.append(j)
        if len(rows) >= 40:
            break
    skips = (robot or {}).get("skipped_detail") or []
    return {"ok": True, "n": len(rows), "rows": rows, "skips": skips[:20]}


def register(advise: dict) -> dict:
    rows = []
    for r in (advise.get("rows") or advise.get("buy") or []) + (advise.get("watch") or []):
        steps = r.get("steps") or []
        rows.append({
            "symbol": r.get("symbol"),
            "action": r.get("action") or r.get("decision"),
            "score": r.get("score"),
            "rank": r.get("rank"),
            "why": r.get("why"),
            "pass_n": sum(1 for s in steps if s.get("status") in ("PASS", "SKIP")),
            "fail_n": sum(1 for s in steps if s.get("status") == "FAIL"),
            "qty": r.get("qty"),
            "notional": r.get("notional"),
        })
    # de-dupe
    seen = set()
    uniq = []
    for r in rows:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        uniq.append(r)
    return {
        "ok": True,
        "n": len(uniq),
        "buy_n": advise.get("buy_n"),
        "watch_n": advise.get("watch_n"),
        "avoid_n": advise.get("avoid_n"),
        "asof": advise.get("asof"),
        "rows": uniq[:250],
    }


def strategy() -> dict:
    return {
        "ok": True,
        "parameter_set_id": PARAMETER_SET_ID,
        "locked": True,
        "rows": [
            {"id": "BOOK", "item": "Paper capital", "value": f"₹{BOOK_VALUE:,.0f}", "note": "ASH08 lock — not AM07 ₹50L"},
            {"id": "KELLY", "item": "Sizing", "value": f"{KELLY_FRACTION}× Kelly · IC {KELLY_IC} · cap {KELLY_MAX_PCT*100:.0f}%", "note": "Locked"},
            {"id": "SELECT", "item": "SELECT / BUY floor", "value": str(SCORE_SELECT), "note": "WATCH " + str(SCORE_WATCH)},
            {"id": "RAILS", "item": "Stop / target / hold", "value": f"−{STOP_PCT}% / +{TARGET_PCT}% / {MAX_HOLD_SESSIONS}d", "note": "Not AM07 −5 / +20 / 200d"},
            {"id": "M1", "item": "Rank", "value": f"6m+12m vol-adj · mom {MOM_WEIGHT} / qual {QUAL_WEIGHT}", "note": "Today's Advice"},
            {"id": "FII", "item": "FII", "value": "Size throttle only", "note": "Never a SELECT veto"},
            {"id": "COST", "item": "Costs", "value": f"{BUY_COST_PCT}% buy + {SELL_COST_PCT}% sell", "note": "Booked on every close"},
            {"id": "RESERVE", "item": "Cash reserve", "value": f"{CASH_RESERVE_PCT}%", "note": "Blocks new buys"},
        ],
        "horizons": HORIZONS,
    }


def settings(upstox: dict, robot: dict, engine, build: str) -> dict:
    return {
        "ok": True,
        "build": build,
        "parameter_set_id": PARAMETER_SET_ID,
        "paper_only": True,
        "live_locked": True,
        "upstox": upstox,
        "robot_armed": bool((robot or {}).get("armed")),
        "clock": schedule_payload(getattr(engine, "clock_last", None) or {}),
        "jobs": JOBS,
        "locks": strategy()["rows"],
        "note": "Formula is locked in ash08/config.py. Settings here are status, not knobs.",
    }


def engine_status(engine, robot: dict, upstox: dict) -> dict:
    return {
        "ok": True,
        "governor": engine.governor.to_dict() if hasattr(engine.governor, "to_dict") else {},
        "open_n": len(engine.open_symbols()) if hasattr(engine, "open_symbols") else 0,
        "pending_n": len([o for o in (engine.pending_orders or []) if o.get("status") == "OPEN"]),
        "shadow_n": len(getattr(engine, "shadow", None) or {}),
        "journal_n": len(engine.journal or []),
        "cash": getattr(engine, "cash", None),
        "clock_last": getattr(engine, "clock_last", None) or {},
        "robot": robot,
        "upstox": upstox,
        "schedule": schedule_payload(getattr(engine, "clock_last", None) or {}),
    }


def shadow_payload(engine) -> dict:
    rows = list((getattr(engine, "shadow", None) or {}).values())
    open_n = sum(1 for r in rows if r.get("status") == "open")
    hit_t = sum(1 for r in rows if r.get("status") == "target")
    hit_s = sum(1 for r in rows if r.get("status") == "stop")
    return {
        "ok": True,
        "n": len(rows),
        "open": open_n,
        "target_hits": hit_t,
        "stop_hits": hit_s,
        "rows": sorted(rows, key=lambda r: str(r.get("ticker") or "")),
        "note": "YES names the robot did not buy — marked to −3 / +6 so you see opportunity cost. Paper only.",
    }


def triggers(advise: dict) -> dict:
    """Per-name trigger layer: Chitty + T/P gates from Today's Advice."""
    rows = []
    for r in (advise.get("buy") or []) + (advise.get("watch") or []) + (advise.get("avoid") or [])[:30]:
        steps = {s.get("id"): s for s in (r.get("steps") or [])}
        rows.append({
            "symbol": r.get("symbol"),
            "action": r.get("action"),
            "score": r.get("score"),
            "cn018": (steps.get("CN-018") or {}).get("status"),
            "cn022": (steps.get("CN-022") or {}).get("status"),
            "cn021": (steps.get("CN-021") or {}).get("status"),
            "t1": (steps.get("T1") or {}).get("status"),
            "t2": (steps.get("T2") or {}).get("status"),
            "t9": (steps.get("T9") or {}).get("status"),
            "why": r.get("why"),
        })
    return {"ok": True, "asof": advise.get("asof"), "n": len(rows), "rows": rows[:80]}
