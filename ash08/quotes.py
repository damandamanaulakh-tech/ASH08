"""Live last prices.

NSE session (Mon–Fri 09:15–15:30 IST): Upstox only. No Yahoo fill.
After hours / weekend: Yahoo only. Never invents a mark. Tape close is not a fill.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ash08.session import session_state

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
IST = timezone(timedelta(hours=5, minutes=30))

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


def chart_symbol(nse: str) -> str:
    """Yahoo chart ticker. Index codes (^NSEI) stay as-is; NSE names get .NS."""
    raw = str(nse or "").strip()
    if raw.startswith("^"):
        return raw
    return yahoo_symbol(raw)


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
    ysym = chart_symbol(nse)
    quoted = urllib.parse.quote(ysym, safe=".-")
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
        # Token present but empty/401-equivalent. Session will not fall to Yahoo.
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
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    want = list(dict.fromkeys(str(s).upper() for s in (symbols or []) if s))
    sess = session_state(now)
    quote_mode = sess["quote_mode"]
    empty = {
        "prices": {},
        "source": "no_live_ltp",
        "upstox_n": 0,
        "yahoo_n": 0,
        "quote_mode": quote_mode,
        "session": sess["why"],
        "cached": False,
    }
    if not want:
        return empty

    tnow = time.time()
    have: Dict[str, float] = {}
    cache_ok = (
        use_cache
        and _CACHE["prices"]
        and _CACHE.get("mode") == quote_mode
        and tnow - float(_CACHE["t"] or 0) < CACHE_TTL
    )
    if cache_ok:
        have = {s: _CACHE["prices"][s] for s in want if s in _CACHE["prices"]}
        missing = [s for s in want if s not in have]
        if not missing:
            return {
                "prices": have,
                "source": _CACHE.get("source") or "no_live_ltp",
                "upstox_n": 0,
                "yahoo_n": 0,
                "quote_mode": quote_mode,
                "session": sess["why"],
                "cached": True,
            }
    else:
        missing = want

    upx: Dict[str, float] = {}
    yah: Dict[str, float] = {}
    if quote_mode == "upstox":
        upx = fetch_upstox_ltp(missing, data_dir)
    else:
        yah = fetch_yahoo_ltp(missing, fetch_fn=yahoo_fn) if missing else {}

    prices = {**have, **upx, **yah}
    if upx:
        source = "upstox"
    elif yah:
        source = "yahoo"
    elif have:
        source = str(_CACHE.get("source") or "no_live_ltp")
    else:
        source = "no_live_ltp"
    _CACHE["t"] = tnow
    _CACHE["mode"] = quote_mode
    merged = dict(_CACHE.get("prices") or {}) if _CACHE.get("mode") == quote_mode else {}
    merged.update(prices)
    _CACHE["prices"] = merged
    if upx or yah:
        _CACHE["source"] = source
    return {
        "prices": prices,
        "source": source if prices else "no_live_ltp",
        "upstox_n": len(upx),
        "yahoo_n": len(yah),
        "quote_mode": quote_mode,
        "session": sess["why"],
        "cached": False,
    }


def quotes_for_symbols(
    symbols: List[str],
    data_dir: str | Path = "ash08_data",
    yahoo_fn: Optional[FetchFn] = None,
    now: Optional[datetime] = None,
) -> Dict[str, float]:
    return quotes_pack(symbols, data_dir=data_dir, yahoo_fn=yahoo_fn, now=now).get("prices") or {}


def _bar_num(arr: list, i: int, fallback: float) -> float:
    if i >= len(arr) or arr[i] is None:
        return fallback
    try:
        v = float(arr[i])
    except (TypeError, ValueError):
        return fallback
    return v if v > 0 else fallback


def bars_from_yahoo_chart(payload: dict) -> Optional[Dict[str, list]]:
    """Parse a Yahoo v8 chart payload into aligned daily bars. None if thin."""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    results = (chart or {}).get("result") if isinstance(chart, dict) else None
    if not results:
        return None
    res = results[0] or {}
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    dates: List[str] = []
    o_out: List[float] = []
    h_out: List[float] = []
    l_out: List[float] = []
    c_out: List[float] = []
    v_out: List[float] = []
    n = min(len(ts), len(closes))
    for i in range(n):
        try:
            px = float(closes[i])
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        try:
            d = datetime.fromtimestamp(int(ts[i]), IST).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        dates.append(d)
        c_out.append(px)
        o_out.append(_bar_num(opens, i, px))
        h_out.append(_bar_num(highs, i, px))
        l_out.append(_bar_num(lows, i, px))
        try:
            vv = float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0
        except (TypeError, ValueError):
            vv = 0.0
        v_out.append(max(0.0, vv))
    if len(c_out) < 60:
        return None
    return {
        "dates": dates,
        "opens": o_out,
        "highs": h_out,
        "lows": l_out,
        "closes": c_out,
        "vols": v_out,
    }


def fetch_yahoo_daily(
    nse: str,
    range_str: str = "5y",
    timeout: int = 20,
) -> Optional[Dict[str, list]]:
    """Daily OHLCV from Yahoo chart. None if Yahoo has no series. Never invents bars."""
    ysym = yahoo_symbol(nse)
    quoted = urllib.parse.quote(ysym, safe=".-")
    last_err: Optional[BaseException] = None
    for tmpl in YAHOO_HOSTS:
        url = tmpl.format(sym=quoted) + "?interval=1d&range=" + urllib.parse.quote(range_str)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=_headers())
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                bars = bars_from_yahoo_chart(payload)
                if bars:
                    return bars
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 502, 503):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
                continue
    if last_err:
        LOG.warning("yahoo daily %s: %s", nse, last_err)
    return None


def fetch_yahoo_daily_many(
    symbols: List[str],
    range_str: str = "5y",
    workers: int = 4,
) -> Dict[str, Dict[str, list]]:
    want = list(dict.fromkeys(str(s).upper() for s in (symbols or []) if s))
    out: Dict[str, Dict[str, list]] = {}
    if not want:
        return out
    n_workers = min(max(1, workers), max(1, len(want)))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = {pool.submit(fetch_yahoo_daily, s, range_str): s for s in want}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                bars = fut.result()
            except Exception as e:
                LOG.warning("yahoo daily worker %s: %s", sym, e)
                continue
            if bars:
                out[sym] = bars
    return out
