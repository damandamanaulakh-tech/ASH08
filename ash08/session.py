"""IST session clock. One place for quote mode and robot windows.

During the NSE cash session (Mon–Fri 09:15–15:30 IST) every live last is
Upstox. Yahoo is used only after hours / weekend. Tape close is never a fill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))

SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30
MARK_CLOSE_MIN = 15 * 60 + 40


def now_ist(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def session_state(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now_ist(now)
    mins = now.hour * 60 + now.minute
    weekday = now.weekday() < 5
    in_reg = weekday and SESSION_OPEN_MIN <= mins <= SESSION_CLOSE_MIN
    in_mark = weekday and SESSION_OPEN_MIN <= mins <= MARK_CLOSE_MIN
    if in_reg:
        why = "nse_session"
        quote_mode = "upstox"
    elif in_mark:
        why = "mark_window"
        quote_mode = "yahoo"
    elif not weekday:
        why = "weekend"
        quote_mode = "yahoo"
    else:
        why = "closed"
        quote_mode = "yahoo"
    return {
        "now_ist": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": weekday,
        "buy_window": in_reg,
        "sell_window": in_mark,
        "open": in_reg,
        "regular_open": in_reg,
        "quote_mode": quote_mode,
        "why": why,
    }
