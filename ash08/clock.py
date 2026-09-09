"""IST operating clock. ASH08 numbers stay locked; the day is a job list.

AM07: 09:20 scan, 14:30 top-up, 15:25 square-off, 15:35 mark.
ASH08 runs those jobs AND the 45s catch-up robot.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Rails stay −3 / +6 / 15d. Intraday also dies at 15:25.
JOBS = [
    {"id": "morning", "hhmm": 9 * 60 + 20, "window": 8, "title": "09:20 IST — multi-horizon scan + auto-buy"},
    {"id": "topup", "hhmm": 14 * 60 + 30, "window": 8, "title": "14:30 IST — top-up if deployed < 60%"},
    {"id": "square", "hhmm": 15 * 60 + 25, "window": 8, "title": "15:25 IST — expire day-limits + square intraday"},
    {"id": "eod", "hhmm": 15 * 60 + 35, "window": 8, "title": "15:35 IST — mark book + fire rails"},
]

DEPLOY_TOPUP_PCT = 60.0


def now_ist(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _mins(now: datetime) -> int:
    return now.hour * 60 + now.minute


def due_jobs(now: Optional[datetime] = None, last_run: Optional[Dict[str, str]] = None) -> List[dict]:
    now = now_ist(now)
    last_run = last_run or {}
    if now.weekday() >= 5:
        return []
    today = now.strftime("%Y-%m-%d")
    m = _mins(now)
    out = []
    for job in JOBS:
        if last_run.get(job["id"]) == today:
            continue
        if job["hhmm"] <= m <= job["hhmm"] + job["window"]:
            out.append(dict(job))
    return out


def schedule_payload(last_run: Optional[Dict[str, str]] = None, now: Optional[datetime] = None) -> dict:
    now = now_ist(now)
    last_run = last_run or {}
    today = now.strftime("%Y-%m-%d")
    rows = []
    for job in JOBS:
        ran = last_run.get(job["id"])
        rows.append({
            **job,
            "last_run": ran,
            "ran_today": ran == today,
            "due": any(d["id"] == job["id"] for d in due_jobs(now, last_run)),
        })
    return {
        "ok": True,
        "now_ist": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": now.weekday() < 5,
        "jobs": rows,
        "note": "ASH08 rails −3/+6/15d. Intraday also squares at 15:25. 45s robot still catch-up buys.",
    }


def pulse(engine, quote_fn: Callable, now: Optional[datetime] = None, tick_fn=None) -> Dict[str, Any]:
    """Run any IST job that is due. Safe to call every 45s."""
    now = now_ist(now)
    last = dict(getattr(engine, "clock_last", None) or {})
    due = due_jobs(now, last)
    ran: List[str] = []
    detail: Dict[str, Any] = {}
    if tick_fn is None:
        from ash08.robot import tick as tick_fn  # local to avoid import cycle at module load

    for job in due:
        jid = job["id"]
        try:
            if jid == "morning":
                detail[jid] = tick_fn(engine, quote_fn=quote_fn, now=now, force_buy=True)
            elif jid == "topup":
                book = engine.book_payload() if hasattr(engine, "book_payload") else {}
                dep = float(book.get("deployed_pct") or 0)
                if dep < DEPLOY_TOPUP_PCT:
                    detail[jid] = tick_fn(engine, quote_fn=quote_fn, now=now, force_buy=True)
                    detail[jid]["topup"] = True
                    detail[jid]["deployed_pct"] = dep
                else:
                    detail[jid] = {"skipped": True, "reason": "deployed_ok", "deployed_pct": dep}
            elif jid == "square":
                n_exp = engine.expire_day_limits() if hasattr(engine, "expire_day_limits") else 0
                sq = engine.square_off_mode("intraday", _live(quote_fn, engine)) if hasattr(engine, "square_off_mode") else {}
                detail[jid] = {"expired": n_exp, **(sq if isinstance(sq, dict) else {"square": sq})}
            elif jid == "eod":
                live = _live(quote_fn, engine)
                if live and hasattr(engine, "mark_to_market"):
                    engine.mark_to_market(live, use_paper_marks=False)
                detail[jid] = {"marked": True, "ltp_n": len(live)}
            last[jid] = now.strftime("%Y-%m-%d")
            ran.append(jid)
            if hasattr(engine, "log_event"):
                engine.log_event("CLOCK", job=jid, title=job["title"])
        except Exception as e:
            detail[jid] = {"error": str(e)}
            if hasattr(engine, "log_event"):
                engine.log_event("CLOCK_FAILED", job=jid, error=str(e)[:160])
    engine.clock_last = last
    if ran and hasattr(engine, "_save"):
        engine._save()
    return {"ok": True, "ran": ran, "detail": detail, "now_ist": now.strftime("%Y-%m-%d %H:%M")}


def _live(quote_fn, engine) -> Dict[str, float]:
    want = []
    if hasattr(engine, "open_symbols"):
        want.extend(sorted(engine.open_symbols()))
    for o in getattr(engine, "pending_orders", []) or []:
        if o.get("status") == "OPEN" and o.get("symbol"):
            want.append(str(o["symbol"]).upper())
    if not want or not quote_fn:
        return {}
    try:
        raw = quote_fn(want) or {}
    except Exception:
        return {}
    prices = raw.get("prices") if isinstance(raw, dict) and "prices" in raw else raw
    live = {}
    if isinstance(prices, dict):
        for k, v in prices.items():
            try:
                px = float(v)
            except (TypeError, ValueError):
                continue
            if px > 0:
                live[str(k).upper()] = px
    return live
