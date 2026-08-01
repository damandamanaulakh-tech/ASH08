"""ASH08 50-lakh reviewed baseline API."""
from __future__ import annotations

import hmac
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ash08.config import (
    ALLOW_DEMO,
    API_TOKEN,
    BOOK_VALUE,
    DATA_DIR,
    MAX_BODY_BYTES,
    MAX_OPEN_POSITIONS,
    PARAMETER_SET_ID,
    RATE_LIMIT_PER_MINUTE,
    TRUSTED_ORIGINS,
    public_config,
)
from ash08.paper_engine import PaperEngine
from ash08.scanner import StockMetrics, demo_metrics, run_scan
from ash08.supabase_store import SupabaseStore
from ash08.universe import build_core, build_discovery, normalize_upstox_row
from ash08.upstox_client import fetch_nse_equity_instruments, fetch_quotes, user_profile

LOG = logging.getLogger("ash08.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_STORE = SupabaseStore(data_dir=str(DATA_DIR))
_ENGINE = PaperEngine(data_dir=str(DATA_DIR), book_value=BOOK_VALUE)
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: Dict[str, deque[float]] = defaultdict(deque)
_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, float] = {}
_PROVIDER_STATE = {"profile_ok": False, "quote_ok": False, "last_quote_at": "", "last_error": ""}
_PROVIDER_LOCK = threading.Lock()
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,39}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def rows_from_payload(payload: Optional[dict]) -> list[dict]:
    return list((payload or {}).get("rows") or [])


def universe_index() -> Dict[str, dict]:
    output: Dict[str, dict] = {}
    for bucket in ("core", "discovery"):
        payload = _STORE.load_universe(bucket) or {}
        for row in rows_from_payload(payload):
            symbol = str(row.get("symbol") or "").upper()
            if symbol and row.get("instrument_key"):
                output[symbol] = row
    return output


def quote_snapshot(symbols: Iterable[str]) -> Dict[str, Any]:
    index = universe_index()
    requested: Dict[str, str] = {}
    missing = []
    for symbol in symbols:
        sym = str(symbol or "").upper()
        row = index.get(sym)
        key = str((row or {}).get("instrument_key") or "")
        if not key:
            missing.append(sym)
        else:
            requested[key] = sym
    if not requested:
        return {"prices": {}, "asof": utc_now(), "missing": missing, "source": "none"}
    try:
        raw = fetch_quotes(list(requested.keys()))
        prices: Dict[str, float] = {}
        for response_key, item in (raw or {}).items():
            if not isinstance(item, dict):
                continue
            embedded_key = str(item.get("instrument_key") or item.get("instrument_token") or "")
            symbol = requested.get(str(response_key)) or requested.get(embedded_key)
            if not symbol:
                symbol = requested.get(str(response_key).replace(":", "|"))
            price = finite(item.get("last_price") or item.get("lastPrice") or (item.get("ohlc") or {}).get("close"))
            if symbol and price is not None and price > 0:
                prices[symbol] = price
        now = utc_now()
        with _PROVIDER_LOCK:
            _PROVIDER_STATE.update({"quote_ok": bool(prices), "last_quote_at": now, "last_error": "" if prices else "no usable prices"})
        return {"prices": prices, "asof": now, "missing": missing + [s for s in requested.values() if s not in prices], "source": "upstox" if prices else "none"}
    except Exception as exc:
        with _PROVIDER_LOCK:
            _PROVIDER_STATE.update({"quote_ok": False, "last_error": str(exc)[:300]})
        LOG.warning("quote request failed: %s", exc)
        return {"prices": {}, "asof": utc_now(), "missing": list(requested.values()) + missing, "source": "none", "error": str(exc)[:300]}


def provider_status(probe_profile: bool = False) -> dict:
    token_set = bool((os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip())
    if probe_profile and token_set:
        try:
            user_profile()
            with _PROVIDER_LOCK:
                _PROVIDER_STATE["profile_ok"] = True
        except Exception as exc:
            with _PROVIDER_LOCK:
                _PROVIDER_STATE["profile_ok"] = False
                _PROVIDER_STATE["last_error"] = str(exc)[:300]
    with _PROVIDER_LOCK:
        state = dict(_PROVIDER_STATE)
    return {"token_set": token_set, **state}


def make_session() -> str:
    token = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        now = time.time()
        for key, expiry in list(_SESSIONS.items()):
            if expiry <= now:
                _SESSIONS.pop(key, None)
        _SESSIONS[token] = now + 8 * 3600
    return token


def valid_session(token: str) -> bool:
    with _SESSION_LOCK:
        expiry = _SESSIONS.get(token, 0)
        if expiry <= time.time():
            _SESSIONS.pop(token, None)
            return False
        return True


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def clean_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ApiError(422, "INVALID_SYMBOL", "Invalid symbol")
    return symbol


def metrics_from_row(row: dict) -> StockMetrics:
    return StockMetrics(
        symbol=str(row.get("symbol") or "").upper(),
        adv20=finite(row.get("adv20")),
        turnover_cr_5d=finite(row.get("turnover_cr_5d")),
        stale_days=finite(row.get("stale_days")),
        mom_6m=finite(row.get("mom_6m")),
        quality_score=finite(row.get("quality_score")),
        max_corr_vs_book=finite(row.get("max_corr_vs_book")),
        segment=str(row.get("segment_tag") or row.get("segment") or ""),
        ltp=finite(row.get("ltp")),
        instrument_key=str(row.get("instrument_key") or ""),
        feature_asof=str(row.get("feature_asof") or ""),
        feature_source=str(row.get("feature_source") or ""),
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ASH08/50L-v1"

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.address_string(), fmt % args)

    def _path(self) -> str:
        path = unquote(urlparse(self.path).path or "/")
        return path[:-1] if len(path) > 1 and path.endswith("/") else path

    def _origin(self) -> str:
        return (self.headers.get("Origin") or "").rstrip("/")

    def _same_origin(self, origin: str) -> bool:
        if not origin:
            return False
        host = self.headers.get("Host") or ""
        return origin in {f"http://{host}", f"https://{host}"} or origin in TRUSTED_ORIGINS

    def _cors_origin(self) -> str:
        origin = self._origin()
        return origin if self._same_origin(origin) else ""

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _rate_allowed(self) -> bool:
        key = self.client_address[0] if self.client_address else "unknown"
        now = time.time()
        with _RATE_LOCK:
            bucket = _RATE_BUCKETS[key]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_PER_MINUTE:
                return False
            bucket.append(now)
            return True

    def _authorize_mutation(self) -> bool:
        if API_TOKEN:
            auth = self.headers.get("Authorization") or ""
            supplied = auth[7:] if auth.startswith("Bearer ") else ""
            return hmac.compare_digest(supplied, API_TOKEN)
        origin = self._origin()
        if not self._same_origin(origin):
            return False
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        cookie_token = cookie.get("ash08_csrf").value if cookie.get("ash08_csrf") else ""
        header_token = self.headers.get("X-CSRF-Token") or ""
        return bool(cookie_token and hmac.compare_digest(cookie_token, header_token) and valid_session(cookie_token))

    def _read_json(self) -> dict:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ApiError(400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "REQUEST_TOO_LARGE", f"Maximum body is {MAX_BODY_BYTES} bytes")
        try:
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "INVALID_JSON", "Malformed JSON body") from exc
        if not isinstance(value, dict):
            raise ApiError(400, "INVALID_JSON_TYPE", "JSON body must be an object")
        return value

    def do_OPTIONS(self):
        origin = self._cors_origin()
        if not origin:
            return self._send_json(403, {"ok": False, "error": {"code": "ORIGIN_DENIED", "message": "Origin not allowed"}})
        self.send_response(204)
        self._security_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-CSRF-Token, Idempotency-Key")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        path = self._path()
        if path in {"/", "/api/health"}:
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "application/json" if path.startswith("/api/") else "text/html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self):
        if not self._rate_allowed():
            return self._send_json(429, {"ok": False, "error": {"code": "RATE_LIMITED", "message": "Too many requests"}})
        path = self._path()
        try:
            if path == "/api/session":
                token = make_session()
                return self._send_json(200, {"ok": True, "csrf_token": token, "auth_mode": "bearer" if API_TOKEN else "same_origin_session"}, cookie=token)
            if path == "/api/health":
                book = _ENGINE.book_payload()
                return self._send_json(200, {
                    "ok": True,
                    "service": "ash08-desk",
                    "release": PARAMETER_SET_ID,
                    "config": public_config(),
                    "paper": {"open_count": book["open_count"], "cash": book["cash"], "equity": book["equity"], "max_open": MAX_OPEN_POSITIONS},
                    "provider": provider_status(probe_profile=True),
                    "store": _STORE.health(),
                    "mutation_auth": "bearer" if API_TOKEN else "same_origin_session",
                })
            if path == "/api/config":
                return self._send_json(200, {"ok": True, "config": public_config()})
            if path == "/api/paper/book":
                return self._send_json(200, {"ok": True, **_ENGINE.book_payload(), "provider": provider_status()})
            if path == "/api/scan/latest":
                return self._send_json(200, _STORE.load_scan() or {"rows": [], "select_count": 0, "watch_count": 0, "reject_count": 0, "unknown_count": 0})
            if path == "/api/universe/core":
                return self._send_json(200, _STORE.load_universe("core") or {"bucket": "core", "status": "MISSING", "count": 0, "rows": []})
            if path == "/api/universe/discovery":
                return self._send_json(200, _STORE.load_universe("discovery") or {"bucket": "discovery", "status": "MISSING", "count": 0, "rows": []})
            return self._serve_static(path)
        except ApiError as exc:
            return self._send_error(exc)
        except Exception as exc:
            LOG.exception("GET %s failed", path)
            return self._send_json(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)[:200]}})

    def do_POST(self):
        if not self._rate_allowed():
            return self._send_json(429, {"ok": False, "error": {"code": "RATE_LIMITED", "message": "Too many requests"}})
        path = self._path()
        try:
            if not self._authorize_mutation():
                raise ApiError(401, "UNAUTHORIZED", "Mutation authorization failed")
            body = self._read_json()
            if path in {"/api/pnl/tick", "/api/live/refresh"}:
                symbols = [position["symbol"] for position in _ENGINE.open_positions()]
                snapshot = quote_snapshot(symbols)
                if not snapshot["prices"] and symbols:
                    raise ApiError(503, "NO_FRESH_QUOTES", snapshot.get("error") or "No fresh Upstox quotes")
                result = _ENGINE.process_marks(snapshot["prices"], snapshot["asof"])
                return self._send_json(200, {"ok": True, "quotes": snapshot, "mark_result": result, "book": _ENGINE.book_payload()})
            if path == "/api/universe/refresh":
                raw = fetch_nse_equity_instruments()
                rows = [normalize_upstox_row(item) for item in raw]
                valid_rows = [row for row in rows if row]
                source = f"upstox-instrument-master:{utc_now()}"
                discovery = build_discovery(valid_rows, source=source)
                core = build_core(valid_rows, source=source)
                _STORE.save_universe("discovery", discovery.to_dict())
                _STORE.save_universe("core", core.to_dict())
                return self._send_json(200, {"ok": True, "discovery": discovery.to_dict(), "core": core.to_dict()})
            if path == "/api/scan/run":
                bucket = str(body.get("bucket") or "core")
                universe = _STORE.load_universe(bucket) or {}
                if not universe.get("rows"):
                    raise ApiError(409, "UNIVERSE_MISSING", f"No {bucket} universe is available")
                if bucket == "core" and universe.get("status") != "READY":
                    raise ApiError(409, "CORE_BLOCKED", "Core universe is not READY; liquidity evidence is incomplete")
                metrics = [metrics_from_row(row) for row in universe["rows"]]
                snapshot = run_scan(metrics, universe_bucket=bucket, require_metrics=True).to_dict()
                _STORE.save_scan(snapshot)
                return self._send_json(200, {"ok": True, **snapshot})
            if path == "/api/demo/run":
                if not ALLOW_DEMO:
                    raise ApiError(404, "DEMO_DISABLED", "Demo mode is disabled")
                snapshot = run_scan(demo_metrics(), universe_bucket="demo", require_metrics=True).to_dict()
                _STORE.save_scan(snapshot)
                return self._send_json(200, {"ok": True, **snapshot, "demo": True})
            if path == "/api/paper/buy":
                return self._paper_buy(body)
            if path == "/api/paper/auto":
                return self._paper_auto(body)
            if path == "/api/positions/close":
                symbol = clean_symbol(body.get("symbol"))
                price = finite(body.get("price"))
                if price is None:
                    quote = quote_snapshot([symbol])
                    price = finite(quote["prices"].get(symbol))
                if price is None:
                    raise ApiError(422, "FRESH_PRICE_REQUIRED", "A fresh executable price is required")
                try:
                    closed = _ENGINE.close_position(symbol, price, qty=body.get("qty"), reason="MANUAL_CLOSE")
                except ValueError as exc:
                    raise ApiError(422, str(exc), str(exc)) from exc
                return self._send_json(200, {"ok": True, "closed": closed, "book": _ENGINE.book_payload()})
            if path == "/api/governor/evaluate":
                result = _ENGINE.apply_governor(
                    damage=as_bool(body.get("damage")),
                    q10=as_bool(body.get("q10")),
                    sell=as_bool(body.get("sell")),
                    any_fii=as_bool(body.get("any_fii")),
                    evidence_complete=as_bool(body.get("evidence_complete")),
                    evidence_fresh=as_bool(body.get("evidence_fresh")),
                    evidence_asof=str(body.get("evidence_asof") or utc_now()),
                )
                return self._send_json(200, {"ok": True, **result})
            raise ApiError(404, "NOT_FOUND", "Endpoint not found")
        except ApiError as exc:
            return self._send_error(exc)
        except Exception as exc:
            LOG.exception("POST %s failed", path)
            return self._send_json(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)[:200]}})

    def _paper_buy(self, body: dict):
        symbol = clean_symbol(body.get("symbol"))
        row = universe_index().get(symbol)
        if not row:
            raise ApiError(422, "UNKNOWN_INSTRUMENT", "Symbol is not present in the validated discovery/core universe")
        try:
            qty = int(body.get("qty") or 0)
        except (TypeError, ValueError) as exc:
            raise ApiError(422, "INVALID_QTY", "Quantity must be an integer") from exc
        if qty <= 0:
            raise ApiError(422, "INVALID_QTY", "Quantity must be positive")
        price = finite(body.get("price"))
        quote = None
        if price is None:
            quote = quote_snapshot([symbol])
            price = finite(quote["prices"].get(symbol))
        if price is None or price <= 0:
            raise ApiError(422, "FRESH_PRICE_REQUIRED", "Pass a finite price or configure a working Upstox quote feed")
        idempotency_key = (self.headers.get("Idempotency-Key") or "").strip()
        if not idempotency_key:
            raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
        order = _ENGINE.place_order(
            symbol=symbol,
            side="BUY",
            order_type=str(body.get("order_type") or "MARKET"),
            qty=qty,
            fill_price=price,
            stop=body.get("stop"),
            target=body.get("target"),
            hold_days=body.get("hold_sessions") or body.get("hold_days"),
            source="manual",
            lot_size=int(row.get("lot_size") or 1),
            tick_size=float(row.get("tick_size") or 0.05),
            idempotency_key=idempotency_key,
            instrument_key=str(row.get("instrument_key") or ""),
        )
        status = 200 if order.get("status") == "FILLED" else 422
        return self._send_json(status, {"ok": order.get("status") == "FILLED", "order": order, "quote": quote, "book": _ENGINE.book_payload()})

    def _paper_auto(self, body: dict):
        scan = _STORE.load_scan() or {}
        rows = [row for row in scan.get("rows") or [] if row.get("decision") == "SELECT"]
        if not rows:
            raise ApiError(409, "NO_SELECT_ROWS", "Latest scan has no SELECT rows")
        scan_id = str(scan.get("snapshot_id") or scan.get("asof") or "")
        snapshot = quote_snapshot([row.get("symbol") for row in rows])
        if not snapshot["prices"]:
            raise ApiError(503, "NO_FRESH_QUOTES", snapshot.get("error") or "No fresh prices for SELECT rows")
        index = universe_index()
        enriched = []
        for row in rows:
            item = dict(row)
            instrument = index.get(str(row.get("symbol") or "").upper()) or {}
            item.update({
                "instrument_key": instrument.get("instrument_key") or row.get("instrument_key"),
                "lot_size": instrument.get("lot_size") or 1,
                "tick_size": instrument.get("tick_size") or 0.05,
            })
            enriched.append(item)
        result = _ENGINE.auto_buy_selects(enriched, snapshot["prices"], scan_id=scan_id)
        return self._send_json(200, {"ok": True, "operation_id": f"auto:{scan_id}", **result, "book": _ENGINE.book_payload(), "quotes": snapshot})

    def _serve_static(self, path: str):
        requested = "ASH08_Desk_Dashboard.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (DESK / requested).resolve()
        try:
            candidate.relative_to(DESK.resolve())
        except ValueError:
            raise ApiError(403, "FORBIDDEN", "Static path denied")
        if not candidate.is_file():
            raise ApiError(404, "NOT_FOUND", "File not found")
        data = candidate.read_bytes()
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, error: ApiError):
        return self._send_json(error.status, {"ok": False, "error": {"code": error.code, "message": error.message}})

    def _send_json(self, status: int, payload: dict, cookie: str = ""):
        raw = json.dumps(payload, default=str, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
            self.send_header("Set-Cookie", f"ash08_csrf={cookie}; Path=/; SameSite=Strict{secure}")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    DESK.mkdir(parents=True, exist_ok=True)
    LOG.info("Starting ASH08 %s on 0.0.0.0:%s; book=%.0f", PARAMETER_SET_ID, PORT, BOOK_VALUE)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
