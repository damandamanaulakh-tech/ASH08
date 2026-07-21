"""ASH08 Paper Engine — Phase 4. Own repo ASH08 (not AshStocks)."""
from __future__ import annotations
import argparse, json, logging, uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("ash08.paper")
MAX_NAME_PCT, DEFAULT_BOOK = 2.5, 1_000_000.0
EXPOSURE = {"L0": 100.0, "L1": 70.0, "L2": 50.0, "L3": 25.0, "L4": 15.0}

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _id(p):
    return f"{p}_{uuid.uuid4().hex[:10]}"

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

    def place_order(self, symbol, side, order_type, qty, fill_price, stop=None, target=None):
        sized = self.size_qty(qty, fill_price)
        order = {"order_id": _id("ord"), "symbol": symbol, "side": side, "qty": qty,
                 "sized_qty": sized, "fill_price": fill_price, "stop": stop, "target": target,
                 "status": "FILLED" if sized else "REJECTED", "governor": self.governor.to_dict(),
                 "created_at": _now()}
        self.orders.append(order)
        if sized and side == "BUY":
            self.positions.append({
                "position_id": _id("pos"), "symbol": symbol, "qty": sized, "entry": fill_price,
                "stop": stop, "target": target, "status": "OPEN", "opened_at": _now(), "ltp": fill_price
            })
        self._save(); return order

    def update_ltp(self, symbol, ltp):
        for p in self.positions:
            if p["status"] != "OPEN" or p["symbol"] != symbol: continue
            p["ltp"] = ltp
            if p.get("stop") is not None and ltp <= p["stop"]:
                p["status"] = "CLOSED"; p["exit_reason"] = "STOP_HIT"; p["exit_price"] = ltp
            elif p.get("target") is not None and ltp >= p["target"]:
                p["status"] = "CLOSED"; p["exit_reason"] = "TARGET_HIT"; p["exit_price"] = ltp
        self._save()

    def governor_cut(self):
        opens = [p for p in self.positions if p["status"] == "OPEN"]
        if not opens: return []
        victim = sorted(opens, key=lambda p: p["entry"] * p["qty"])[0]
        victim["status"] = "CLOSED"; victim["exit_reason"] = "GOVERNOR_CUT"
        victim["exit_price"] = victim.get("ltp") or victim["entry"]
        self._save(); return [victim]

    def _save(self):
        (self.data_dir / "paper_state.json").write_text(json.dumps({
            "governor": self.governor.to_dict(), "orders": self.orders, "positions": self.positions
        }, indent=2))

def run_demo(data_dir):
    eng = PaperEngine(data_dir)
    eng.governor = evaluate_governor(damage=True)
    o1 = eng.place_order("TCS", "BUY", "MARKET", 50, 3840, 3720, 4100)
    o2 = eng.place_order("HDFCBANK", "BUY", "LIMIT", 80, 1690, 1640, 1760)
    eng.update_ltp("HDFCBANK", 1635)
    eng.governor = evaluate_governor(damage=True, q10=True, any_fii=True)
    cut = eng.governor_cut()
    return {"orders": [o1, o2], "cut": [c["symbol"] for c in cut],
            "open": sum(1 for p in eng.positions if p["status"]=="OPEN"),
            "closed": sum(1 for p in eng.positions if p["status"]=="CLOSED"),
            "governor": eng.governor.to_dict()}

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
