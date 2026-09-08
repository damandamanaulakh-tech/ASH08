"""ASH08 Upstox client - real NSE instruments + quotes."""
from __future__ import annotations

import gzip
import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

LOG = logging.getLogger("ash08.upstox")
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2"
# Cloudflare 1010 blocks Python-urllib UA. Browser UA reaches the API (401 = token, 1010 = WAF).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _token() -> str:
    return (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()


def _headers(auth: bool = True) -> Dict[str, str]:
    h = {
        "Accept": "application/json",
        "Accept-Language": "en-IN,en;q=0.9",
        "User-Agent": BROWSER_UA,
        "Api-Version": "2.0",
    }
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


def _norm_ikey(raw: str) -> str:
    return str(raw or "").replace(" ", "").replace("|", ":").upper()


def load_eq_keymap(data_dir: str | Path = "ash08_data") -> Dict[str, str]:
    """trading_symbol → NSE_EQ|<ISIN>. Cached. Never invent ISINs."""
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "nse_eq_keys.json"
    if path.exists():
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age < 20 * 3600:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and len(data) > 50:
                    return {str(k).upper(): str(v) for k, v in data.items() if k and v}
            except Exception as e:
                LOG.warning("keymap cache: %s", e)
    rows = fetch_nse_equity_instruments()
    km: Dict[str, str] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        key = str(r.get("instrument_key") or "")
        if sym and key:
            km[sym] = key
    if km:
        path.write_text(json.dumps(km), encoding="utf-8")
        LOG.info("eq keymap %s names", len(km))
    return km


def ltp_by_symbol(symbols: List[str], data_dir: str | Path = "ash08_data") -> Dict[str, float]:
    km = load_eq_keymap(data_dir)
    keys = []
    for raw in symbols or []:
        sym = str(raw or "").upper()
        if sym in km:
            keys.append(km[sym])
    if not keys:
        return {}
    raw = fetch_quotes(keys)
    rev = {_norm_ikey(v): k for k, v in km.items()}
    out: Dict[str, float] = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        lp = v.get("last_price") or v.get("lastPrice")
        if lp is None and isinstance(v.get("ohlc"), dict):
            lp = v["ohlc"].get("close")
        if lp is None:
            continue
        sym = rev.get(_norm_ikey(k))
        if not sym:
            continue
        try:
            out[sym] = float(lp)
        except Exception:
            pass
    return out


def fetch_quotes(instrument_keys: List[str]) -> Dict[str, Any]:
    tok = _token()
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    result = {}
    for i in range(0, len(instrument_keys), 50):
        part = instrument_keys[i:i+50]
        keys = ",".join(part)
        url = f"{API_BASE}/market-quote/quotes?instrument_key={urllib.parse.quote(keys, safe=',|')}"
        req = urllib.request.Request(url, headers=_headers(True))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Upstox quotes {e.code}: {err[:200]}") from e
        result.update(payload.get("data") or {})
    return result


def fetch_historical_daily(instrument_key: str, from_date: str, to_date: str) -> List[List[Any]]:
    """Daily candles. Returns list of [ts, o, h, l, c, volume, oi]."""
    tok = _token()
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    key = urllib.parse.quote(instrument_key, safe="")
    url = f"{API_BASE}/historical-candle/{key}/day/{to_date}/{from_date}"
    req = urllib.request.Request(url, headers=_headers(True))
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Upstox history {e.code}: {err[:200]}") from e
    data = payload.get("data") if isinstance(payload, dict) else None
    candles = (data or {}).get("candles") if isinstance(data, dict) else None
    return candles or []


def user_profile() -> Dict[str, Any]:
    if not _token():
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing")
    req = urllib.request.Request(f"{API_BASE}/user/profile", headers=_headers(True))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))
