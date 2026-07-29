"""ASH08 Paper Engine — Phase 4. Auto-SELECT buys + hold/exit plan."""
from __future__ import annotations
import argparse, json, logging, uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("ash08.paper")
MAX_NAME_PCT, DEFAULT_BOOK = 2.5, 1_000_000.0
EXPOSURE = {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0}

STOP_PCT = 3.0
TARGET_PCT = 6.0
MAX_HOLD_DAYS = 15
MAX_OPEN_POSITIONS = 10
DEFAULT_QTY = 50

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _id(p):
    return f"{p}_{uuid.uuid4().hex[:10]}"

def _parse_ts(s: str):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception: return None

@dataclass
class GovState:
    level: str
    exposure_pct: float
    rationale: str
    def to_dict(self):
        return asdict(self)

def evaluate_governor(damage=False, q10=False, sell=False, any_fii=False) -> GovState:
    confirms = sum([q10, sell, any_fii])
    if damage and q10 and sell:
        return GovState("L4_EXTREME", EXPOSURE["L4"], "Q10+sell")
    if damage and confirms >= 2:
        return GovState("L3_HIGH_SEVERITY", EXPOSURE["L3"], ">=2 FII")
    if damage and confirms == 1:
        return GovState("L2_CONFIRMED", EXPOSURE["L2"], "1 FII")
    if damage:
        return GovState("L1_DAMAGE_ONLY", EXPOSURE["L1"], "damage")
    return GovState("L0_NORMAL", EXPOSURE["L0"], "normal")

class PaperEngine:
    def __init__(self, data_dir="ash08_data", book_value=DEFAULT_BOOK):
        self.data_dir = Path(data_dir); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.book_value = book_value
        self.governor = GovState("L0_NORMAL", 100.0, "init")
        self.orders, self.positions = [], []

    def size_qty(self, qty, price):
        if price <= 0 or qty <= 0: return 0
        max_n = self.book_value * (MAX_NAME_PCT/100) * (self.governor.exposure_pct/100)
        return max(0, min(qty, int(max_n // price)))

    def place_order(self, symbol, side, order_type, qty, fill_price, stop=None, target=None,
                    hold_days=None, source="manual", score=None):
        sized = self.size_qty(qty, fill_price)
        if stop is None and fill_price:
            stop = round(fill_price * (1 - STOP_PCT/100), 2)
        if target is None and fill_price:
            target = round(fill_price * (1 + TARGET_PCT/100), 2)
        hold = int(hold_days if hold_days is not None else MAX_HOLD_DAYS)
        opened = _now()
        order = {"order_id": _id("ord"), "symbol": symbol, "side": side, "qty": qty,
                 "sized_qty": sized, "fill_price": fill_price, "stop": stop, "target": target,
                 "hold_days": hold, "source": source, "score": score,
                 "status": "FILLED" if sized else "REJECTED", "governor": self.governor.to_dict(),
                 "created_at": opened,
                 "exit_plan": {"stop_pct": STOP_PCT, "target_pct": TARGET_PCT, "max_hold_days": hold,
                               "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"]}}
        self.orders.append(order)
        if sized and side == "BUY":
            self.positions.append({
                "position_id": _id("pos"), "symbol": symbol, "qty": sized, "entry": fill_price,
                "stop": stop, "target": target, "hold_days": hold, "days_held": 0, "days_left": hold,
                "status": "OPEN", "opened_at": opened, "ltp": fill_price, "source": source, "score": score,
                "stop_pct": STOP_PCT, "target_pct": TARGET_PCT, "exit_plan": order["exit_plan"],
                "exit_reason": None, "exit_price": None})
        self._save(); return order

    def open_symbols(self):
        return {p["symbol"] for p in self.positions if p.get("status") == "OPEN"}

    def refresh_hold_days(self):
        now = datetime.now(timezone.utc)
        changed = False
        for p in self.positions:
            if p.get("status") != "OPEN": continue
            opened = _parse_ts(p.get("opened_at") or "")
            if not opened: continue
            held = max(0, (now - opened).days)
            hold = int(p.get("hold_days") or MAX_HOLD_DAYS)
            p["days_held"] = held; p["days_left"] = max(0, hold - held)
            if held >= hold:
                p["status"] = "CLOSED"; p["exit_reason"] = "MAX_HOLD"
                p["exit_price"] = p.get("ltp") or p.get("entry"); p["closed_at"] = _now()
                changed = True
        if changed: self._save()

    def update_ltp(self, symbol, ltp):
        for p in self.positions:
            if p["status"] != "OPEN" or p["symbol"] != symbol: continue
            p["ltp"] = ltp
            if p.get("stop") is not None and ltp <= p["stop"]:
                p["status"] = "CLOSED"; p["exit_reason"] = "STOP_HIT"; p["exit_price"] = ltp; p["closed_at"] = _now()
            elif p.get("target") is not None and ltp >= p["target"]:
                p["status"] = "CLOSED"; p["exit_reason"] = "TARGET_HIT"; p["exit_price"] = ltp; p["closed_at"] = _now()
        self.refresh_hold_days(); self._save()

    def governor_cut(self):
        opens = [p for p in self.positions if p["status"] == "OPEN"]
        if not opens: return []
        victim = sorted(opens, key=lambda p: p["entry"] * p["qty"])[0]
        victim["status"] = "CLOSED"; victim["exit_reason"] = "GOVERNOR_CUT"
        victim["exit_price"] = victim.get("ltp") or victim["entry"]; victim["closed_at"] = _now()
        self._save(); return [victim]

    def auto_buy_selects(self, select_rows, price_map=None):
        price_map = price_map or {}
        defaults = {"TCS":3840,"HDFCBANK":1690,"RELIANCE":2950,"INFY":1850,"ICICIBANK":1180,
                    "SBIN":820,"ITC":450,"MTARTECH":1850,"COCHINSHIP":1450,"HAL":4200,"BEL":280,
                    "LT":3600,"HCLTECH":1650,"WIPRO":480,"AXISBANK":1100,"KOTAKBANK":1750}
        already = self.open_symbols(); open_n = len(already)
        bought, skipped = [], []
        for row in select_rows:
            if open_n >= MAX_OPEN_POSITIONS:
                skipped.append({"symbol": row.get("symbol"), "reason": "max_open"}); continue
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym: continue
            if sym in already:
                skipped.append({"symbol": sym, "reason": "already_open"}); continue
            price = price_map.get(sym) or row.get("ltp") or defaults.get(sym) or 100.0
            try: price = float(price)
            except Exception: price = 100.0
            order = self.place_order(symbol=sym, side="BUY", order_type="MARKET", qty=DEFAULT_QTY,
                                     fill_price=price, source="auto_select", score=row.get("score"))
            if order.get("status") == "FILLED":
                already.add(sym); open_n += 1; bought.append(order)
            else:
                skipped.append({"symbol": sym, "reason": "rejected_size"})
        self.refresh_hold_days()
        return {"bought": len(bought), "skipped": len(skipped), "orders": bought,
                "skipped_detail": skipped[:20], "open_count": open_n,
                "plan": {"stop_pct": STOP_PCT, "target_pct": TARGET_PCT, "max_hold_days": MAX_HOLD_DAYS,
                          "max_open": MAX_OPEN_POSITIONS,
                          "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"]}}

    def _save(self):
        (self.data_dir / "paper_state.json").write_text(json.dumps({
            "governor": self.governor.to_dict(), "orders": self.orders, "positions": self.positions,
            "plan": {"stop_pct": STOP_PCT, "target_pct": TARGET_PCT, "max_hold_days": MAX_HOLD_DAYS,
                      "max_open": MAX_OPEN_POSITIONS}}, indent=2))

def run_demo(data_dir):
    eng = PaperEngine(data_dir)
    eng.governor = evaluate_governor(damage=True)
    o1 = eng.place_order("TCS", "BUY", "MARKET", 50, 3840, source="demo")
    o2 = eng.place_order("HDFCBANK", "BUY", "LIMIT", 80, 1690, source="demo")
    eng.update_ltp("HDFCBANK", 1635)
    eng.governor = evaluate_governor(damage=True, q10=True, any_fii=True)
    cut = eng.governor_cut()
    return {"orders": [o1, o2], "cut": [c["symbol"] for c in cut],
            "open": sum(1 for p in eng.positions if p["status"]=="OPEN"),
            "closed": sum(1 for p in eng.positions if p["status"]=="CLOSED"),
            "governor": eng.governor.to_dict()}

def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(); p.add_argument("--demo", action="store_true"); p.add_argument("--data-dir", default="ash08_data")
    args = p.parse_args()
    if args.demo: print(json.dumps(run_demo(args.data_dir), indent=2))
    else: p.error("use --demo")

if __name__ == "__main__":
    main()
