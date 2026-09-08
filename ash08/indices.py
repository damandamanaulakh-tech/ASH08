"""G4 index tiles. Upstox quotes only. 403/missing → failed, never a fake print."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# Documented Upstox v2 instrument keys (not guessed LTPs).
INDEX_SPECS = [
    {"id": "NIFTY50", "label": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50"},
    {"id": "SENSEX", "label": "SENSEX", "instrument_key": "BSE_INDEX|SENSEX"},
    {"id": "BANKNIFTY", "label": "BANK NIFTY", "instrument_key": "NSE_INDEX|Nifty Bank"},
    {"id": "INDIAVIX", "label": "INDIA VIX", "instrument_key": "NSE_INDEX|India VIX"},
]


def _norm_key(raw: str) -> str:
    return str(raw or "").replace(" ", "").upper()


def parse_quote_blob(blob: Any) -> Dict[str, Optional[float]]:
    if not isinstance(blob, dict):
        return {"ltp": None, "change": None}
    lp = blob.get("last_price") or blob.get("lastPrice")
    if lp is None and isinstance(blob.get("ohlc"), dict):
        lp = blob["ohlc"].get("close") or blob["ohlc"].get("Close")
    ch = blob.get("net_change")
    if ch is None:
        ch = blob.get("netChange") or blob.get("change")
    try:
        ltp = float(lp) if lp is not None else None
    except Exception:
        ltp = None
    try:
        change = float(ch) if ch is not None else None
    except Exception:
        change = None
    return {"ltp": ltp, "change": change}


def lookup_quote(raw: Dict[str, Any], instrument_key: str) -> Optional[dict]:
    if not raw:
        return None
    if instrument_key in raw and isinstance(raw[instrument_key], dict):
        return raw[instrument_key]
    want = _norm_key(instrument_key)
    for k, v in raw.items():
        if _norm_key(k) == want and isinstance(v, dict):
            return v
        if isinstance(v, dict):
            ik = str(v.get("instrument_key") or v.get("instrumentKey") or "")
            if _norm_key(ik) == want:
                return v
    return None


def _failed(spec: dict, detail: str) -> dict:
    return {
        "id": spec["id"],
        "label": spec["label"],
        "instrument_key": spec["instrument_key"],
        "ltp": None,
        "change": None,
        "status": "failed",
        "detail": detail,
    }


def fetch_index_tiles(fetch_quotes: Callable[[List[str]], Dict[str, Any]], token_set: bool) -> dict:
    """Return tiles. Never invent LTP. status is ok or failed."""
    if not token_set:
        tiles = [_failed(s, "no token") for s in INDEX_SPECS]
        return {"ok": False, "status": "failed", "detail": "no token", "tiles": tiles}
    keys = [s["instrument_key"] for s in INDEX_SPECS]
    try:
        raw = fetch_quotes(keys) or {}
    except Exception as e:
        detail = str(e)[:220]
        tiles = [_failed(s, detail) for s in INDEX_SPECS]
        return {"ok": False, "status": "failed", "detail": detail, "tiles": tiles}

    tiles = []
    any_ok = False
    for spec in INDEX_SPECS:
        blob = lookup_quote(raw, spec["instrument_key"])
        parsed = parse_quote_blob(blob)
        if parsed["ltp"] is None:
            tiles.append(_failed(spec, "no last_price in Upstox payload"))
            continue
        any_ok = True
        tiles.append({
            "id": spec["id"],
            "label": spec["label"],
            "instrument_key": spec["instrument_key"],
            "ltp": parsed["ltp"],
            "change": parsed["change"],
            "status": "ok",
            "detail": "upstox",
        })
    return {
        "ok": any_ok,
        "status": "ok" if any_ok else "failed",
        "detail": "upstox" if any_ok else "no last_price in Upstox payload",
        "tiles": tiles,
    }
