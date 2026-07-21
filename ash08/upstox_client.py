"""ASH08 Upstox client — real NSE instruments + quotes."""
from __future__ import annotations

import gzip
import io
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

LOG = logging.getLogger("ash08.upstox")
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2"


def _token() -> str:
    return (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()


def _headers(auth: bool = True) -> Dict[str, str]:
    h = {"Accept": "application/json"}
    if auth:
        tok = _token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"
    return h


def fetch_nse_equity_instruments() -> List[Dict[str, Any]]:
    LOG.info("Fetching NSE instruments from Upstox CDN")
    req = urllib.request.Request(NSE_INSTRUMENTS_URL, headers={"Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    try:
        text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode("utf-8")
    except OSError:
        text = raw.decode("utf-8")
    data = json.loads(text)
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict):
        rows = data.get("data") or data.get("instruments") or []
    out = []
    seen = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        segment = str(r.get("segment") or "").upper()
        itype = str(r.get("instrument_type") or r.get("instrumentType") or "").upper()
        exchange = str(r.get("exchange") or "").upper()
        if exchange and "NSE" not in exchange and exchange != "NSE_EQ":
            continue
        if segment and segment not in ("NSE_EQ", "EQ", "") and "EQ" not in segment:
            continue
        if itype and itype not in ("EQ", "EQUITY", ""):
            continue
        sym = str(r.get("trading_symbol") or r.get("tradingsymbol") or r.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        key = str(r.get("instrument_key") or r.get("instrumentKey") or "")
        seen.add(sym)
        out.append({
            "symbol": sym,
            "name": str(r.get("name") or sym),
            "instrument_key": key or f"NSE_EQ|{sym}",
            "isin": str(r.get("isin") or ""),
            "lot_size": int(r.get("lot_size") or r.get("lotSize") or 1),
            "tick_size": float(r.get("tick_size") or r.get("tickSize") or 0.05),
        })
    LOG.info("NSE_EQ-like instruments: %s", len(out))
    return out


def fetch_quotes(instrument_keys: List[str]) -> Dict[str, Any]:
    tok = _token()
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    result = {}
    for i in range(0, len(instrument_keys), 50):
        part = instrument_keys[i:i+50]
        keys = ",".join(part)
        url = f"{API_BASE}/market-quote/quotes?instrument_key={urllib.request.quote(keys, safe=',|')}"
        req = urllib.request.Request(url, headers=_headers(True))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Upstox quotes {e.code}: {err[:200]}") from e
        result.update(payload.get("data") or {})
    return result


def user_profile() -> Dict[str, Any]:
    if not _token():
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    req = urllib.request.Request(f"{API_BASE}/user/profile", headers=_headers(True))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))
