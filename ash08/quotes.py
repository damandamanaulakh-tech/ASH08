"""Live last prices. Upstox first, Yahoo chart last as fallback.

Never invents a mark. Missing quote = no fill. Tape close is not a fill.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

LOG = logging.getLogger("ash08.quotes")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
YAHOO_HOSTS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{sym}",
)
CACHE_TTL = 20.0
UPSTOX_COOLDOWN = 300.0
YAHOO_TIMEOUT = 8

# Snapshot CSVs used filesystem-safe stems (M.M). Live Yahoo tickers are NSE + .NS.
NSE_TO_YAHOO = {
    "M.M": "M&M",
    "M.MFIN": "M&MFIN",
}

FetchFn = Callable[[str], Optional[float]]

_CACHE: Dict[str, Any] = {"t": 0.0, "prices": {}, "source": "no_live_ltp"}
_UPX_DEAD_UNTIL = 0.0


def yahoo_symbol(nse: str) -> str:
    raw = str(nse or "").strip().upper()
    if raw.endswith(".NS"):
        raw = raw[:-3]
    raw = NSE_TO_YAHOO.get(raw, raw)
    return f"{raw}.NS"


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-IN,en;q=0.9",
        "User-Agent": BROWSER_UA,
    }


def _px_from_chart(payload: dict) -> Optional[float]:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    results = (chart or {}).get("result") if isinstance(chart, dict) else None
    if not results:
        return None
    meta = results[0].get("meta") or {}
    for key in ("regularMarketPrice", "chartPreviousClose", "previousClose"):
        v = meta.get(key)
        try:
            px = float(v)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    quotes = ((results[0].get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    for v in reversed(closes):
        try:
            px = float(v)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    return None


def fetch_yahoo_one(nse: str) -> Optional[float]:
    ysym = yahoo_symbol(nse)
    quoted = urllib.parse.quote(ysym, safe=".")
    last_err = None
    for tmpl in YAHOO_HOSTS:
        url = tmpl.format(sym=quoted)
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=YAHOO_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            px = _px_from_chart(payload)
            if px:
                return px
        except Exception as e:
            last_err = e
            continue
    if last_err:
        LOG.warning("yahoo %s: %s", nse, last_err)
    return None


def fetch_yahoo_ltp(symbols: List[str], fetch_fn: Optional[FetchFn] = None) -> Dict[str, float]:
    want = [str(s).upper() for s in (symbols or []) if s]
    if not want:
        return {}
    getter = fetch_fn or fetch_yahoo_one
    out: Dict[str, float] = {}
    workers = min(6, max(1, len(want)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(getter, s): s for s in want}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                px = fut.result()
            except Exception as e:
                LOG.warning("yahoo worker %s: %s", sym, e)
                continue
            try:
                val = float(px) if px is not None else 0.0
            except (TypeError, ValueError):
                continue
            if val > 0:
                out[sym] = val
    return out


def fetch_upstox_ltp(symbols: List[str], data_dir: str | Path = "ash08_data") -> Dict[str, float]:
    global _UPX_DEAD_UNTIL
    tok = (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()
    if not tok:
        return {}
    if time.time() < _UPX_DEAD_UNTIL:
        return {}
    try:
        from ash08.upstox_client import ltp_by_symbol

        raw = ltp_by_symbol([str(s).upper() for s in symbols if s], data_dir) or {}
    except Exception as e:
        LOG.warning("upstox ltp: %s", e)
        _UPX_DEAD_UNTIL = time.time() + UPSTOX_COOLDOWN
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            px = float(v)
        except (TypeError, ValueError):
            continue
        if px > 0:
            out[str(k).upper()] = px
    if not out:
        # Token present but empty/401-equivalent: cool down so Yahoo can fill.
        _UPX_DEAD_UNTIL = time.time() + min(60.0, UPSTOX_COOLDOWN)
    return out


def _source(upx: Dict[str, float], yah: Dict[str, float]) -> str:
    if upx and yah:
        return "mixed"
    if upx:
        return "upstox"
    if yah:
        return "yahoo"
    return "no_live_ltp"


def quotes_pack(
    symbols: List[str],
    data_dir: str | Path = "ash08_data",
    yahoo_fn: Optional[FetchFn] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    want = list(dict.fromkeys(str(s).upper() for s in (symbols or []) if s))
    if not want:
        return {"prices": {}, "source": "no_live_ltp", "upstox_n": 0, "yahoo_n": 0}

    now = time.time()
    have: Dict[str, float] = {}
    if use_cache and _CACHE["prices"] and now - float(_CACHE["t"] or 0) < CACHE_TTL:
        have = {s: _CACHE["prices"][s] for s in want if s in _CACHE["prices"]}
        missing = [s for s in want if s not in have]
        if not missing:
            return {
                "prices": have,
                "source": _CACHE.get("source") or "no_live_ltp",
                "upstox_n": 0,
                "yahoo_n": 0,
                "cached": True,
            }
    else:
        missing = want

    upx = fetch_upstox_ltp(missing, data_dir)
    still = [s for s in missing if s not in upx]
    yah = fetch_yahoo_ltp(still, fetch_fn=yahoo_fn) if still else {}
    prices = {**have, **upx, **yah}
    source = _source(upx, yah) if (upx or yah) else ("no_live_ltp" if not have else str(_CACHE.get("source") or "live"))
    if have and not upx and not yah:
        source = str(_CACHE.get("source") or "live")
    _CACHE["t"] = now
    merged = dict(_CACHE.get("prices") or {})
    merged.update(prices)
    _CACHE["prices"] = merged
    if upx or yah:
        _CACHE["source"] = source
    return {
        "prices": prices,
        "source": source if prices else "no_live_ltp",
        "upstox_n": len(upx),
        "yahoo_n": len(yah),
        "cached": False,
    }


def quotes_for_symbols(
    symbols: List[str],
    data_dir: str | Path = "ash08_data",
    yahoo_fn: Optional[FetchFn] = None,
) -> Dict[str, float]:
    return quotes_pack(symbols, data_dir=data_dir, yahoo_fn=yahoo_fn).get("prices") or {}
