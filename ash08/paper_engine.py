"""ASH08 paper portfolio engine with durable state and fail-closed pricing."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import (
    BOOK_VALUE,
    BUY_COST_PCT,
    MAX_GROSS_PCT,
    MAX_HOLD_SESSIONS,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    PARAMETER_SET_ID,
    SELL_COST_PCT,
    STOP_PCT,
    TARGET_PCT,
)

DEFAULT_BOOK = BOOK_VALUE
EXPOSURE = {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _trading_sessions_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    current = start.date()
    finish = end.date()
    sessions = 0
    while current < finish:
        current = date.fromordinal(current.toordinal() + 1)
        if current.weekday() < 5:
            sessions += 1
    return sessions


def _round_to_tick(value: float, tick_size: float) -> float:
    tick = tick_size if tick_size > 0 else 0.05
    return round(round(value / tick) * tick, 8)


@dataclass
class GovState:
    level: str = "L0"
    exposure_pct: float = 100.0
    rationale: str = "NO_VERIFIED_EVIDENCE"
    verified: bool = False
    evidence_asof: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_governor(
    damage: bool = False,
    q10: bool = False,
    sell: bool = False,
    any_fii: bool = False,
    evidence_complete: bool = False,
    evidence_fresh: bool = False,
    evidence_asof: str = "",
) -> GovState:
    if not (evidence_complete and evidence_fresh):
        return GovState("L0", EXPOSURE["L0"], "NO_VERIFIED_EVIDENCE", False, evidence_asof)
    confirmations = int(bool(q10)) + int(bool(sell)) + int(bool(any_fii))
    if damage and q10 and sell:
        return GovState("L4", EXPOSURE["L4"], "DAMAGE+Q10+SELL", True, evidence_asof)
    if damage and confirmations >= 2:
        return GovState("L3", EXPOSURE["L3"], "DAMAGE+AT_LEAST_2_CONFIRMATIONS", True, evidence_asof)
    if damage and confirmations == 1:
        return GovState("L2", EXPOSURE["L2"], "DAMAGE+1_CONFIRMATION", True, evidence_asof)
    if damage:
        return GovState("L1", EXPOSURE["L1"], "DAMAGE_ONLY", True, evidence_asof)
    return GovState("L0", EXPOSURE["L0"], "NO_DAMAGE", True, evidence_asof)


class PaperEngine:
    def __init__(self, data_dir: str | Path = "ash08_data", book_value: float = DEFAULT_BOOK):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "paper_state.json"
        self.book_value = float(book_value)
        self.lock = threading.RLock()
        self.governor = GovState()
        self.orders: List[Dict[str, Any]] = []
        self.positions: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.idempotency: Dict[str, str] = {}
        self.cash = self.book_value
        self.state_version = 0
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("paper state is not an object")
            self.orders = list(state.get("orders") or [])
            self.positions = list(state.get("positions") or [])
            self.events = list(state.get("events") or [])
            self.idempotency = dict(state.get("idempotency") or {})
            gov = state.get("governor") or {}
            self.governor = GovState(
                level=str(gov.get("level") or "L0").split("_")[0],
                exposure_pct=float(gov.get("exposure_pct") or 100.0),
                rationale=str(gov.get("rationale") or "MIGRATED"),
                verified=bool(gov.get("verified", False)),
                evidence_asof=str(gov.get("evidence_asof") or ""),
            )
            self.state_version = int(state.get("state_version") or 0)
            cash = _finite(state.get("cash"))
            if cash is None:
                open_cost = sum(
                    float(position.get("entry") or 0) * float(position.get("qty") or 0)
                    for position in self.positions
                    if position.get("status") == "OPEN"
                )
                realized = sum(
                    float(position.get("net_pnl") or position.get("pnl") or 0)
                    for position in self.positions
                    if position.get("status") == "CLOSED"
                )
                self.cash = max(0.0, self.book_value - open_cost + realized)
            else:
                self.cash = cash
        except Exception as exc:
            quarantine = self.state_path.with_suffix(f".corrupt-{int(datetime.now().timestamp())}.json")
            os.replace(self.state_path, quarantine)
            self.events.append({"event_id": _id("evt"), "type": "STATE_QUARANTINED", "detail": str(exc), "at": _now()})

    def _state(self) -> Dict[str, Any]:
        return {
            "schema_version": 2,
            "parameter_set_id": PARAMETER_SET_ID,
            "state_version": self.state_version,
            "book_value": self.book_value,
            "cash": round(self.cash, 2),
            "governor": self.governor.to_dict(),
            "orders": self.orders,
            "positions": self.positions,
            "events": self.events[-1000:],
            "idempotency": self.idempotency,
            "plan": {
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "max_hold_sessions": MAX_HOLD_SESSIONS,
                "max_open": MAX_OPEN_POSITIONS,
                "max_name_pct": MAX_NAME_PCT,
                "max_gross_pct": MAX_GROSS_PCT,
            },
        }

    def _save(self) -> None:
        self.state_version += 1
        payload = json.dumps(self._state(), indent=2, sort_keys=True, default=str)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.data_dir, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, self.state_path)

    def _event(self, event_type: str, **payload: Any) -> None:
        self.events.append({"event_id": _id("evt"), "type": event_type, "at": _now(), **payload})

    def open_positions(self) -> List[Dict[str, Any]]:
        return [position for position in self.positions if position.get("status") == "OPEN"]

    def open_symbols(self) -> set[str]:
        return {str(position.get("symbol") or "") for position in self.open_positions()}

    def open_count(self) -> int:
        return len(self.open_positions())

    def gross_exposure(self) -> float:
        return round(sum(float(position.get("ltp") or position.get("entry") or 0) * float(position.get("qty") or 0) for position in self.open_positions()), 2)

    def name_exposure(self, symbol: str) -> float:
        return round(sum(float(position.get("ltp") or position.get("entry") or 0) * float(position.get("qty") or 0) for position in self.open_positions() if position.get("symbol") == symbol), 2)

    def size_qty(self, symbol: str, requested_qty: int, price: float, lot_size: int = 1) -> Dict[str, Any]:
        px = _finite(price)
        if px is None or px <= 0 or requested_qty <= 0:
            return {"qty": 0, "allowed_notional": 0.0, "reason": "INVALID_PRICE_OR_QTY"}
        lot = max(1, int(lot_size or 1))
        exposure_factor = self.governor.exposure_pct / 100.0
        name_limit = self.book_value * (MAX_NAME_PCT / 100.0) * exposure_factor
        gross_limit = self.book_value * (MAX_GROSS_PCT / 100.0) * exposure_factor
        name_headroom = max(0.0, name_limit - self.name_exposure(symbol))
        gross_headroom = max(0.0, gross_limit - self.gross_exposure())
        cash_headroom = max(0.0, self.cash / (1.0 + BUY_COST_PCT / 100.0))
        allowed_notional = min(name_headroom, gross_headroom, cash_headroom)
        raw = min(int(requested_qty), int(allowed_notional // px))
        sized = (raw // lot) * lot
        return {
            "qty": max(0, sized),
            "allowed_notional": round(allowed_notional, 2),
            "name_headroom": round(name_headroom, 2),
            "gross_headroom": round(gross_headroom, 2),
            "cash_headroom": round(cash_headroom, 2),
            "reason": "OK" if sized > 0 else "NO_RISK_HEADROOM",
        }

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: int,
        fill_price: float,
        stop: Optional[float] = None,
        target: Optional[float] = None,
        hold_days: Optional[int] = None,
        source: str = "manual",
        score: Optional[float] = None,
        lot_size: int = 1,
        tick_size: float = 0.05,
        idempotency_key: str = "",
        scan_id: str = "",
        instrument_key: str = "",
    ) -> Dict[str, Any]:
        with self.lock:
            if idempotency_key and idempotency_key in self.idempotency:
                order_id = self.idempotency[idempotency_key]
                return next(order for order in self.orders if order.get("order_id") == order_id)
            sym = str(symbol or "").strip().upper()
            action = str(side or "BUY").strip().upper()
            px = _finite(fill_price)
            requested_qty = int(qty or 0)
            if not sym or px is None or px <= 0 or requested_qty <= 0:
                return self._reject(sym, action, requested_qty, px, "INVALID_ORDER", idempotency_key)
            if action != "BUY":
                return self._reject(sym, action, requested_qty, px, "USE_CLOSE_ENDPOINT_FOR_SELL", idempotency_key)
            if sym in self.open_symbols():
                return self._reject(sym, action, requested_qty, px, "ALREADY_OPEN", idempotency_key)
            if self.open_count() >= MAX_OPEN_POSITIONS:
                return self._reject(sym, action, requested_qty, px, "MAX_OPEN_REACHED", idempotency_key)
            sizing = self.size_qty(sym, requested_qty, px, lot_size=lot_size)
            sized_qty = int(sizing["qty"])
            if sized_qty <= 0:
                return self._reject(sym, action, requested_qty, px, sizing["reason"], idempotency_key, sizing=sizing)

            effective_stop = _finite(stop)
            effective_target = _finite(target)
            if effective_stop is None:
                effective_stop = px * (1.0 - STOP_PCT / 100.0)
            if effective_target is None:
                effective_target = px * (1.0 + TARGET_PCT / 100.0)
            effective_stop = _round_to_tick(effective_stop, tick_size)
            effective_target = _round_to_tick(effective_target, tick_size)
            if not (0 < effective_stop < px < effective_target):
                return self._reject(sym, action, requested_qty, px, "INVALID_STOP_TARGET", idempotency_key)

            hold_sessions = int(hold_days if hold_days is not None else MAX_HOLD_SESSIONS)
            notional = round(px * sized_qty, 2)
            buy_costs = round(notional * BUY_COST_PCT / 100.0, 2)
            debit = notional + buy_costs
            if debit > self.cash + 0.01:
                return self._reject(sym, action, requested_qty, px, "INSUFFICIENT_CASH", idempotency_key)

            created_at = _now()
            order_id = _id("ord")
            order = {
                "order_id": order_id,
                "symbol": sym,
                "instrument_key": instrument_key,
                "side": "BUY",
                "order_type": str(order_type or "MARKET").upper(),
                "requested_qty": requested_qty,
                "qty": sized_qty,
                "fill_price": round(px, 8),
                "notional": notional,
                "costs": buy_costs,
                "status": "FILLED",
                "reason": "FILLED",
                "source": source,
                "score": score,
                "scan_id": scan_id,
                "parameter_set_id": PARAMETER_SET_ID,
                "governor": self.governor.to_dict(),
                "created_at": created_at,
                "sizing": sizing,
            }
            position = {
                "position_id": _id("pos"),
                "symbol": sym,
                "instrument_key": instrument_key,
                "qty": sized_qty,
                "entry": round(px, 8),
                "ltp": round(px, 8),
                "mark_status": "ENTRY_ONLY",
                "mark_asof": "",
                "stop": effective_stop,
                "target": effective_target,
                "hold_sessions": hold_sessions,
                "sessions_held": 0,
                "sessions_left": hold_sessions,
                "status": "OPEN",
                "opened_at": created_at,
                "source": source,
                "score": score,
                "scan_id": scan_id,
                "parameter_set_id": PARAMETER_SET_ID,
                "buy_costs": buy_costs,
                "sell_costs": 0.0,
                "gross_pnl": 0.0,
                "net_pnl": -buy_costs,
                "pnl": -buy_costs,
                "pnl_pct": round((-buy_costs / debit) * 100.0, 4) if debit else 0.0,
                "exit_reason": None,
                "exit_price": None,
            }
            self.cash = round(self.cash - debit, 2)
            self.orders.append(order)
            self.positions.append(position)
            if idempotency_key:
                self.idempotency[idempotency_key] = order_id
            self._event("ORDER_FILLED", order_id=order_id, position_id=position["position_id"], symbol=sym, qty=sized_qty, price=px)
            self._save()
            return order

    def _reject(
        self,
        symbol: str,
        side: str,
        requested_qty: int,
        price: Optional[float],
        reason: str,
        idempotency_key: str = "",
        sizing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        order = {
            "order_id": _id("ord"),
            "symbol": symbol,
            "side": side,
            "requested_qty": requested_qty,
            "qty": 0,
            "fill_price": price,
            "status": "REJECTED",
            "reason": reason,
            "created_at": _now(),
            "sizing": sizing or {},
            "parameter_set_id": PARAMETER_SET_ID,
        }
        self.orders.append(order)
        if idempotency_key:
            self.idempotency[idempotency_key] = order["order_id"]
        self._event("ORDER_REJECTED", order_id=order["order_id"], reason=reason)
        self._save()
        return order

    def _update_pnl(self, position: Dict[str, Any]) -> None:
        entry = float(position.get("entry") or 0)
        mark = float(position.get("exit_price") if position.get("status") == "CLOSED" else position.get("ltp") or entry)
        qty = float(position.get("qty") or 0)
        gross = round((mark - entry) * qty, 2)
        estimated_sell_costs = round(mark * qty * SELL_COST_PCT / 100.0, 2)
        sell_costs = float(position.get("sell_costs") or 0) if position.get("status") == "CLOSED" else estimated_sell_costs
        net = round(gross - float(position.get("buy_costs") or 0) - sell_costs, 2)
        capital = entry * qty + float(position.get("buy_costs") or 0)
        position.update({
            "gross_pnl": gross,
            "net_pnl": net,
            "pnl": net,
            "pnl_pct": round((net / capital) * 100.0, 4) if capital else 0.0,
            "pnl_label": f"{net:+.2f}",
        })

    def process_marks(self, price_map: Dict[str, Any], mark_asof: str = "") -> Dict[str, Any]:
        with self.lock:
            changed = 0
            closed: List[Dict[str, Any]] = []
            now = datetime.now(timezone.utc)
            for position in self.open_positions():
                symbol = str(position.get("symbol") or "")
                raw = price_map.get(symbol)
                price = _finite(raw.get("price") if isinstance(raw, dict) else raw)
                if price is None or price <= 0:
                    position["mark_status"] = "UNMARKED"
                    continue
                position["ltp"] = round(price, 8)
                position["mark_status"] = "LIVE"
                position["mark_asof"] = mark_asof or _now()
                opened = _parse_ts(str(position.get("opened_at") or ""))
                held = _trading_sessions_between(opened, now) if opened else 0
                position["sessions_held"] = held
                position["sessions_left"] = max(0, int(position.get("hold_sessions") or MAX_HOLD_SESSIONS) - held)
                reason = None
                if price <= float(position.get("stop") or -math.inf):
                    reason = "STOP_HIT"
                elif price >= float(position.get("target") or math.inf):
                    reason = "TARGET_HIT"
                elif held >= int(position.get("hold_sessions") or MAX_HOLD_SESSIONS):
                    reason = "MAX_HOLD"
                if reason:
                    closed.append(self._close_position_locked(position, int(position["qty"]), price, reason))
                else:
                    self._update_pnl(position)
                changed += 1
            if changed:
                self._event("MARK_BATCH", marked=changed, closed=len(closed), mark_asof=mark_asof or _now())
                self._save()
            return {"marked": changed, "closed": closed}

    def close_position(self, symbol: str, price: float, qty: Optional[int] = None, reason: str = "MANUAL_CLOSE") -> Dict[str, Any]:
        with self.lock:
            position = next((item for item in self.open_positions() if item.get("symbol") == str(symbol).upper()), None)
            if not position:
                raise ValueError("POSITION_NOT_FOUND")
            close_qty = int(qty or position["qty"])
            return self._close_position_locked(position, close_qty, price, reason, persist=True)

    def _close_position_locked(
        self,
        position: Dict[str, Any],
        qty: int,
        price: float,
        reason: str,
        persist: bool = False,
    ) -> Dict[str, Any]:
        px = _finite(price)
        if px is None or px <= 0:
            raise ValueError("FRESH_EXECUTABLE_PRICE_REQUIRED")
        current_qty = int(position.get("qty") or 0)
        if qty <= 0 or qty > current_qty:
            raise ValueError("INVALID_CLOSE_QTY")
        proceeds = round(px * qty, 2)
        sell_costs = round(proceeds * SELL_COST_PCT / 100.0, 2)
        self.cash = round(self.cash + proceeds - sell_costs, 2)
        if qty == current_qty:
            position["status"] = "CLOSED"
            position["exit_reason"] = reason
            position["exit_price"] = round(px, 8)
            position["closed_at"] = _now()
            position["sell_costs"] = sell_costs
            self._update_pnl(position)
            result = dict(position)
        else:
            ratio = qty / current_qty
            realized_buy_costs = round(float(position.get("buy_costs") or 0) * ratio, 2)
            position["qty"] = current_qty - qty
            position["buy_costs"] = round(float(position.get("buy_costs") or 0) - realized_buy_costs, 2)
            self._update_pnl(position)
            realized = {
                "position_id": _id("posclose"),
                "parent_position_id": position["position_id"],
                "symbol": position["symbol"],
                "qty": qty,
                "entry": position["entry"],
                "exit_price": round(px, 8),
                "buy_costs": realized_buy_costs,
                "sell_costs": sell_costs,
                "status": "CLOSED",
                "exit_reason": reason,
                "opened_at": position.get("opened_at"),
                "closed_at": _now(),
            }
            self._update_pnl(realized)
            self.positions.append(realized)
            result = realized
        self._event("POSITION_CLOSED", symbol=position["symbol"], qty=qty, price=px, reason=reason)
        if persist:
            self._save()
        return result

    def auto_buy_selects(
        self,
        select_rows: Iterable[Dict[str, Any]],
        price_map: Dict[str, float],
        scan_id: str,
        default_qty: int = 50,
    ) -> Dict[str, Any]:
        bought: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for row in select_rows:
            if row.get("decision") != "SELECT":
                continue
            symbol = str(row.get("symbol") or "").upper()
            price = _finite(price_map.get(symbol))
            if price is None:
                skipped.append({"symbol": symbol, "reason": "NO_FRESH_PRICE"})
                continue
            key = f"auto:{scan_id}:{symbol}"
            order = self.place_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                qty=default_qty,
                fill_price=price,
                source="auto_select",
                score=row.get("score"),
                lot_size=int(row.get("lot_size") or 1),
                tick_size=float(row.get("tick_size") or 0.05),
                idempotency_key=key,
                scan_id=scan_id,
                instrument_key=str(row.get("instrument_key") or ""),
            )
            if order.get("status") == "FILLED":
                bought.append(order)
            else:
                skipped.append({"symbol": symbol, "reason": order.get("reason")})
        return {"bought": len(bought), "orders": bought, "skipped": len(skipped), "skipped_detail": skipped}

    def apply_governor(self, **evidence: Any) -> Dict[str, Any]:
        with self.lock:
            state = evaluate_governor(**evidence)
            self.governor = state
            gross = self.gross_exposure()
            target = round(self.book_value * state.exposure_pct / 100.0, 2)
            required_cut = round(max(0.0, gross - target), 2)
            plan = self.governor_cut_plan(required_cut)
            self._event("GOVERNOR_EVALUATED", governor=state.to_dict(), gross=gross, target=target, required_cut=required_cut)
            self._save()
            return {"governor": state.to_dict(), "gross_exposure": gross, "target_gross": target, "required_cut": required_cut, "cut_plan": plan}

    def governor_cut_plan(self, required_cut: float) -> List[Dict[str, Any]]:
        remaining = max(0.0, float(required_cut))
        plan: List[Dict[str, Any]] = []
        candidates = sorted(
            self.open_positions(),
            key=lambda item: float(item.get("ltp") or item.get("entry") or 0) * float(item.get("qty") or 0),
            reverse=True,
        )
        for position in candidates:
            if remaining <= 0:
                break
            price = float(position.get("ltp") or position.get("entry") or 0)
            qty = int(position.get("qty") or 0)
            notional = price * qty
            cut_notional = min(notional, remaining)
            cut_qty = min(qty, max(1, math.ceil(cut_notional / price))) if price > 0 else 0
            plan.append({"symbol": position.get("symbol"), "qty": cut_qty, "estimated_notional": round(cut_qty * price, 2)})
            remaining -= cut_qty * price
        return plan

    def book_payload(self) -> Dict[str, Any]:
        with self.lock:
            opens = [dict(position) for position in self.open_positions()]
            closed = [dict(position) for position in self.positions if position.get("status") == "CLOSED"]
            unrealized = round(sum(float(position.get("net_pnl") or 0) for position in opens), 2)
            realized = round(sum(float(position.get("net_pnl") or 0) for position in closed), 2)
            gross = self.gross_exposure()
            equity = round(self.cash + gross, 2)
            target_gross = round(self.book_value * self.governor.exposure_pct / 100.0, 2)
            return {
                "book_value": self.book_value,
                "cash": round(self.cash, 2),
                "equity": equity,
                "gross_exposure": gross,
                "target_gross_exposure": target_gross,
                "required_governor_cut": round(max(0.0, gross - target_gross), 2),
                "open": opens,
                "closed": closed[-100:],
                "orders": list(reversed(self.orders[-100:])),
                "open_count": len(opens),
                "order_count": len(self.orders),
                "unrealized_pnl": unrealized,
                "realized_pnl": realized,
                "total_pnl": round(unrealized + realized, 2),
                "governor": self.governor.to_dict(),
                "plan": self._state()["plan"],
                "parameter_set_id": PARAMETER_SET_ID,
                "state_version": self.state_version,
            }

    def book_summary(self) -> Dict[str, Any]:
        return self.book_payload()

    def mark_to_market(self, price_map: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.process_marks(price_map or {})

    def update_ltp(self, symbol: str, ltp: float) -> Dict[str, Any]:
        return self.process_marks({str(symbol).upper(): ltp})
