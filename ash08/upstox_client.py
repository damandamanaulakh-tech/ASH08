"""Minimal Upstox client with correct encoding, bounded retries and exact instrument keys."""
from __future__ import annotations

import gzip
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import quote

LOG = logging.getLogger("ash08.upstox")
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2"


def _token() -> str:
    return (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()


def _headers(auth: bool = True) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "ASH08/50L-v1"}
    if auth:
        token = _token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _urlopen_json(request: urllib.request.Request, timeout: int, retries: int = 2) -> Dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code < 500 or attempt >= retries:
                raise RuntimeError(f"Upstox HTTP {exc.code}: {body[:300]}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= retries:
                break
        time.sleep(0.25 * (2**attempt))
    raise RuntimeError(f"Upstox request failed: {last_error}") from last_error


def fetch_nse_equity_instruments() -> List[Dict[str, Any]]:
    request = urllib.request.Request(NSE_INSTRUMENTS_URL, headers={"Accept": "*/*", "User-Agent": "ASH08/50L-v1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read()
    try:
        text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode("utf-8")
    except OSError:
        text = raw.decode("utf-8")
    data = json.loads(text)
    rows = data if isinstance(data, list) else data.get("data") or data.get("instruments") or []
    output: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        segment = str(row.get("segment") or "").upper()
        instrument_type = str(row.get("instrument_type") or row.get("instrumentType") or "").upper()
        exchange = str(row.get("exchange") or "").upper()
        if exchange and "NSE" not in exchange:
            continue
        if segment and "EQ" not in segment:
            continue
        if instrument_type and instrument_type not in {"EQ", "EQUITY"}:
            continue
        symbol = str(row.get("trading_symbol") or row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
        key = str(row.get("instrument_key") or row.get("instrumentKey") or "").strip()
        if not symbol or not key or symbol in seen:
            continue
        seen.add(symbol)
        output.append({
            "symbol": symbol,
            "name": str(row.get("name") or symbol),
            "instrument_key": key,
            "exchange": exchange or "NSE",
            "segment": segment or "NSE_EQ",
            "instrument_type": instrument_type or "EQ",
            "isin": str(row.get("isin") or ""),
            "lot_size": int(row.get("lot_size") or row.get("lotSize") or 1),
            "tick_size": float(row.get("tick_size") or row.get("tickSize") or 0.05),
            "active": True,
            "tradable": True,
        })
    LOG.info("Fetched %s NSE equity instruments", len(output))
    return output


def fetch_quotes(instrument_keys: List[str]) -> Dict[str, Any]:
    if not _token():
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    clean_keys = [str(key).strip() for key in instrument_keys if str(key).strip()]
    result: Dict[str, Any] = {}
    for start in range(0, len(clean_keys), 50):
        keys = ",".join(clean_keys[start:start + 50])
        encoded = quote(keys, safe=",|")
        url = f"{API_BASE}/market-quote/quotes?instrument_key={encoded}"
        payload = _urlopen_json(urllib.request.Request(url, headers=_headers(True)), timeout=60)
        result.update(payload.get("data") or {})
    return result


def user_profile() -> Dict[str, Any]:
    if not _token():
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    return _urlopen_json(
        urllib.request.Request(f"{API_BASE}/user/profile", headers=_headers(True)),
        timeout=30,
        retries=1,
    )
