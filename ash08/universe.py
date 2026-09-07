"""ASH08 Universe Manager — G1 Core 150–250 weekly, Discovery on demand.

Does not invent ADV/turnover. If those fields are missing, Core is a prefer-rank
cap (seed order), tagged liquidity_evidence=pending_G2.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ash08.config import (
    ADV20_MIN,
    CORE_MAX,
    CORE_MIN,
    CORE_TTL_DAYS,
    DISCOVERY_MAX,
    TURNOVER_CR_MIN,
    UNIVERSE_POLICY_ID,
)

LOG = logging.getLogger("ash08.universe")


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
    policy_id: str = UNIVERSE_POLICY_ID

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_asof(asof: str) -> Optional[datetime]:
    raw = (asof or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def rows_from_symbols(symbols: Sequence[str]) -> List[InstrumentRow]:
    """Membership rows only. No invented ADV/turnover/LTP."""
    out, seen = [], set()
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(InstrumentRow(symbol=sym, name=sym, instrument_key=f"NSE_EQ|{sym}"))
    return out


def normalize_upstox_row(raw: Dict[str, Any]) -> Optional[InstrumentRow]:
    symbol = str(raw.get("trading_symbol") or raw.get("tradingsymbol") or raw.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    adv = raw.get("adv20")
    to = raw.get("turnover_cr_5d")
    try:
        adv_f = float(adv) if adv is not None else None
    except Exception:
        adv_f = None
    try:
        to_f = float(to) if to is not None else None
    except Exception:
        to_f = None
    return InstrumentRow(
        symbol=symbol,
        name=str(raw.get("name") or symbol),
        instrument_key=str(raw.get("instrument_key") or raw.get("instrumentKey") or f"NSE_EQ|{symbol}"),
        adv20=adv_f,
        turnover_cr_5d=to_f,
    )


def load_instruments_from_json(path: Path) -> List[InstrumentRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows_raw = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict):
        rows_raw = data.get("data") or data.get("instruments") or data.get("rows") or data.get("symbols") or []
    if rows_raw and isinstance(rows_raw, list) and rows_raw and isinstance(rows_raw[0], str):
        return rows_from_symbols(rows_raw)
    out, seen = [], set()
    for raw in rows_raw or []:
        if not isinstance(raw, dict):
            continue
        row = normalize_upstox_row(raw)
        if row and row.symbol not in seen:
            seen.add(row.symbol)
            out.append(row)
    return out


def has_liquidity_evidence(row: InstrumentRow) -> bool:
    return row.adv20 is not None or row.turnover_cr_5d is not None


def passes_core_liquidity(row: InstrumentRow) -> bool:
    """Missing evidence is not a pass. Do not invent numbers."""
    if not has_liquidity_evidence(row):
        return False
    if row.adv20 is not None and row.adv20 < ADV20_MIN:
        return False
    if row.turnover_cr_5d is not None and row.turnover_cr_5d < TURNOVER_CR_MIN:
        return False
    return True


def build_discovery(rows: Sequence[InstrumentRow], max_rows: int = DISCOVERY_MAX) -> UniverseSnapshot:
    capped = list(rows)[:max_rows]
    return UniverseSnapshot(
        _utc_now_iso(),
        "discovery",
        "seed_or_upstox",
        len(capped),
        [r.symbol for r in capped],
        [r.to_dict() for r in capped],
        [f"capped_at={max_rows}", "not_for_auto_buy"],
    )


def build_core(
    rows: Sequence[InstrumentRow],
    target_min: int = CORE_MIN,
    target_max: int = CORE_MAX,
    prefer_symbols: Optional[Sequence[str]] = None,
) -> UniverseSnapshot:
    prefer_list = [str(s).strip().upper() for s in (prefer_symbols or []) if str(s).strip()]
    rank: Dict[str, int] = {}
    for i, s in enumerate(prefer_list):
        rank.setdefault(s, i)
    proven = [r for r in rows if has_liquidity_evidence(r)]
    notes: List[str] = [f"policy={UNIVERSE_POLICY_ID}", f"core_band={target_min}-{target_max}"]
    if proven:
        pool = [r for r in proven if passes_core_liquidity(r)]
        notes.append("liquidity_evidence=applied")
        notes.append(f"proven={len(proven)}")
        notes.append(f"liquid={len(pool)}")
    else:
        pool = list(rows)
        notes.append("liquidity_evidence=pending_G2")
        notes.append("selection=prefer_rank_cap")
    ranked = sorted(pool, key=lambda r: (rank.get(r.symbol, 10**9), r.symbol))
    selected = ranked[:target_max]
    notes.append(f"selected={len(selected)}")
    if len(selected) < target_min:
        notes.append("WARN_below_core_min")
    if len(selected) > target_max:
        selected = selected[:target_max]
        notes.append("capped_at_core_max")
    return UniverseSnapshot(
        _utc_now_iso(),
        "core",
        "universe_g1",
        len(selected),
        [r.symbol for r in selected],
        [r.to_dict() for r in selected],
        notes,
    )


def save_snapshot(snap: UniverseSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap.to_dict(), indent=2), encoding="utf-8")


def core_count_ok(data: Optional[dict], target_min: int = CORE_MIN, target_max: int = CORE_MAX) -> bool:
    if not data or not isinstance(data, dict):
        return False
    symbols = [str(s).upper() for s in (data.get("symbols") or []) if s]
    n = len(symbols)
    return target_min <= n <= target_max


def core_is_fresh(data: Optional[dict], ttl_days: int = CORE_TTL_DAYS) -> bool:
    if not core_count_ok(data):
        return False
    dt = _parse_asof(str(data.get("asof") or ""))
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    return age.total_seconds() <= max(0, int(ttl_days)) * 86400


class UniverseManager:
    def __init__(self, data_dir: Path | str = "ash08_data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.core_path = self.data_dir / "universe_core.json"
        self.discovery_path = self.data_dir / "universe_discovery.json"

    def load_core(self) -> Optional[dict]:
        if not self.core_path.exists():
            return None
        try:
            return json.loads(self.core_path.read_text(encoding="utf-8"))
        except Exception as e:
            LOG.warning("load_core: %s", e)
            return None

    def load_discovery(self) -> Optional[dict]:
        if not self.discovery_path.exists():
            return None
        try:
            return json.loads(self.discovery_path.read_text(encoding="utf-8"))
        except Exception as e:
            LOG.warning("load_discovery: %s", e)
            return None

    def rebuild_from_rows(self, rows, prefer_symbols=None):
        d, c = build_discovery(rows), build_core(rows, prefer_symbols=prefer_symbols)
        save_snapshot(d, self.discovery_path)
        save_snapshot(c, self.core_path)
        return {"core": c, "discovery": d}

    def ensure_core(
        self,
        symbols: Sequence[str],
        force: bool = False,
        ttl_days: int = CORE_TTL_DAYS,
    ) -> Tuple[dict, bool]:
        """Return (snapshot_dict, rebuilt). Restart-safe: keep fresh 150–250 Core."""
        existing = self.load_core()
        if not force and core_is_fresh(existing, ttl_days=ttl_days):
            return existing or {}, False
        rows = rows_from_symbols(symbols)
        core = build_core(rows, prefer_symbols=symbols)
        save_snapshot(core, self.core_path)
        return core.to_dict(), True

    def rebuild_discovery(self, symbols: Sequence[str]) -> dict:
        rows = rows_from_symbols(symbols)
        snap = build_discovery(rows)
        save_snapshot(snap, self.discovery_path)
        return snap.to_dict()

    def status(self):
        core = self.load_core() or {}
        disc = self.load_discovery() or {}
        return {
            "core_path": str(self.core_path),
            "discovery_path": str(self.discovery_path),
            "policy": {
                "id": UNIVERSE_POLICY_ID,
                "core_min": CORE_MIN,
                "core_max": CORE_MAX,
                "discovery_max": DISCOVERY_MAX,
                "core_ttl_days": CORE_TTL_DAYS,
            },
            "core_count": len(core.get("symbols") or []),
            "core_fresh": core_is_fresh(core),
            "discovery_count": len(disc.get("symbols") or []),
        }


def _demo_rows(n: int = 300):
    base = ["TCS", "INFY", "HDFCBANK", "ICICIBANK", "RELIANCE", "SBIN", "ITC", "LT", "AXISBANK", "MARUTI"]
    rows = []
    for i in range(n):
        sym = base[i] if i < len(base) else f"SYM{i:04d}"
        rows.append(
            InstrumentRow(
                sym,
                sym,
                f"NSE_EQ|{sym}",
                adv20=500_000 if i < 200 else 50_000,
                turnover_cr_5d=20.0 if i < 200 else 1.0,
            )
        )
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
        print(json.dumps(mgr.status(), indent=2))
        return 0
    if args.demo:
        r = mgr.rebuild_from_rows(_demo_rows())
        print(json.dumps({"core": r["core"].count, "discovery": r["discovery"].count}, indent=2))
        return 0
    if args.from_json:
        r = mgr.rebuild_from_rows(load_instruments_from_json(args.from_json))
        print(json.dumps({"core": r["core"].count, "discovery": r["discovery"].count}, indent=2))
        return 0
    p.error("use --demo or --from-json or --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
