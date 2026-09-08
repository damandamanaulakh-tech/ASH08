"""G5 segments. A name is in a segment only if it is on this map. No guessing."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

SEGMENT_ORDER = ("Oil", "Gold", "Metals", "IT", "Finance")

# Exclusive membership. First segment in SEGMENT_ORDER wins if a ticker is listed twice.
# Gold = jewellery + gold-loan NBFCs (India has no large listed bullion miner set).
SEGMENT_MEMBERS: Dict[str, tuple] = {
    "Oil": (
        "RELIANCE", "ONGC", "IOC", "BPCL", "HINDPETRO", "GAIL", "OIL", "PETRONET",
        "IGL", "ATGL", "GUJGASLTD", "GSPL", "AEGISLOG", "CASTROLIND",
    ),
    "Gold": (
        "TITAN", "KALYANKJIL", "RAJESHEXPO", "THANGAMAYL", "MANAPPURAM", "MUTHOOTFIN",
    ),
    "Metals": (
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "HINDZINC", "JINDALSTEL",
        "SAIL", "NMDC", "NATIONALUM", "COALINDIA", "MOIL", "APLAPOLLO",
        "RATNAMANI", "WELCORP", "HINDCOPPER",
    ),
    "IT": (
        "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "PERSISTENT",
        "COFORGE", "MPHASIS", "LTTS", "OFSS", "CYIENT", "KPITTECH", "BSOFT",
        "NEWGEN", "INTELLECT", "TATAELXSI", "SONATSOFTW",
    ),
    "Finance": (
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
        "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "IOB", "IDBI", "FEDERALBNK",
        "IDFCFIRSTB", "BANDHANBNK", "AUBANK", "BAJFINANCE", "BAJAJFINSV",
        "CHOLAFIN", "SHRIRAMFIN", "HDFCLIFE", "SBILIFE", "ICICIPRULI",
        "ICICIGI", "SBICARD", "PFC", "RECLTD", "IRFC", "LICI", "JIOFIN",
        "LICHSGFIN",
    ),
}


def _build_lookup() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for seg in SEGMENT_ORDER:
        for raw in SEGMENT_MEMBERS[seg]:
            sym = str(raw).upper()
            out.setdefault(sym, seg)
    return out


LOOKUP = _build_lookup()


def segment_of(symbol: str) -> Optional[str]:
    return LOOKUP.get(str(symbol or "").strip().upper())


def _empty_bucket(name: str) -> dict:
    return {
        "segment": name,
        "core_count": 0,
        "select_count": 0,
        "watch_count": 0,
        "reject_count": 0,
        "unknown_count": 0,
        "unscanned_count": 0,
        "symbols": [],
        "select": [],
    }


def segment_snapshot(
    core_symbols: Sequence[str],
    scan_rows: Optional[Iterable[dict]] = None,
) -> dict:
    core = [str(s).upper() for s in (core_symbols or []) if str(s).strip()]
    core_set = set(core)
    by_seg = {name: _empty_bucket(name) for name in SEGMENT_ORDER}
    mapped = 0
    for sym in core:
        seg = LOOKUP.get(sym)
        if not seg:
            continue
        mapped += 1
        bucket = by_seg[seg]
        bucket["symbols"].append(sym)
        bucket["core_count"] += 1

    scan_map = {}
    for row in scan_rows or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        if sym:
            scan_map[sym] = row

    for name, bucket in by_seg.items():
        for sym in bucket["symbols"]:
            row = scan_map.get(sym)
            if not row:
                bucket["unscanned_count"] += 1
                continue
            dec = str(row.get("decision") or "").upper()
            if dec == "SELECT":
                bucket["select_count"] += 1
                bucket["select"].append({
                    "symbol": sym,
                    "score": row.get("score"),
                })
            elif dec == "WATCH":
                bucket["watch_count"] += 1
            elif dec == "UNKNOWN":
                bucket["unknown_count"] += 1
            else:
                bucket["reject_count"] += 1

    notes = [
        "membership=documented_map_only",
        "gold=jewellery_and_gold_loan_nbfc",
        f"map_size={len(LOOKUP)}",
        f"core_n={len(core)}",
        f"core_mapped={mapped}",
        f"core_unmapped={len(core) - mapped}",
        "not_an_analysis_essay",
    ]
    return {
        "ok": True,
        "segments": [by_seg[name] for name in SEGMENT_ORDER],
        "notes": notes,
        "unmapped_core": len(core) - mapped,
        "core_n": len(core),
        "scan_n": len(scan_map),
    }
