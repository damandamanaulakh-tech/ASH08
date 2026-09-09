"""ASH08 Paper Engine - P&L, LTP mark-to-market, auto-SELECT. Numbers from ash08.config."""
from __future__ import annotations
import argparse, json, logging, uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ash08.config import (
    BOOK_VALUE as DEFAULT_BOOK,
    BUY_COST_PCT,
    CASH_RESERVE_PCT,
    CONSEC_LOSS_MAX,
    GOVERNOR_EXPOSURE as EXPOSURE,
    KILL_DAILY_PCT,
    MAX_HOLD_SESSIONS as MAX_HOLD_DAYS,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    SECTOR_MAX,
    SELL_COST_PCT,
    STOP_PCT,
    TARGET_PCT,
    TICKER_BLOCKLIST,
)
from ash08.sizing import kelly_notional

LOG = logging.getLogger("ash08.paper")
DEFAULT_QTY = 50
REF_LTP = {
    "TCS": 3840.0, "HDFCBANK": 1690.0, "RELIANCE": 2950.0, "INFY": 1850.0,
    "ICICIBANK": 1180.0, "SBIN": 820.0, "ITC": 450.0, "MTARTECH": 1850.0,
    "COCHINSHIP": 1450.0, "HAL": 4200.0, "BEL": 280.0, "LT": 3600.0,
    "HCLTECH": 1650.0, "WIPRO": 480.0, "AXISBANK": 1100.0, "KOTAKBANK": 1750.0,
    "TATAMOTORS": 980.0, "MARUTI": 12400.0, "BAJFINANCE": 7100.0, "POWERGRID": 300.0,
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(p):
    return f"{p}_{uuid.uuid4().hex[:10]}"


def _parse_ts(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _pnl_fields(entry, ltp, qty, exit_price=None, status="OPEN"):
    try:
        entry = float(entry or 0)
        qty = float(qty or 0)
        mark = float(exit_price if status != "OPEN" and exit_price is not None else (ltp or entry))
    except Exception:
        return {
            "ltp": ltp,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "pnl_label": "-",
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
        }
    pnl = round((mark - entry) * qty, 2)
    pct = round(((mark / entry) - 1.0) * 100.0, 2) if entry else 0.0
    label = f"{pnl:+.2f} ({pct:+.2f}%)"
    out = {"ltp": round(mark, 2), "pnl": pnl, "pnl_pct": pct, "pnl_label": label}
    if status == "OPEN":
        out["unrealized_pnl"] = pnl
        out["unrealized_pct"] = pct
    else:
        out["realized_pnl"] = pnl
        out["return_pct"] = pct
    return out


def paper_mark_price(symbol: str, entry: float) -> float:
    """REMOVED from the live book. Kept only so old imports do not explode.
    Book P&L uses live LTP or stays 0. Never invent a drift.
    """
    try:
        return round(float(entry or 0), 2)
    except Exception:
        return 0.0


@dataclass
class GovState:
    level: str
    exposure_pct: float
    rationale: str

    def to_dict(self):
        return asdict(self)


def evaluate_governor(
    damage=False,
    q10=False,
    sell=False,
    any_fii=False,
    day_pnl_pct=None,
    drawdown_pct=None,
    consec_losses=None,
) -> GovState:
    if day_pnl_pct is not None and day_pnl_pct <= -KILL_DAILY_PCT:
        return GovState("L4_EXTREME", EXPOSURE["L4"], "kill_daily")
    if drawdown_pct is not None and drawdown_pct <= -20.0:
        return GovState("L4_EXTREME", EXPOSURE["L4"], "dd_-20")
    confirms = sum([q10, sell, any_fii])
    if damage and q10 and sell:
        return GovState("L4_EXTREME", EXPOSURE["L4"], "Q10+sell")
    if drawdown_pct is not None and drawdown_pct <= -15.0:
        return GovState("L3_HIGH_SEVERITY", EXPOSURE["L3"], "dd_-15")
    if damage and confirms >= 2:
        return GovState("L3_HIGH_SEVERITY", EXPOSURE["L3"], ">=2 FII")
    if consec_losses is not None and consec_losses >= CONSEC_LOSS_MAX:
        return GovState("L2_CONFIRMED", EXPOSURE["L2"], "consec_loss")
    if drawdown_pct is not None and drawdown_pct <= -8.0:
        return GovState("L2_CONFIRMED", EXPOSURE["L2"], "dd_-8")
    if damage and confirms == 1:
        return GovState("L2_CONFIRMED", EXPOSURE["L2"], "1 FII")
    if drawdown_pct is not None and drawdown_pct <= -5.0:
        return GovState("L1_DAMAGE_ONLY", EXPOSURE["L1"], "dd_-5")
    if damage:
        return GovState("L1_DAMAGE_ONLY", EXPOSURE["L1"], "damage")
    return GovState("L0_NORMAL", EXPOSURE["L0"], "normal")


class PaperEngine:
    def __init__(self, data_dir="ash08_data", book_value=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.book_value = float(book_value if book_value is not None else DEFAULT_BOOK)
        self.governor = GovState("L0_NORMAL", EXPOSURE["L0"], "init")
        self.orders, self.positions = [], []
        self.pending_orders = []
        self.journal = []
        self.shadow: Dict[str, dict] = {}
        self.equity_history: List[tuple] = []
        self.peak_equity = float(self.book_value)
        self.clock_last: Dict[str, str] = {}
        self.cash = self.book_value
        self._load()

    def _load(self):
        from ash08.book_store import apply_state, load as load_book

        st, src = load_book(self.data_dir)
        if not st:
            return
        try:
            apply_state(self, st)
            LOG.info("paper book loaded source=%s opens=%s", src, len(self.open_symbols()))
        except Exception as e:
            LOG.warning("paper_state load failed: %s", e)

    def size_qty(self, qty, price, score=None, sigma=None):
        if price is None or price <= 0:
            return 0
        notional, _diag = kelly_notional(
            self.book_value, score, sigma, self.governor.exposure_pct,
        )
        name_cap = self.book_value * (MAX_NAME_PCT / 100.0) * (self.governor.exposure_pct / 100.0)
        notional = min(notional, name_cap)
        return max(0, int(notional // price))

    def _deployed_notional(self) -> float:
        total = 0.0
        for p in self.positions:
            if p.get("status") != "OPEN":
                continue
            try:
                total += float(p.get("entry") or 0) * float(p.get("qty") or 0)
            except Exception:
                pass
        return total

    def _reserve_floor(self) -> float:
        return self.book_value * (CASH_RESERVE_PCT / 100.0)

    def _buy_debit(self, notional: float) -> float:
        return round(float(notional) * (1.0 + BUY_COST_PCT / 100.0), 2)

    def _sell_credit(self, notional: float) -> float:
        return round(float(notional) * (1.0 - SELL_COST_PCT / 100.0), 2)

    def _settle_close(self, p: Dict[str, Any]) -> None:
        if p.get("cash_restored"):
            return
        qty = float(p.get("qty") or 0)
        entry = float(p.get("entry") or 0)
        exit_px = float(p.get("exit_price") or p.get("ltp") or entry)
        entry_value = round(entry * qty, 2)
        exit_value = round(exit_px * qty, 2)
        buy_cost = round(entry_value * BUY_COST_PCT / 100.0, 2)
        sell_cost = round(exit_value * SELL_COST_PCT / 100.0, 2)
        proceeds = self._sell_credit(exit_value)
        self.cash = round(self.cash + proceeds, 2)
        p["entry_value"] = entry_value
        p["exit_value"] = exit_value
        p["buy_cost"] = buy_cost
        p["sell_cost"] = sell_cost
        p["costs"] = round(buy_cost + sell_cost, 2)
        p["realized_pnl"] = round(proceeds - entry_value - buy_cost, 2)
        p["return_pct"] = round((p["realized_pnl"] / entry_value) * 100.0, 2) if entry_value else 0.0
        p["hold_days_closed"] = self._hold_days(p)
        p["cash_restored"] = True

    def _hold_days(self, p: Dict[str, Any]) -> int:
        a = _parse_ts(p.get("opened_at") or "")
        b = _parse_ts(p.get("closed_at") or "")
        if a and b:
            return max(0, (b.date() - a.date()).days)
        try:
            return int(p.get("days_held") or 0)
        except Exception:
            return 0

    def _open_pos(self, symbol: str):
        for p in self.positions:
            if p.get("status") == "OPEN" and str(p.get("symbol") or "").upper() == str(symbol).upper():
                return p
        return None

    def close_position(self, symbol, price, reason="OWNER_SELL", source="manual"):
        """Sell an open name at a real price. Never invents a fill."""
        try:
            px = float(price)
        except Exception:
            px = 0.0
        if not px or px <= 0:
            return {"status": "REJECTED", "reason": "no_live_ltp", "symbol": symbol}
        pos = self._open_pos(symbol)
        if not pos:
            return {"status": "REJECTED", "reason": "not_open", "symbol": symbol}
        qty = int(pos.get("qty") or 0)
        pos["status"] = "CLOSED"
        pos["exit_reason"] = reason
        pos["exit_price"] = round(px, 2)
        pos["closed_at"] = _now()
        pos["ltp"] = round(px, 2)
        pos.update(_pnl_fields(pos.get("entry"), px, qty, px, "CLOSED"))
        self._settle_close(pos)
        order = {
            "order_id": _id("ord"),
            "symbol": str(symbol).upper(),
            "side": "SELL",
            "qty": qty,
            "sized_qty": qty,
            "fill_price": round(px, 2),
            "stop": pos.get("stop"),
            "target": pos.get("target"),
            "hold_days": pos.get("hold_days"),
            "source": source,
            "score": pos.get("score"),
            "status": "FILLED",
            "governor": self.governor.to_dict(),
            "created_at": _now(),
            "exit_reason": reason,
        }
        self.orders.append(order)
        self.log_event(
            "SELL",
            symbol=str(symbol).upper(),
            qty=qty,
            price=round(px, 2),
            reason=reason,
            pnl=pos.get("realized_pnl"),
            source=source,
        )
        self._save()
        return order

    def close_all(self, price_map=None):
        """Owner close-all at live marks only. Missing quote stays open."""
        price_map = price_map or {}
        closed, skipped = [], []
        for p in list(self.positions):
            if p.get("status") != "OPEN":
                continue
            sym = str(p.get("symbol") or "").upper()
            px = price_map.get(sym)
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if px is None or px <= 0:
                skipped.append({"symbol": sym, "reason": "no_live_ltp"})
                continue
            order = self.close_position(sym, px, reason="OWNER_CLOSE_ALL", source="manual")
            if order.get("status") == "FILLED":
                closed.append(order)
            else:
                skipped.append({"symbol": sym, "reason": order.get("reason")})
        return {"closed": len(closed), "skipped": skipped, "orders": closed}

    def _rest_limit(self, symbol, limit_price, qty, stop=None, target=None, source="manual", score=None, sigma=None):
        try:
            limit_price = float(limit_price)
        except Exception:
            return {"status": "REJECTED", "reason": "bad_limit", "symbol": symbol}
        if limit_price <= 0:
            return {"status": "REJECTED", "reason": "bad_limit", "symbol": symbol}
        if self._open_pos(symbol):
            return {"status": "REJECTED", "reason": "already_open", "symbol": symbol}
        order = {
            "order_id": _id("ord"),
            "symbol": str(symbol).upper(),
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": qty,
            "sized_qty": None,
            "fill_price": None,
            "limit_price": round(limit_price, 2),
            "stop": stop,
            "target": target,
            "hold_days": MAX_HOLD_DAYS,
            "source": source,
            "score": score,
            "sigma": sigma,
            "status": "OPEN",
            "governor": self.governor.to_dict(),
            "created_at": _now(),
        }
        self.pending_orders.append(order)
        self.orders.append(order)
        self.log_event("LIMIT_PLACED", symbol=str(symbol).upper(), price=round(limit_price, 2), qty=qty)
        self._save()
        return order

    def process_pending(self, price_map=None):
        price_map = price_map or {}
        filled = []
        for o in list(self.pending_orders):
            if o.get("status") != "OPEN":
                continue
            sym = str(o.get("symbol") or "").upper()
            px = price_map.get(sym)
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if px is None or px <= 0:
                continue
            if px > float(o.get("limit_price") or 0):
                continue
            buy = self.place_order(
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                qty=o.get("qty") or DEFAULT_QTY,
                fill_price=px,
                stop=o.get("stop"),
                target=o.get("target"),
                source="limit_fill",
                score=o.get("score"),
                sigma=o.get("sigma"),
                why=o.get("why"),
            )
            if buy.get("status") == "FILLED" and self.orders and self.orders[-1].get("order_id") == buy.get("order_id"):
                self.orders.pop()
            o["status"] = "FILLED" if buy.get("status") == "FILLED" else o.get("status")
            if buy.get("status") != "FILLED":
                continue
            o["filled_ts"] = _now()
            o["fill_price"] = px
            o["sized_qty"] = buy.get("sized_qty")
            filled.append(o)
            self.log_event("LIMIT_FILLED", symbol=sym, price=px, qty=buy.get("sized_qty"))
        self.pending_orders = [o for o in self.pending_orders if o.get("status") == "OPEN"]
        if filled:
            self._save()
        return filled

    def place_order(
        self,
        symbol,
        side,
        order_type,
        qty,
        fill_price,
        stop=None,
        target=None,
        hold_days=None,
        source="manual",
        score=None,
        sigma=None,
        why=None,
        mode="momentum",
        segment=None,
    ):
        side = str(side or "BUY").upper()
        ot = str(order_type or "MARKET").upper()
        symbol = str(symbol or "").strip().upper()
        if side == "SELL":
            return self.close_position(symbol, fill_price, reason="OWNER_SELL", source=source)
        if ot == "LIMIT":
            return self._rest_limit(
                symbol, fill_price, qty, stop=stop, target=target,
                source=source, score=score, sigma=sigma,
            )
        if self._open_pos(symbol):
            return {
                "order_id": _id("ord"),
                "symbol": symbol,
                "side": "BUY",
                "status": "REJECTED",
                "reason": "already_open",
                "fill_price": fill_price,
            }
        sized = self.size_qty(qty, fill_price, score=score, sigma=sigma)
        if stop is None and fill_price:
            stop = round(fill_price * (1 - STOP_PCT / 100), 2)
        if target is None and fill_price:
            target = round(fill_price * (1 + TARGET_PCT / 100), 2)
        hold = int(hold_days if hold_days is not None else MAX_HOLD_DAYS)
        opened = _now()
        debit = 0.0
        if sized and fill_price:
            debit = self._buy_debit(sized * float(fill_price))
            if self.cash - debit < self._reserve_floor():
                sized = 0
                debit = 0.0
        order = {
            "order_id": _id("ord"),
            "symbol": symbol,
            "side": side,
            "order_type": "MARKET",
            "qty": qty,
            "sized_qty": sized,
            "fill_price": fill_price,
            "stop": stop,
            "target": target,
            "hold_days": hold,
            "source": source,
            "score": score,
            "status": "FILLED" if sized else "REJECTED",
            "governor": self.governor.to_dict(),
            "created_at": opened,
            "exit_plan": {
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "max_hold_days": hold,
                "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION", "OWNER_SELL"],
            },
        }
        self.orders.append(order)
        if sized and side == "BUY":
            self.cash = round(self.cash - debit, 2)
            pnl = _pnl_fields(fill_price, fill_price, sized, status="OPEN")
            entry_value = round(float(fill_price) * sized, 2)
            self.positions.append(
                {
                    "position_id": _id("pos"),
                    "symbol": symbol,
                    "qty": sized,
                    "entry": fill_price,
                    "entry_value": entry_value,
                    "buy_cost": round(entry_value * BUY_COST_PCT / 100.0, 2),
                    "stop": stop,
                    "target": target,
                    "hold_days": hold,
                    "days_held": 0,
                    "days_left": hold,
                    "status": "OPEN",
                    "opened_at": opened,
                    "ltp": fill_price,
                    "ltp_live": True,
                    "source": source,
                    "score": score,
                    "why": why,
                    "mode": mode or "momentum",
                    "segment": segment or "",
                    "notes": "",
                    "tags": [],
                    "stop_pct": STOP_PCT,
                    "target_pct": TARGET_PCT,
                    "exit_plan": order["exit_plan"],
                    "exit_reason": None,
                    "exit_price": None,
                    "pnl": pnl["pnl"],
                    "pnl_pct": pnl["pnl_pct"],
                    "pnl_label": pnl["pnl_label"],
                    "unrealized_pnl": pnl.get("unrealized_pnl", 0.0),
                    "unrealized_pct": pnl.get("unrealized_pct", 0.0),
                    "cash_restored": False,
                }
            )
            self.log_event(
                "BUY",
                symbol=symbol,
                qty=sized,
                price=fill_price,
                stop=stop,
                target=target,
                source=source,
            )
        self._save()
        return order

    def open_symbols(self):
        return {p["symbol"] for p in self.positions if p.get("status") == "OPEN"}

    def refresh_hold_days(self, live_symbols=None):
        now = datetime.now(timezone.utc)
        live_symbols = {str(s).upper() for s in (live_symbols or [])}
        changed = False
        for p in self.positions:
            if p.get("status") != "OPEN":
                continue
            opened = _parse_ts(p.get("opened_at") or "")
            if not opened:
                continue
            held = max(0, (now - opened).days)
            hold = int(p.get("hold_days") or MAX_HOLD_DAYS)
            p["days_held"] = held
            p["days_left"] = max(0, hold - held)
            if held >= hold:
                sym = str(p.get("symbol") or "").upper()
                if live_symbols and sym not in live_symbols:
                    continue
                if not live_symbols:
                    continue
                px = p.get("ltp") or p.get("entry")
                self.close_position(sym, px, reason="MAX_HOLD", source="robot")
                changed = True
        if changed:
            self._save()

    def mark_to_market(self, price_map=None, use_paper_marks=False):
        price_map = price_map or {}
        self.process_pending(price_map)
        live_syms = set()
        for p in self.positions:
            sym = str(p.get("symbol") or "").upper()
            if p.get("status") != "OPEN":
                if not p.get("cash_restored"):
                    self._settle_close(p)
                p.update(
                    _pnl_fields(
                        p.get("entry"), p.get("ltp"), p.get("qty"), p.get("exit_price"), "CLOSED"
                    )
                )
                continue
            quoted = price_map.get(sym)
            have_live = quoted is not None
            if quoted is None and use_paper_marks:
                quoted = p.get("ltp") or p.get("entry")
            if quoted is None:
                quoted = p.get("ltp") or p.get("entry")
            try:
                ltp = float(quoted)
            except Exception:
                ltp = float(p.get("entry") or 0)
            p["ltp"] = round(ltp, 2)
            p["ltp_live"] = bool(have_live)
            p["mark_value"] = round(ltp * float(p.get("qty") or 0), 2)
            p["entry_value"] = round(float(p.get("entry") or 0) * float(p.get("qty") or 0), 2)
            try:
                tgt = float(p.get("target") or 0)
                stp = float(p.get("stop") or 0)
                p["to_target_pct"] = round((tgt / ltp - 1.0) * 100.0, 2) if ltp and tgt else None
                p["to_stop_pct"] = round((stp / ltp - 1.0) * 100.0, 2) if ltp and stp else None
            except Exception:
                p["to_target_pct"] = None
                p["to_stop_pct"] = None
            if have_live:
                live_syms.add(sym)
                if p.get("stop") is not None and ltp <= float(p["stop"]):
                    self.close_position(sym, ltp, reason="STOP_HIT", source="robot")
                elif p.get("target") is not None and ltp >= float(p["target"]):
                    self.close_position(sym, ltp, reason="TARGET_HIT", source="robot")
            st = p.get("status") or "OPEN"
            fields = _pnl_fields(p.get("entry"), p.get("ltp"), p.get("qty"), p.get("exit_price"), st)
            p.update(fields)
        self.refresh_hold_days(live_symbols=live_syms)
        self.mark_shadow(price_map)
        try:
            eq = round(self.cash + sum(
                float(p.get("mark_value") or 0) for p in self.positions if p.get("status") == "OPEN"
            ), 2)
            self.equity_history = (self.equity_history or [])[-500:] + [(_now(), eq)]
            if eq > float(self.peak_equity or 0):
                self.peak_equity = eq
        except Exception:
            pass
        self._save()

    def update_ltp(self, symbol, ltp):
        self.mark_to_market({str(symbol).upper(): float(ltp)}, use_paper_marks=False)

    def governor_cut(self):
        opens = [p for p in self.positions if p["status"] == "OPEN"]
        if not opens:
            return []
        victim = sorted(opens, key=lambda p: float(p.get("entry") or 0) * int(p.get("qty") or 0))[0]
        px = victim.get("ltp") or victim.get("entry")
        self.close_position(victim.get("symbol"), px, reason="GOVERNOR_CUT", source="robot")
        return [victim]

    def _sector_open_count(self, segment) -> int:
        seg = str(segment or "").strip()
        if not seg:
            return 0
        n = 0
        for p in self.positions:
            if p.get("status") != "OPEN":
                continue
            if str(p.get("segment") or "") == seg:
                n += 1
        return n

    def auto_buy_selects(self, select_rows, price_map=None):
        price_map = price_map or {}
        already = self.open_symbols()
        open_n = len(already)
        bought, skipped = [], []
        for row in select_rows:
            if open_n >= MAX_OPEN_POSITIONS:
                skipped.append({"symbol": row.get("symbol"), "reason": "max_open"})
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            if sym in TICKER_BLOCKLIST:
                skipped.append({"symbol": sym, "reason": "blocklist"})
                continue
            if self.governor.level == "L4_EXTREME":
                skipped.append({"symbol": sym, "reason": "kill_or_l4"})
                continue
            if self._sector_open_count(row.get("segment")) >= SECTOR_MAX:
                skipped.append({"symbol": sym, "reason": "sector_max"})
                continue
            if sym in already:
                skipped.append({"symbol": sym, "reason": "already_open"})
                continue
            price = price_map.get(sym) or row.get("ltp")
            try:
                price = float(price) if price is not None else None
            except Exception:
                price = None
            if price is None or price <= 0:
                skipped.append({"symbol": sym, "reason": "no_live_ltp"})
                continue
            score = row.get("score")
            sigma = row.get("vol_sigma")
            if sigma is None:
                sigma = row.get("sigma")
            if sigma is None:
                skipped.append({"symbol": sym, "reason": "no_vol"})
                continue
            trial_qty = self.size_qty(1, price, score=score, sigma=sigma)
            extra = trial_qty * price
            debit = self._buy_debit(extra)
            if self.cash - debit < self._reserve_floor():
                skipped.append({"symbol": sym, "reason": "cash_reserve"})
                continue
            order = self.place_order(
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                qty=trial_qty or DEFAULT_QTY,
                fill_price=price,
                source="auto_select",
                score=score,
                sigma=sigma,
                why=row.get("why") or row.get("reason"),
                mode=row.get("mode") or "momentum",
                segment=row.get("segment"),
            )
            if order.get("status") == "FILLED":
                already.add(sym)
                open_n += 1
                bought.append(order)
            else:
                skipped.append({"symbol": sym, "reason": "rejected_size"})
        self.mark_to_market(price_map, use_paper_marks=False)
        return {
            "bought": len(bought),
            "skipped": len(skipped),
            "orders": bought,
            "skipped_detail": skipped[:20],
            "open_count": open_n,
            "cash": self.cash,
            "plan": {
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "max_hold_days": MAX_HOLD_DAYS,
                "max_open": MAX_OPEN_POSITIONS,
                "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            },
        }

    def book_payload(self, live_prices=None):
        live_prices = live_prices or {}
        self.mark_to_market(live_prices, use_paper_marks=False)
        opens = [p for p in self.positions if p.get("status") == "OPEN"]
        closed = [p for p in self.positions if p.get("status") != "OPEN"]
        for p in closed:
            if not p.get("cash_restored"):
                self._settle_close(p)
        mark_open = round(sum(float(p.get("mark_value") or (p.get("ltp") or p.get("entry") or 0) * float(p.get("qty") or 0)) for p in opens), 2)
        unreal = round(sum(float(p.get("unrealized_pnl") if p.get("unrealized_pnl") is not None else p.get("pnl") or 0) for p in opens), 2)
        realized = round(
            sum(float(p.get("realized_pnl") if p.get("realized_pnl") is not None else p.get("pnl") or 0) for p in closed),
            2,
        )
        equity = round(self.cash + mark_open, 2)
        wins = [p for p in closed if float(p.get("realized_pnl") or 0) > 0]
        losses = [p for p in closed if float(p.get("realized_pnl") or 0) < 0]
        gross_profit = round(sum(float(p.get("realized_pnl") or 0) for p in wins), 2)
        gross_loss = round(sum(float(p.get("realized_pnl") or 0) for p in losses), 2)
        capital_used = round(sum(float(p.get("entry_value") or 0) for p in closed), 2)
        deployed = round(self._deployed_notional(), 2)
        return {
            "open": opens,
            "closed": list(reversed(closed)),
            "orders": list(reversed(self.orders[-80:])),
            "pending": [o for o in (self.pending_orders or []) if o.get("status") == "OPEN"],
            "open_count": len(opens),
            "closed_count": len(closed),
            "order_count": len(self.orders),
            "unrealized_pnl": unreal,
            "realized_pnl": realized,
            "total_pnl": round(unreal + realized, 2),
            "cash": round(self.cash, 2),
            "equity": equity,
            "book_value": self.book_value,
            "deployed": deployed,
            "deployed_pct": round(deployed / self.book_value * 100.0, 2) if self.book_value else 0.0,
            "mark_open": mark_open,
            "reserve_pct": CASH_RESERVE_PCT,
            "reserve_floor": self._reserve_floor(),
            "buy_cost_pct": BUY_COST_PCT,
            "sell_cost_pct": SELL_COST_PCT,
            "max_open": MAX_OPEN_POSITIONS,
            "journal": list(getattr(self, "journal", []) or [])[-80:],
            "closed_stats": {
                "total": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "net": realized,
                "capital_used": capital_used,
                "net_pct_of_capital": round(realized / self.book_value * 100.0, 2) if self.book_value else 0.0,
            },
            "shadow": list((self.shadow or {}).values()),
            "clock_last": dict(self.clock_last or {}),
        }

    def expire_day_limits(self) -> int:
        n = 0
        for o in list(self.pending_orders or []):
            if o.get("status") != "OPEN":
                continue
            o["status"] = "EXPIRED"
            o["note"] = "day validity over (15:25 IST)"
            self.log_event("LIMIT_EXPIRED", symbol=o.get("symbol"), order_id=o.get("order_id"))
            n += 1
        self.pending_orders = [o for o in self.pending_orders if o.get("status") == "OPEN"]
        if n:
            self._save()
        return n

    def square_off_mode(self, mode: str, price_map=None) -> dict:
        price_map = price_map or {}
        mode = str(mode or "").lower()
        closed, skipped = [], []
        for p in list(self.positions):
            if p.get("status") != "OPEN":
                continue
            if str(p.get("mode") or "").lower() != mode:
                continue
            sym = str(p.get("symbol") or "").upper()
            px = price_map.get(sym)
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if px is None or px <= 0:
                skipped.append({"symbol": sym, "reason": "no_live_ltp"})
                continue
            self.close_position(sym, px, reason="INTRADAY_SQUARE", source="clock")
            closed.append(sym)
        return {"closed": closed, "skipped": skipped}

    def set_notes(self, symbol: str, notes: str = "", tags=None) -> bool:
        pos = self._open_pos(symbol)
        if not pos:
            return False
        pos["notes"] = str(notes or "")
        if tags is not None:
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            pos["tags"] = list(tags)
        self._save()
        return True

    def ingest_shadow(self, buy_rows, skipped, price_map=None):
        """Track BUY names the robot did not open — opportunity cost, ASH08 rails."""
        price_map = price_map or {}
        skip_map = {}
        for s in skipped or []:
            sym = str(s.get("symbol") or "").upper()
            if sym and sym != "*":
                skip_map[sym] = s.get("reason") or "skipped"
        held = self.open_symbols()
        for row in buy_rows or []:
            sym = str(row.get("symbol") or "").upper()
            if not sym:
                continue
            if sym in held:
                self.shadow.pop(sym, None)
                continue
            if sym not in skip_map:
                continue
            px = price_map.get(sym) or row.get("ltp") or row.get("close")
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if not px or px <= 0:
                continue
            prev = self.shadow.get(sym) or {}
            self.shadow[sym] = {
                "ticker": sym,
                "score": row.get("score"),
                "skip_reason": skip_map[sym],
                "entry_px": prev.get("entry_px") or round(px, 2),
                "last_px": round(px, 2),
                "status": prev.get("status") or "open",
                "stop": round(px * (1 - STOP_PCT / 100.0), 2),
                "target": round(px * (1 + TARGET_PCT / 100.0), 2),
                "why": row.get("why") or row.get("reason"),
            }
        self._save()

    def mark_shadow(self, price_map=None):
        price_map = price_map or {}
        for s in (self.shadow or {}).values():
            if s.get("status") != "open":
                continue
            px = price_map.get(str(s.get("ticker") or "").upper())
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if not px:
                continue
            entry = float(s.get("entry_px") or px)
            s["last_px"] = round(px, 2)
            s["pnl_pct"] = round((px / entry - 1.0) * 100.0, 2) if entry else 0.0
            if px <= float(s.get("stop") or 0):
                s["status"] = "stop"
            elif px >= float(s.get("target") or 0):
                s["status"] = "target"

    def _save(self):
        from ash08.book_store import dump_state, save as save_book

        try:
            save_book(dump_state(self), self.data_dir)
        except Exception as e:
            LOG.warning("paper_state save failed: %s", e)

    def log_event(self, event: str, **kw):
        row = {"ts": _now(), "event": event, **kw}
        self.journal = (getattr(self, "journal", None) or []) + [row]
        self.journal = self.journal[-300:]


def run_demo(data_dir):
    eng = PaperEngine(data_dir)
    eng.place_order("TCS", "BUY", "MARKET", 50, 3840, source="demo")
    eng.place_order("HDFCBANK", "BUY", "MARKET", 80, 1690, source="demo")
    eng.place_order("CASTROLIND", "BUY", "MARKET", 50, 241, source="demo")
    eng.mark_to_market({"TCS": 3880, "HDFCBANK": 1635}, use_paper_marks=True)
    return eng.book_payload()


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--data-dir", default="ash08_data")
    args = p.parse_args()
    if args.demo:
        print(json.dumps(run_demo(args.data_dir), indent=2))
    else:
        p.error("use --demo")


if __name__ == "__main__":
    main()
