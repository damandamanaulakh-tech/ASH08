"""Paper robot. Buys Today's Advice. Sells on −3 / +6 / 15d.

Paper only. Live Upstox LTP or skip. Never invents a fill.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from ash08.advisory import payload as advise_payload

LOG = logging.getLogger("ash08.robot")
IST = ZoneInfo("Asia/Kolkata")

QuoteFn = Callable[[List[str]], Dict[str, float]]

_LOCK = threading.Lock()
_LAST: Dict[str, Any] = {
    "ok": True,
    "armed": True,
    "mode": "paper",
    "ticked_at": None,
    "session": None,
    "bought": 0,
    "sold": 0,
    "skipped": 0,
    "open_count": 0,
    "ltp_n": 0,
    "ltp_source": "no_live_ltp",
    "buy_symbols": [],
    "sold_detail": [],
    "skipped_detail": [],
    "note": "Paper robot. BUY names auto-fill on live LTP. Exits −3% / +6% / 15d. No fake mark.",
}


def session_now(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    mins = now.hour * 60 + now.minute
    weekday = now.weekday() < 5
    in_reg = weekday and (9 * 60 + 15) <= mins <= (15 * 60 + 30)
    in_mark = weekday and (9 * 60 + 15) <= mins <= (15 * 60 + 40)
    return {
        "now_ist": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": weekday,
        "buy_window": in_reg,
        "sell_window": in_mark,
        "open": in_reg,
        "why": "nse_session" if in_reg else ("mark_window" if in_mark else ("weekend" if not weekday else "closed")),
    }


def _rows_for_engine(buys: List[dict], live: Dict[str, float]) -> List[dict]:
    out = []
    for r in buys:
        row = dict(r)
        sym = str(row.get("symbol") or "").upper()
        row["symbol"] = sym
        row["vol_sigma"] = row.get("vol_sigma") if row.get("vol_sigma") is not None else row.get("sigma")
        if live.get(sym) is not None:
            row["ltp"] = live[sym]
        out.append(row)
    return out


def tick(
    engine,
    quote_fn: Optional[QuoteFn] = None,
    now: Optional[datetime] = None,
    force_buy: bool = False,
) -> Dict[str, Any]:
    """One cycle: mark opens (sell rails), then buy today's BUY names.

    quote_fn(symbols) -> {SYM: ltp}. Missing name = no fill / no exit this tick.
    """
    sess = session_now(now)
    advise = advise_payload()
    buys = list(advise.get("buy") or [])
    buy_syms = [str(r.get("symbol") or "").upper() for r in buys if r.get("symbol")]
    opens = [str(p.get("symbol") or "").upper() for p in engine.positions if p.get("status") == "OPEN"]
    want = list(dict.fromkeys(buy_syms + opens))
    live: Dict[str, float] = {}
    if quote_fn and want:
        try:
            raw = quote_fn(want) or {}
            for k, v in raw.items():
                try:
                    px = float(v)
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    live[str(k).upper()] = px
        except Exception as e:
            LOG.warning("robot quotes: %s", e)

    closed_before = {
        p.get("position_id")
        for p in engine.positions
        if p.get("status") != "OPEN"
    }
    if live:
        engine.mark_to_market(live, use_paper_marks=False)
    else:
        engine.refresh_hold_days(live_symbols=set())

    sold = [
        {
            "symbol": p.get("symbol"),
            "reason": p.get("exit_reason"),
            "exit_price": p.get("exit_price"),
            "pnl": p.get("realized_pnl"),
            "entry_value": p.get("entry_value"),
            "exit_value": p.get("exit_value"),
        }
        for p in engine.positions
        if p.get("status") != "OPEN" and p.get("position_id") not in closed_before
    ]

    buy_result: Dict[str, Any] = {
        "bought": 0,
        "skipped": 0,
        "orders": [],
        "skipped_detail": [],
        "open_count": len(engine.open_symbols()),
    }
    allow_buy = sess["buy_window"] or force_buy
    if allow_buy and buys:
        rows = _rows_for_engine(buys, live)
        buy_result = engine.auto_buy_selects(rows, price_map=live)
    elif not allow_buy:
        buy_result["skipped_detail"] = [{"symbol": "*", "reason": "outside_buy_window"}]

    ltp_source = "upstox" if live else "no_live_ltp"
    body = {
        "ok": True,
        "armed": True,
        "mode": "paper",
        "ticked_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "session": sess,
        "asof_advice": advise.get("asof"),
        "buy_n": len(buy_syms),
        "buy_symbols": buy_syms,
        "bought": buy_result.get("bought") or 0,
        "sold": len(sold),
        "sold_detail": sold,
        "skipped": buy_result.get("skipped") or 0,
        "skipped_detail": (buy_result.get("skipped_detail") or [])[:20],
        "skipped_no_upstox_ltp": [s for s, px in ((sym, live.get(sym)) for sym in buy_syms) if px is None],
        "open_count": buy_result.get("open_count") if buy_result.get("open_count") is not None else len(engine.open_symbols()),
        "cash": getattr(engine, "cash", None),
        "ltp_n": len(live),
        "ltp_source": ltp_source,
        "force_buy": force_buy,
        "note": "Paper. Auto-buy Today's BUY. Auto-sell −3% / +6% / 15d. Live LTP only.",
    }
    with _LOCK:
        _LAST.update(body)
    return body


def status() -> Dict[str, Any]:
    with _LOCK:
        out = dict(_LAST)
    out["session"] = session_now()
    return out
