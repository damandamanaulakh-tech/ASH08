"""ASH08 Universe Manager — Phase 2. Own repo ASH08 (not AshStocks)."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

LOG = logging.getLogger("ash08.universe")
CORE_MIN, CORE_MAX, DISCOVERY_MAX = 150, 250, 5000
ADV20_MIN, TURNOVER_CR_MIN = 200_000, 5.0

@dataclass
class InstrumentRow:
    symbol: str
    name: str = ""
    instrument_key: str = ""
    exchange: str = "NSE"
    segment: str = "NSE_EQ"
    instrument_type: str = "EQ"
    isin: str = ""
    lot_size: int = 1
    tick_size: float = 0.05
    adv20: Optional[float] = None
    turnover_cr_5d: Optional[float] = None
    segment_tag: str = ""
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class UniverseSnapshot:
    asof: str
    bucket: str
    source: str
    count: int
    symbols: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def normalize_upstox_row(raw: Dict[str, Any]) -> Optional[InstrumentRow]:
    symbol = str(raw.get("trading_symbol") or raw.get("tradingsymbol") or raw.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    return InstrumentRow(symbol=symbol, name=str(raw.get("name") or symbol),
        instrument_key=str(raw.get("instrument_key") or raw.get("instrumentKey") or ""))

def load_instruments_from_json(path: Path) -> List[InstrumentRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows_raw = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict):
        rows_raw = data.get("data") or data.get("instruments") or data.get("rows") or []
    out, seen = [], set()
    for raw in rows_raw or []:
        if not isinstance(raw, dict):
            continue
        row = normalize_upstox_row(raw)
        if row and row.symbol not in seen:
            seen.add(row.symbol); out.append(row)
    return out

def passes_core_liquidity(row: InstrumentRow) -> bool:
    if row.adv20 is not None and row.adv20 < ADV20_MIN: return False
    if row.turnover_cr_5d is not None and row.turnover_cr_5d < TURNOVER_CR_MIN: return False
    return True

def build_discovery(rows: Sequence[InstrumentRow], max_rows: int = DISCOVERY_MAX) -> UniverseSnapshot:
    capped = list(rows)[:max_rows]
    return UniverseSnapshot(_utc_now_iso(), "discovery", "upstox_or_local", len(capped),
        [r.symbol for r in capped], [r.to_dict() for r in capped], [f"capped_at={max_rows}"])

def build_core(rows: Sequence[InstrumentRow], target_min: int = CORE_MIN, target_max: int = CORE_MAX,
               prefer_symbols: Optional[Sequence[str]] = None) -> UniverseSnapshot:
    prefer = {s.upper() for s in (prefer_symbols or [])}
    liquid = [r for r in rows if passes_core_liquidity(r)] or list(rows)
    ranked = sorted(liquid, key=lambda r: (0 if r.symbol in prefer else 1, r.symbol))
    selected = ranked[:target_max]
    notes = [f"selected={len(selected)}", f"target_min={target_min}"]
    if len(selected) < target_min: notes.append("WARN_below_core_min")
    return UniverseSnapshot(_utc_now_iso(), "core", "upstox_or_local", len(selected),
        [r.symbol for r in selected], [r.to_dict() for r in selected], notes)

def save_snapshot(snap: UniverseSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap.to_dict(), indent=2), encoding="utf-8")

class UniverseManager:
    def __init__(self, data_dir: Path | str = "ash08_data") -> None:
        self.data_dir = Path(data_dir); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.core_path = self.data_dir / "universe_core.json"
        self.discovery_path = self.data_dir / "universe_discovery.json"
    def rebuild_from_rows(self, rows, prefer_symbols=None):
        d, c = build_discovery(rows), build_core(rows, prefer_symbols=prefer_symbols)
        save_snapshot(d, self.discovery_path); save_snapshot(c, self.core_path)
        return {"core": c, "discovery": d}
    def status(self):
        return {"core_path": str(self.core_path), "discovery_path": str(self.discovery_path),
                "policy": {"core_min": CORE_MIN, "core_max": CORE_MAX, "discovery_max": DISCOVERY_MAX}}

def _demo_rows(n: int = 300):
    base = ["TCS","INFY","HDFCBANK","ICICIBANK","RELIANCE","SBIN","ITC","LT","AXISBANK","MARUTI"]
    rows = []
    for i in range(n):
        sym = base[i] if i < len(base) else f"SYM{i:04d}"
        rows.append(InstrumentRow(sym, sym, f"NSE_EQ|{sym}", adv20=500_000 if i < 200 else 50_000,
                                  turnover_cr_5d=20.0 if i < 200 else 1.0))
    return rows

def main(argv=None):
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="ash08_data")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--from-json", type=Path)
    args = p.parse_args(argv)
    mgr = UniverseManager(args.data_dir)
    if args.status:
        print(json.dumps(mgr.status(), indent=2)); return 0
    if args.demo:
        r = mgr.rebuild_from_rows(_demo_rows())
        print(json.dumps({"core": r["core"].count, "discovery": r["discovery"].count}, indent=2)); return 0
    if args.from_json:
        r = mgr.rebuild_from_rows(load_instruments_from_json(args.from_json))
        print(json.dumps({"core": r["core"].count, "discovery": r["discovery"].count}, indent=2)); return 0
    p.error("use --demo or --from-json or --status")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
