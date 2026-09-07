"""G2 bar history: local cache first, Upstox daily candles second. Never invent OHLCV."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ash08.config import HISTORY_TTL_HOURS, METRICS_REFRESH_BATCH, MOM_LOOKBACK_CAL_DAYS

LOG = logging.getLogger("ash08.history")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_bar_date(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "T" in text:
        text = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except Exception:
            text = text[:10]
    if len(text) >= 10:
        return text[:10]
    return None


def normalize_bars(raw_bars: Any) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw_bars or []:
        if isinstance(item, (list, tuple)) and len(item) >= 6:
            date = parse_bar_date(item[0])
            try:
                o, h, l, c, vol = float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5])
            except Exception:
                continue
        elif isinstance(item, dict):
            date = parse_bar_date(item.get("date") or item.get("timestamp") or item.get("ts"))
            try:
                o = float(item.get("open"))
                h = float(item.get("high"))
                l = float(item.get("low"))
                c = float(item.get("close"))
                vol = float(item.get("volume") or 0)
            except Exception:
                continue
        else:
            continue
        if not date or c <= 0:
            continue
        out[date] = {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": max(vol, 0.0)}
    return [out[k] for k in sorted(out)]


class HistoryStore:
    def __init__(self, data_dir: str | Path = "ash08_data"):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "history"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        return self.root / f"{str(symbol).strip().upper()}.json"

    def load(self, symbol: str) -> List[Dict[str, Any]]:
        path = self.path_for(symbol)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            LOG.warning("history load %s: %s", symbol, e)
            return []
        return normalize_bars(payload.get("bars") if isinstance(payload, dict) else payload)

    def save(self, symbol: str, bars: List[Dict[str, Any]], source: str) -> None:
        body = {
            "symbol": str(symbol).upper(),
            "source": source,
            "asof": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bars": normalize_bars(bars),
        }
        self.path_for(symbol).write_text(json.dumps(body), encoding="utf-8")

    def is_fresh(self, symbol: str, ttl_hours: int = HISTORY_TTL_HOURS) -> bool:
        path = self.path_for(symbol)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            asof = parse_bar_date((payload or {}).get("asof"))
            if not asof:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                age = _utc_now() - mtime
            else:
                dt = datetime.fromisoformat(str(payload.get("asof")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = _utc_now() - dt
            return age.total_seconds() <= ttl_hours * 3600 and bool(self.load(symbol))
        except Exception:
            return False

    def refresh_symbol(self, symbol: str, instrument_key: Optional[str] = None) -> Dict[str, Any]:
        """Fetch Upstox daily bars. Returns status; never fabricates candles."""
        sym = str(symbol).upper()
        key = instrument_key or f"NSE_EQ|{sym}"
        to_d = _utc_now().date()
        from_d = to_d - timedelta(days=MOM_LOOKBACK_CAL_DAYS + 20)
        try:
            from ash08.upstox_client import fetch_historical_daily
            candles = fetch_historical_daily(key, from_d.isoformat(), to_d.isoformat())
        except Exception as e:
            LOG.warning("upstox history %s: %s", sym, e)
            return {"symbol": sym, "ok": False, "error": str(e)[:200], "bars": len(self.load(sym))}
        bars = normalize_bars(candles)
        if not bars:
            return {"symbol": sym, "ok": False, "error": "empty candles", "bars": 0}
        self.save(sym, bars, source="upstox")
        return {"symbol": sym, "ok": True, "bars": len(bars), "source": "upstox"}

    def refresh_many(self, symbols, instrument_keys=None, force=False, limit=METRICS_REFRESH_BATCH):
        keys = instrument_keys or {}
        results = []
        n = 0
        for raw in symbols:
            if n >= limit:
                break
            sym = str(raw or "").strip().upper()
            if not sym:
                continue
            if not force and self.is_fresh(sym):
                results.append({"symbol": sym, "ok": True, "skipped": "fresh", "bars": len(self.load(sym))})
                continue
            results.append(self.refresh_symbol(sym, keys.get(sym)))
            n += 1
        return results
