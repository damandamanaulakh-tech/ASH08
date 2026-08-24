"""ASH08 Paper Engine - P&L, LTP mark-to-market, auto-SELECT. Works without Upstox."""
from __future__ import annotations
import argparse, json, logging, os, uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from .config import (
    BOOK_VALUE as DEFAULT_BOOK,
    MAX_HOLD_SESSIONS as MAX_HOLD_DAYS,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    STOP_PCT,
    TARGET_PCT,
)

LOG = logging.getLogger("ash08.paper")
EXPOSURE = {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0}
DEFAULT_QTY = 50


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(p):
    return f"{p}_{uuid.uuid4().hex[:10]}"


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


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


@dataclass
class GovState:
    level: str
    exposure_pct: float
    rationale: str
    verified: bool = False
    evidence_complete: bool = False
    evidence_fresh: bool = False

    def to_dict(self):
        return asdict(self)


def evaluate_governor(
    damage=False,
    q10=False,
    sell=False,
    any_fii=False,
    evidence_complete=False,
    evidence_fresh=False,
) -> GovState:
    verified = bool(evidence_complete and evidence_fresh)
    confirms = sum([q10, sell, any_fii])
    if damage and q10 and sell:
        state = GovState("L4", EXPOSURE["L4"], "Q10+sell")
    elif damage and confirms >= 2:
        state = GovState("L3", EXPOSURE["L3"], ">=2 FII")
    elif damage and confirms == 1:
        state = GovState("L2", EXPOSURE["L2"], "1 FII")
    elif damage:
        state = GovState("L1", EXPOSURE["L1"], "damage")
    else:
        state = GovState("L0", EXPOSURE["L0"], "normal")
    state.verified = verified
    state.evidence_complete = bool(evidence_complete)
    state.evidence_fresh = bool(evidence_fresh)
    if not verified:
        state.rationale += "; evidence unverified"
    return state


class PaperEngine:
    def __init__(self, data_dir="ash08_data", book_value=DEFAULT_BOOK):
        self._lock = RLock()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.book_value = book_value
        self.governor = GovState("L0", 100.0, "init")
        self.orders, self.positions = [], []
        self._load()

    def _load(self):
        path = self.data_dir / "paper_state.json"
        if not path.exists():
            return
        try:
            st = json.loads(path.read_text())
        except Exception as e:
            LOG.warning("paper_state load failed: %s", e)
            return
        self.orders = st.get("orders") or []
        self.positions = st.get("positions") or []
        g = st.get("governor") or {}
        if g.get("level"):
            self.governor = GovState(
                str(g.get("level")),
                float(g.get("exposure_pct") or 100),
                str(g.get("rationale") or ""),
                bool(g.get("verified")),
                bool(g.get("evidence_complete")),
                bool(g.get("evidence_fresh")),
            )

    @synchronized
    def size_qty(self, symbol, qty, price):
        if price is None or price <= 0 or qty is None or qty <= 0:
            return {"qty": 0, "requested_qty": qty, "name_headroom": 0.0}
        max_n = self.book_value * (MAX_NAME_PCT / 100) * (self.governor.exposure_pct / 100)
        current_n = sum(
            float(p.get("entry") or 0) * int(p.get("qty") or 0)
            for p in self.positions
            if p.get("status") == "OPEN" and str(p.get("symbol") or "").upper() == str(symbol or "").upper()
        )
        headroom = max(0.0, max_n - current_n)
        sized = max(0, min(int(qty), int(headroom // price)))
        return {
            "qty": sized,
            "requested_qty": int(qty),
            "name_headroom": round(headroom, 2),
            "max_name_notional": round(max_n, 2),
        }

    @synchronized
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
        idempotency_key=None,
    ):
        symbol = str(symbol or "").strip().upper()
        side = str(side or "").strip().upper()
        idempotency_key = str(idempotency_key or "").strip() or None
        if idempotency_key:
            for existing in self.orders:
                if existing.get("idempotency_key") == idempotency_key:
                    return existing
        try:
            requested_qty = int(qty)
            fill_price = float(fill_price)
        except (TypeError, ValueError):
            requested_qty = 0
            fill_price = 0.0
        reason = None
        sizing = {"qty": 0, "requested_qty": requested_qty, "name_headroom": 0.0}
        if not symbol:
            reason = "SYMBOL_REQUIRED"
        elif side not in {"BUY", "SELL"}:
            reason = "INVALID_SIDE"
        elif requested_qty <= 0:
            reason = "INVALID_QUANTITY"
        elif fill_price <= 0:
            reason = "INVALID_FILL_PRICE"
        elif side == "BUY":
            if self.open_count() >= MAX_OPEN_POSITIONS:
                reason = "MAX_OPEN_REACHED"
            else:
                sizing = self.size_qty(symbol, requested_qty, fill_price)
                if sizing["qty"] <= 0:
                    reason = "POSITION_SIZE_ZERO"
        else:
            available = sum(
                int(p.get("qty") or 0)
                for p in self.positions
                if p.get("status") == "OPEN" and str(p.get("symbol") or "").upper() == symbol
            )
            if available <= 0:
                reason = "NO_OPEN_POSITION"
            elif requested_qty > available:
                reason = "INSUFFICIENT_POSITION"
            else:
                sizing = {
                    "qty": requested_qty,
                    "requested_qty": requested_qty,
                    "name_headroom": 0.0,
                }
        sized = sizing["qty"]
        if stop is None and fill_price:
            stop = round(fill_price * (1 - STOP_PCT / 100), 2)
        if target is None and fill_price:
            target = round(fill_price * (1 + TARGET_PCT / 100), 2)
        hold = int(hold_days if hold_days is not None else MAX_HOLD_DAYS)
        opened = _now()
        order = {
            "order_id": _id("ord"),
            "symbol": symbol,
            "side": side,
            "qty": requested_qty,
            "sized_qty": sized,
            "fill_price": fill_price,
            "stop": stop,
            "target": target,
            "hold_days": hold,
            "source": source,
            "score": score,
            "status": "FILLED" if sized and not reason else "REJECTED",
            "reason": reason,
            "idempotency_key": idempotency_key,
            "sizing": sizing,
            "governor": self.governor.to_dict(),
            "created_at": opened,
            "exit_plan": {
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "max_hold_days": hold,
                "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            },
        }
        self.orders.append(order)
        if order["status"] == "FILLED" and side == "BUY":
            pnl = _pnl_fields(fill_price, fill_price, sized, status="OPEN")
            self.positions.append(
                {
                    "position_id": _id("pos"),
                    "symbol": symbol,
                    "qty": sized,
                    "entry": fill_price,
                    "stop": stop,
                    "target": target,
                    "hold_days": hold,
                    "days_held": 0,
                    "days_left": hold,
                    "status": "OPEN",
                    "opened_at": opened,
                    "ltp": fill_price,
                    "source": source,
                    "score": score,
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
                }
            )
        elif order["status"] == "FILLED" and side == "SELL":
            remaining = sized
            for position in list(self.positions):
                if remaining <= 0:
                    break
                if position.get("status") != "OPEN" or str(position.get("symbol") or "").upper() != symbol:
                    continue
                held_qty = int(position.get("qty") or 0)
                sold_qty = min(held_qty, remaining)
                remaining -= sold_qty
                if sold_qty == held_qty:
                    closed_position = position
                else:
                    closed_position = dict(position)
                    closed_position["position_id"] = _id("pos")
                    closed_position["qty"] = sold_qty
                    position["qty"] = held_qty - sold_qty
                    position.update(
                        _pnl_fields(
                            position.get("entry"),
                            position.get("ltp"),
                            position.get("qty"),
                            status="OPEN",
                        )
                    )
                    self.positions.append(closed_position)
                closed_position["status"] = "CLOSED"
                closed_position["exit_reason"] = "MANUAL_SELL"
                closed_position["exit_price"] = fill_price
                closed_position["closed_at"] = opened
                closed_position["ltp"] = fill_price
                closed_position.update(
                    _pnl_fields(
                        closed_position.get("entry"),
                        fill_price,
                        sold_qty,
                        fill_price,
                        "CLOSED",
                    )
                )
        self._save()
        return order

    @synchronized
    def open_symbols(self):
        return {p["symbol"] for p in self.positions if p.get("status") == "OPEN"}

    @synchronized
    def open_count(self):
        return sum(1 for p in self.positions if p.get("status") == "OPEN")

    @synchronized
    def refresh_hold_days(self):
        now = datetime.now(timezone.utc)
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
                p["status"] = "CLOSED"
                p["exit_reason"] = "MAX_HOLD"
                p["exit_price"] = p.get("ltp") or p.get("entry")
                p["closed_at"] = _now()
                p.update(
                    _pnl_fields(p["entry"], p.get("ltp"), p["qty"], p["exit_price"], "CLOSED")
                )
                changed = True
        if changed:
            self._save()

    @synchronized
    def mark_to_market(self, price_map=None, use_paper_marks=False):
        """Update P&L only from supplied marks; never manufacture a price."""
        price_map = price_map or {}
        for p in self.positions:
            sym = str(p.get("symbol") or "").upper()
            if p.get("status") != "OPEN":
                p.update(
                    _pnl_fields(
                        p.get("entry"), p.get("ltp"), p.get("qty"), p.get("exit_price"), "CLOSED"
                    )
                )
                continue
            ltp = price_map.get(sym)
            if ltp is None:
                ltp = p.get("ltp") or p.get("entry")
            try:
                ltp = float(ltp)
            except Exception:
                ltp = float(p.get("entry") or 0)
            p["ltp"] = round(ltp, 2)
            if p.get("stop") is not None and ltp <= float(p["stop"]):
                p["status"] = "CLOSED"
                p["exit_reason"] = "STOP_HIT"
                p["exit_price"] = ltp
                p["closed_at"] = _now()
            elif p.get("target") is not None and ltp >= float(p["target"]):
                p["status"] = "CLOSED"
                p["exit_reason"] = "TARGET_HIT"
                p["exit_price"] = ltp
                p["closed_at"] = _now()
            st = p.get("status") or "OPEN"
            fields = _pnl_fields(p.get("entry"), p.get("ltp"), p.get("qty"), p.get("exit_price"), st)
            p.update(fields)
        self.refresh_hold_days()
        self._save()

    @synchronized
    def process_marks(self, price_map=None):
        price_map = price_map or {}
        valid = {}
        for symbol, value in price_map.items():
            try:
                mark = float(value)
            except (TypeError, ValueError):
                continue
            if mark > 0:
                valid[str(symbol).upper()] = mark
        marked = sum(
            1
            for position in self.positions
            if position.get("status") == "OPEN"
            and str(position.get("symbol") or "").upper() in valid
        )
        self.mark_to_market(valid, use_paper_marks=False)
        open_count = self.open_count()
        return {"marked": marked, "missing": max(0, open_count - marked), "open_count": open_count}

    @synchronized
    def update_ltp(self, symbol, ltp):
        self.mark_to_market({str(symbol).upper(): float(ltp)}, use_paper_marks=False)

    @synchronized
    def governor_cut(self):
        opens = [p for p in self.positions if p["status"] == "OPEN"]
        if not opens:
            return []
        victim = sorted(opens, key=lambda p: float(p.get("entry") or 0) * int(p.get("qty") or 0))[0]
        victim["status"] = "CLOSED"
        victim["exit_reason"] = "GOVERNOR_CUT"
        victim["exit_price"] = victim.get("ltp") or victim["entry"]
        victim["closed_at"] = _now()
        victim.update(
            _pnl_fields(victim["entry"], victim.get("ltp"), victim["qty"], victim["exit_price"], "CLOSED")
        )
        self._save()
        return [victim]

    @synchronized
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
            if sym in already:
                skipped.append({"symbol": sym, "reason": "already_open"})
                continue
            price = price_map.get(sym)
            if price is None:
                skipped.append({"symbol": sym, "reason": "missing_live_price"})
                continue
            try:
                price = float(price)
            except Exception:
                skipped.append({"symbol": sym, "reason": "invalid_live_price"})
                continue
            if price <= 0:
                skipped.append({"symbol": sym, "reason": "invalid_live_price"})
                continue
            order = self.place_order(
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                qty=DEFAULT_QTY,
                fill_price=price,
                source="auto_select",
                score=row.get("score"),
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
            "plan": {
                "stop_pct": STOP_PCT,
                "target_pct": TARGET_PCT,
                "max_hold_days": MAX_HOLD_DAYS,
                "max_open": MAX_OPEN_POSITIONS,
                "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            },
        }

    @synchronized
    def book_payload(self, live_prices=None):
        """Recompute P&L from supplied Upstox prices without synthetic marks."""
        live_prices = live_prices or {}
        self.mark_to_market(live_prices, use_paper_marks=False)
        opens = [p for p in self.positions if p.get("status") == "OPEN"]
        closed = [p for p in self.positions if p.get("status") != "OPEN"]
        unreal = round(sum(float(p.get("unrealized_pnl") if p.get("unrealized_pnl") is not None else p.get("pnl") or 0) for p in opens), 2)
        realized = round(
            sum(float(p.get("realized_pnl") if p.get("realized_pnl") is not None else p.get("pnl") or 0) for p in closed),
            2,
        )
        return {
            "open": opens,
            "closed": closed[-30:],
            "orders": list(reversed(self.orders[-50:])),
            "open_count": len(opens),
            "order_count": len(self.orders),
            "unrealized_pnl": unreal,
            "realized_pnl": realized,
            "total_pnl": round(unreal + realized, 2),
        }

    @synchronized
    def _save(self):
        target = self.data_dir / "paper_state.json"
        temporary = self.data_dir / f".paper_state.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(
            {
                "governor": self.governor.to_dict(),
                "orders": self.orders,
                "positions": self.positions,
                "plan": {
                    "stop_pct": STOP_PCT,
                    "target_pct": TARGET_PCT,
                    "max_hold_days": MAX_HOLD_DAYS,
                    "max_open": MAX_OPEN_POSITIONS,
                },
            },
            indent=2,
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    LOG.warning("temporary paper state cleanup failed: %s", temporary)


def run_demo(data_dir):
    eng = PaperEngine(data_dir)
    eng.place_order("TCS", "BUY", "MARKET", 50, 3840, source="demo")
    eng.place_order("HDFCBANK", "BUY", "LIMIT", 80, 1690, source="demo")
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
