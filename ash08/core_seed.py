"""ASH08 core universe seed loader."""
from __future__ import annotations
import json
from pathlib import Path

_PATH = Path(__file__).with_name("core_symbols.json")

def _load():
    if _PATH.exists():
        return json.loads(_PATH.read_text(encoding="utf-8"))
    return ["TCS", "HDFCBANK", "RELIANCE", "INFY", "ICICIBANK"]

CORE_SYMBOLS = _load()
CORE_COUNT = len(CORE_SYMBOLS)
