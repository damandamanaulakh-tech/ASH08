"""Yahoo Finance NSE quotes — no API key. Used when Upstox is blocked (Cloudflare 1010)."""
from __future__ import annotations
import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List

LOG = logging.getLogger("ash08.yahoo")
UA = "Mozilla/5.0 (compatible; ASH08/1.0; +https://ash08-desk.onrender.com)"


def _one(symbol: str, timeout: float = 10.0):
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        res = (d.get("chart") or {}).get("result") or []
        if not res:
            return None
        meta = res[0].get("meta") or {}
        px = meta.get("regularMarketPrice")
        if px is None:
            px = meta.get("previousClose")
        if px is None:
            return None
        return sym, float(px)
    except Exception as e:
        LOG.debug("yahoo %s: %s", sym, e)
        return None


def fetch_yahoo_quotes(symbols: Iterable[str], max_workers: int = 8) -> Dict[str, float]:
    """Return {SYMBOL: last_price} for NSE names. Empty dict on total failure."""
    uniq: List[str] = []
    seen = set()
    for s in symbols:
        u = str(s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    if not uniq:
        return {}
    out: Dict[str, float] = {}
    workers = min(max_workers, max(1, len(uniq)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, s): s for s in uniq}
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception:
                continue
            if row:
                out[row[0]] = row[1]
    LOG.info("yahoo quotes %s/%s", len(out), len(uniq))
    return out
