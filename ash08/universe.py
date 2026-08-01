"""Strict, source-traceable ASH08 universe construction."""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import ADV20_MIN, CORE_MAX, CORE_MIN, DISCOVERY_MAX, TURNOVER_CR_MIN


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
    active: bool = True
    tradable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UniverseSnapshot:
    asof: str
    bucket: str
    source: str
    status: str
    count: int
    symbols: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "active", "enabled"}


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_upstox_row(raw: Dict[str, Any]) -> Optional[InstrumentRow]:
    symbol = str(raw.get("trading_symbol") or raw.get("tradingsymbol") or raw.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    segment = str(raw.get("segment") or "NSE_EQ").upper()
    instrument_type = str(raw.get("instrument_type") or raw.get("instrumentType") or "EQ").upper()
    exchange = str(raw.get("exchange") or "NSE").upper()
    try:
        lot_size = max(1, int(raw.get("lot_size") or raw.get("lotSize") or 1))
    except (TypeError, ValueError):
        lot_size = 1
    try:
        tick_size = float(raw.get("tick_size") or raw.get("tickSize") or 0.05)
    except (TypeError, ValueError):
        tick_size = 0.05
    return InstrumentRow(
        symbol=symbol,
        name=str(raw.get("name") or symbol),
        instrument_key=str(raw.get("instrument_key") or raw.get("instrumentKey") or ""),
        exchange=exchange,
        segment=segment,
        instrument_type=instrument_type,
        isin=str(raw.get("isin") or ""),
        lot_size=lot_size,
        tick_size=tick_size,
        adv20=_optional_float(raw.get("adv20")),
        turnover_cr_5d=_optional_float(raw.get("turnover_cr_5d")),
        segment_tag=str(raw.get("segment_tag") or raw.get("sector") or ""),
        active=_to_bool(raw.get("active"), True),
        tradable=_to_bool(raw.get("tradable"), True),
    )


def load_instruments_from_json(path: Path) -> List[InstrumentRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows_raw = data if isinstance(data, list) else data.get("data") or data.get("instruments") or data.get("rows") or []
    output: List[InstrumentRow] = []
    seen = set()
    for raw in rows_raw:
        if not isinstance(raw, dict):
            continue
        row = normalize_upstox_row(raw)
        if row and row.symbol not in seen:
            seen.add(row.symbol)
            output.append(row)
    return output


def passes_core_liquidity(row: InstrumentRow) -> bool:
    return bool(
        row.active
        and row.tradable
        and row.instrument_key
        and row.adv20 is not None
        and row.turnover_cr_5d is not None
        and row.adv20 >= ADV20_MIN
        and row.turnover_cr_5d >= TURNOVER_CR_MIN
    )


def investability_score(row: InstrumentRow, preferred: set[str]) -> float:
    liquidity = math.log1p(float(row.adv20 or 0)) + math.log1p(float(row.turnover_cr_5d or 0) * 1_000_000)
    return liquidity + (0.05 if row.symbol in preferred else 0.0)


def build_discovery(rows: Sequence[InstrumentRow], source: str, max_rows: int = DISCOVERY_MAX) -> UniverseSnapshot:
    eligible = [row for row in rows if row.active and row.tradable and row.instrument_key]
    capped = eligible[:max_rows]
    return UniverseSnapshot(
        _utc_now_iso(), "discovery", source, "READY", len(capped),
        [row.symbol for row in capped], [row.to_dict() for row in capped],
        [f"capped_at={max_rows}", "active+tradable+instrument_key required"],
    )


def build_core(
    rows: Sequence[InstrumentRow],
    source: str,
    target_min: int = CORE_MIN,
    target_max: int = CORE_MAX,
    prefer_symbols: Optional[Sequence[str]] = None,
) -> UniverseSnapshot:
    preferred = {symbol.upper() for symbol in (prefer_symbols or [])}
    eligible = [row for row in rows if passes_core_liquidity(row)]
    ranked = sorted(eligible, key=lambda row: (-investability_score(row, preferred), row.symbol))
    selected = ranked[:target_max]
    status = "READY" if target_min <= len(selected) <= target_max else "BLOCKED"
    notes = [
        f"eligible={len(eligible)}",
        f"selected={len(selected)}",
        f"required_range={target_min}-{target_max}",
        "missing liquidity never passes",
        "no fallback to unfiltered instruments",
    ]
    if status == "BLOCKED":
        notes.append("CORE_NOT_TRADABLE")
    return UniverseSnapshot(
        _utc_now_iso(), "core", source, status, len(selected),
        [row.symbol for row in selected], [row.to_dict() for row in selected], notes,
    )


def save_snapshot(snapshot: UniverseSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


class UniverseManager:
    def __init__(self, data_dir: Path | str = "ash08_data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.core_path = self.data_dir / "universe_core.json"
        self.discovery_path = self.data_dir / "universe_discovery.json"

    def rebuild_from_rows(self, rows: Sequence[InstrumentRow], source: str, prefer_symbols=None):
        discovery = build_discovery(rows, source=source)
        core = build_core(rows, source=source, prefer_symbols=prefer_symbols)
        save_snapshot(discovery, self.discovery_path)
        save_snapshot(core, self.core_path)
        return {"core": core, "discovery": discovery}

    def status(self):
        return {
            "core_path": str(self.core_path),
            "discovery_path": str(self.discovery_path),
            "policy": {"core_min": CORE_MIN, "core_max": CORE_MAX, "discovery_max": DISCOVERY_MAX},
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="ash08_data")
    parser.add_argument("--from-json", type=Path)
    parser.add_argument("--source", default="local-json")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    manager = UniverseManager(args.data_dir)
    if args.status:
        print(json.dumps(manager.status(), indent=2))
        return 0
    if not args.from_json:
        parser.error("use --from-json or --status")
    result = manager.rebuild_from_rows(load_instruments_from_json(args.from_json), source=args.source)
    print(json.dumps({"core": result["core"].to_dict(), "discovery": result["discovery"].to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
