"""ASH08 Paper Engine - P&L, LTP mark-to-market, auto-SELECT. Numbers from ash08.config."""
from __future__ import annotations
import argparse, json, logging, uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ash08.config import (
    BOOK_VALUE as DEFAULT_BOOK,
    CASH_RESERVE_PCT,
    CONSEC_LOSS_MAX,
    GOVERNOR_EXPOSURE as EXPOSURE,
    KILL_DAILY_PCT,
    MAX_HOLD_SESSIONS as MAX_HOLD_DAYS,
    MAX_NAME_PCT,
    MAX_OPEN_POSITIONS,
    POSITION_SIZE_VALUE,
    SECTOR_MAX,
    STOP_PCT,
    TARGET_PCT,
    TICKER_BLOCKLIST,
)

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
    """Deterministic paper mark when live quotes are unavailable.
    Always applies a stable symbol-hash drift (±2%) on entry so Trade Book
    P&L is never stuck at 0 in offline / Cloudflare-blocked mode.
    """
    sym = (symbol or "").upper()
    try:
        entry = float(entry or 0)
    except Exception:
        entry = 0.0
    if entry <= 0:
        entry = float(REF_LTP.get(sym) or 100.0)
    h = sum(ord(c) for c in sym) % 41
    drift = (h - 20) / 1000.0
    return round(entry * (1.0 + drift), 2)


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
                float(g.get("exposure_pct") or EXPOSURE["L0"]),
                str(g.get("rationale") or ""),
            )

    def size_qty(self, qty, price):
        if price is None or price <= 0:
            return 0
        notional = POSITION_SIZE_VALUE * (self.governor.exposure_pct / 100.0)
        name_cap = self.book_value * (MAX_NAME_PCT / 100.0) * (self.governor.exposure_pct / 100.0)
        sized = int(min(notional, name_cap) // price)
        if qty and int(qty) > 0:
            sized = max(sized, 0)
        return max(0, sized)

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
    ):
        sized = self.size_qty(qty, fill_price)
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
                "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            },
        }
        self.orders.append(order)
        if sized and side == "BUY":
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
        self._save()
        return order

    def open_symbols(self):
        return {p["symbol"] for p in self.positions if p.get("status") == "OPEN"}

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

    def mark_to_market(self, price_map=None, use_paper_marks=True):
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
            if ltp is None and use_paper_marks:
                ltp = paper_mark_price(sym, p.get("entry") or 0)
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

    def update_ltp(self, symbol, ltp):
        self.mark_to_market({str(symbol).upper(): float(ltp)}, use_paper_marks=False)

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
            price = price_map.get(sym) or row.get("ltp") or REF_LTP.get(sym) or 100.0
            try:
                price = float(price)
            except Exception:
                price = 100.0
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
        mtm = dict(REF_LTP)
        mtm.update(price_map)
        self.mark_to_market(mtm, use_paper_marks=True)
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

    def book_payload(self, live_prices=None):
        live_prices = live_prices or {}
        self.mark_to_market(live_prices, use_paper_marks=True)
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

    def _save(self):
        (self.data_dir / "paper_state.json").write_text(
            json.dumps(
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
        )


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
