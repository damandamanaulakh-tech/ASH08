"""Order / announcement family from NSE bulk, block, buyback.

Missing name = UNKNOWN (not a hard fail). Net bulk-sell = FAIL and blocks SELECT.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ash08.config import ROOT

PACK = ROOT / "ash08" / "data" / "order_events.json"
_CACHE: Optional[Dict[str, Any]] = None


def load_order_map(path: Optional[Path] = None) -> Dict[str, Any]:
    global _CACHE
    target = Path(path) if path else PACK
    if _CACHE is not None and path is None:
        return _CACHE
    if not target.exists():
        return {"asof": None, "n": 0, "symbols": {}}
    data = json.loads(target.read_text(encoding="utf-8"))
    if path is None:
        _CACHE = data
    return data


def signal_for(symbol: str, pack: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return 'buy', 'sell', or None (no evidence)."""
    pack = pack if pack is not None else load_order_map()
    rec = (pack.get("symbols") or {}).get(str(symbol or "").upper())
    if not rec:
        return None
    if rec.get("buyback"):
        return "buy"
    buy_n = int(rec.get("bulk_buy_n") or 0)
    sell_n = int(rec.get("bulk_sell_n") or 0)
    buy_q = int(rec.get("bulk_buy_qty") or 0)
    sell_q = int(rec.get("bulk_sell_qty") or 0)
    if buy_n > sell_n or buy_q > sell_q:
        return "buy"
    if sell_n > buy_n or sell_q > buy_q:
        return "sell"
    if int(rec.get("block_n") or 0) > 0:
        return "buy"
    return None


def evidence_for(symbol: str, pack: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pack = pack if pack is not None else load_order_map()
    rec = (pack.get("symbols") or {}).get(str(symbol or "").upper()) or {}
    return {
        "symbol": str(symbol or "").upper(),
        "signal": signal_for(symbol, pack),
        "asof": pack.get("asof"),
        "record": rec,
    }
