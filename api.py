"""ASH08 API. Start: python api.py
Upstox ONLY for LTP. Token must work from this host.
"""
from __future__ import annotations
import hmac, json, logging, mimetypes, os, re, sys, time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import unquote, urlparse

from ash08 import config as CONFIG
from ash08.upstox_client import is_exact_nse_equity_key

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR = CONFIG.DATA_DIR
DEMO_ENABLED = CONFIG.ALLOW_DEMO
SYMBOL_RE = re.compile(r"^[A-Z0-9&.-]{1,30}$")
_ENGINE_LOCK = RLock()
_INSTRUMENT_LOCK = RLock()
_INSTRUMENT_KEYS = {}
_INSTRUMENT_MASTER_LOADED = False
_RATE_LOCK = RLock()
_REQUEST_TIMES = defaultdict(deque)
REF_LTP = {
    "TCS": 3840.0, "HDFCBANK": 1690.0, "RELIANCE": 2950.0, "INFY": 1850.0,
    "ICICIBANK": 1180.0, "SBIN": 820.0, "ITC": 450.0, "MTARTECH": 1850.0,
    "COCHINSHIP": 1450.0, "HAL": 4200.0, "BEL": 280.0, "LT": 3600.0,
    "HCLTECH": 1650.0, "WIPRO": 480.0, "AXISBANK": 1100.0, "KOTAKBANK": 1750.0,
    "TATAMOTORS": 980.0, "MARUTI": 12400.0, "BAJFINANCE": 7100.0, "POWERGRID": 300.0,
}

def load_mods():
    m = {}
    for name, imp in [
        ("store", ("ash08.supabase_store", "SupabaseStore")),
        ("Metrics", ("ash08.scanner", "StockMetrics")),
        ("run_scan", ("ash08.scanner", "run_scan")),
        ("PaperEngine", ("ash08.paper_engine", "PaperEngine")),
        ("fetch_quotes", ("ash08.upstox_client", "fetch_quotes")),
        ("profile", ("ash08.upstox_client", "user_profile")),
        ("fetch_nse", ("ash08.upstox_client", "fetch_nse_equity_instruments")),
        ("Row", ("ash08.universe", "InstrumentRow")),
        ("Uni", ("ash08.universe", "UniverseManager")),
    ]:
        try:
            mod = __import__(imp[0], fromlist=[imp[1]])
            m[name] = getattr(mod, imp[1])
        except Exception as e:
            LOG.error("%s: %s", name, e)
    return m

MODS = load_mods()
LOG.info("modules: %s", sorted(MODS.keys()))
try:
    from ash08.core_seed import CORE_SYMBOLS, CORE_COUNT
except Exception:
    CORE_SYMBOLS = list(REF_LTP.keys()); CORE_COUNT = len(CORE_SYMBOLS)

_ENGINE = None
def get_engine():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        if "PaperEngine" not in MODS:
            return None
        _ENGINE = MODS["PaperEngine"](
            data_dir=str(DATA_DIR),
            book_value=CONFIG.BOOK_VALUE,
        )
        return _ENGINE


def rate_limit_allows(client_id):
    now = time.monotonic()
    cutoff = now - 60.0
    with _RATE_LOCK:
        history = _REQUEST_TIMES[str(client_id)]
        while history and history[0] < cutoff:
            history.popleft()
        if len(history) >= CONFIG.RATE_LIMIT_PER_MINUTE:
            return False
        history.append(now)
        return True

def upstox_status():
    tok = (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()
    info = {"token_set": bool(tok), "connected": False, "detail": "no token" if not tok else "token present"}
    if not tok:
        return info
    if "profile" not in MODS:
        info["detail"] = "token set; upstox module missing"
        return info
    try:
        MODS["profile"]()
        info["connected"] = True
        info["detail"] = "profile ok"
    except Exception as e:
        info["detail"] = f"token set but API failed: {e}"
    return info

def quotes_for_symbols(symbols, prefer_live=True):
    """Upstox ONLY. No other source, no invented prices."""
    out = {}
    toks = [str(s).strip().upper() for s in (symbols or []) if s]
    toks = list(dict.fromkeys(toks))
    if not toks:
        return {}
    if "fetch_quotes" not in MODS:
        LOG.warning("upstox quotes: fetch_quotes module missing")
        return {}
    if not (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip():
        LOG.warning("upstox quotes: UPSTOX_ACCESS_TOKEN missing")
        return {}
    key_map = instrument_keys_for_symbols(toks)
    keys = [key_map[symbol] for symbol in toks if symbol in key_map]
    missing = [symbol for symbol in toks if symbol not in key_map]
    if missing:
        LOG.warning("upstox quotes: exact instrument keys missing for %s", missing[:10])
    if not keys:
        return {}
    reverse_keys = {key.upper(): symbol for symbol, key in key_map.items()}
    try:
        raw = MODS["fetch_quotes"](keys)
        for k, v in (raw or {}).items():
            if not isinstance(v, dict):
                continue
            normalized_key = str(k or "").strip().upper().replace(":", "|", 1)
            value_key = str(v.get("instrument_key") or v.get("instrument_token") or "").strip().upper()
            sym = reverse_keys.get(normalized_key) or reverse_keys.get(value_key)
            if not sym:
                continue
            lp = v.get("last_price") or v.get("lastPrice")
            if lp is None and isinstance(v.get("ohlc"), dict):
                lp = v["ohlc"].get("close")
            if lp is not None:
                try:
                    out[str(sym).upper()] = float(lp)
                except Exception:
                    pass
    except Exception as e:
        LOG.warning("upstox quotes: %s", e)
    return out


def instrument_keys_for_symbols(symbols):
    global _INSTRUMENT_MASTER_LOADED
    requested = {str(symbol or "").strip().upper() for symbol in symbols if symbol}
    if not requested:
        return {}
    with _INSTRUMENT_LOCK:
        if "store" in MODS:
            try:
                store = MODS["store"]()
                for bucket in ("core", "discovery"):
                    snapshot = store.load_universe(bucket) or {}
                    for row in snapshot.get("rows") or []:
                        symbol = str(row.get("symbol") or "").strip().upper()
                        key = str(row.get("instrument_key") or "").strip().upper()
                        if symbol and is_exact_nse_equity_key(key):
                            _INSTRUMENT_KEYS[symbol] = key
            except Exception as error:
                LOG.warning("instrument key store lookup: %s", error)
        unresolved = requested.difference(_INSTRUMENT_KEYS)
        if unresolved and not _INSTRUMENT_MASTER_LOADED and "fetch_nse" in MODS:
            try:
                for row in MODS["fetch_nse"]() or []:
                    symbol = str(row.get("symbol") or row.get("trading_symbol") or "").strip().upper()
                    key = str(row.get("instrument_key") or "").strip().upper()
                    if symbol and is_exact_nse_equity_key(key):
                        _INSTRUMENT_KEYS[symbol] = key
            except Exception as error:
                LOG.warning("instrument master lookup: %s", error)
            finally:
                _INSTRUMENT_MASTER_LOADED = True
        return {
            symbol: _INSTRUMENT_KEYS[symbol]
            for symbol in requested
            if symbol in _INSTRUMENT_KEYS
        }

def auto_buy_from_scan(scan_dict):
    eng = get_engine()
    if not eng or not hasattr(eng, "auto_buy_selects"):
        return None
    if scan_dict.get("synthetic") or str(scan_dict.get("source") or "").lower().startswith("synthetic"):
        return {
            "bought": 0,
            "skipped": len(scan_dict.get("rows") or []),
            "open_count": len(eng.open_symbols()),
            "blocked": "SYNTHETIC_SCAN",
        }
    selects = [r for r in (scan_dict.get("rows") or []) if str(r.get("decision") or "").upper() == "SELECT"]
    if not selects:
        return {"bought": 0, "skipped": 0, "open_count": len(eng.open_symbols())}
    price_map = quotes_for_symbols([r.get("symbol") for r in selects])
    for r in selects:
        sym = str(r.get("symbol") or "").upper()
        if r.get("ltp") and sym not in price_map:
            try:
                price_map[sym] = float(r["ltp"])
            except Exception:
                pass
    try:
        return eng.auto_buy_selects(selects, price_map=price_map)
    except Exception as e:
        LOG.exception("auto_buy")
        return {"error": str(e)}

def seed_demo_local():
    """Create visibly synthetic data for an explicitly enabled local demo.

    Demo generation never deletes the paper ledger, and synthetic rows are
    blocked from automatic order creation by ``auto_buy_from_scan``.
    """
    if not DEMO_ENABLED:
        return {
            "ok": False,
            "error": "Demo generation disabled; set ASH08_ENABLE_DEMO=true only in an isolated local demo.",
            "synthetic": True,
        }
    if "store" not in MODS or "Metrics" not in MODS or "run_scan" not in MODS:
        return {"ok": False, "error": "modules missing"}
    from datetime import datetime, timezone
    store = MODS["store"]()
    symbols = list(CORE_SYMBOLS)
    rows = [{"symbol": s, "name": s, "instrument_key": ""} for s in symbols]
    store.save_universe("core", {
        "bucket": "core",
        "asof": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "synthetic_demo", "synthetic": True,
        "count": len(symbols), "symbols": symbols, "rows": rows,
        "notes": ["Synthetic local demo data; never valid for live or paper decisions."],
    })
    batch = symbols[:80]
    live_map = quotes_for_symbols(batch)
    metrics = []
    for i, s in enumerate(symbols[:400]):
        mom = 0.14 - (i % 9) * 0.015
        qual = 78 - (i % 11) * 2
        ltp = live_map.get(s) or REF_LTP.get(s) or (100.0 + (i % 50) * 3)
        metrics.append(MODS["Metrics"](symbol=s, adv20=350000, turnover_cr_5d=12, stale_days=0,
                                       mom_6m=mom, quality_score=qual, ltp=ltp))
    snap = MODS["run_scan"](metrics, universe_bucket="core")
    scan_dict = snap.to_dict()
    scan_dict["source"] = "synthetic_demo"
    scan_dict["synthetic"] = True
    store.save_scan(scan_dict)
    auto = auto_buy_from_scan(scan_dict)
    return {"ok": True, "synthetic": True, "core_count": len(symbols), "select": snap.select_count,
            "watch": snap.watch_count, "reject": snap.reject_count, "auto_paper": auto,
            "live_quote_count": len(live_map), "ltp_source": ("upstox" if live_map else "upstox_failed"),
            "upstox": upstox_status()}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

    def _origin_allowed(self):
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if not origin:
            return True
        if origin in CONFIG.TRUSTED_ORIGINS:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        try:
            return urlparse(origin).netloc.lower() == host
        except Exception:
            return False

    def _authorized(self):
        if not CONFIG.API_TOKEN:
            return False
        authorization = (self.headers.get("Authorization") or "").strip()
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        supplied = bearer or (self.headers.get("X-ASH08-Token") or "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, CONFIG.API_TOKEN)

    def _reject_mutation(self, code, error):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= 1024 * 1024:
            self.rfile.read(length)
        self.close_connection = True
        self.json(code, {"ok": False, "error": error})

    def _require_mutation_access(self):
        if not self._origin_allowed():
            self._reject_mutation(403, "origin not allowed")
            return False
        if not CONFIG.API_TOKEN:
            self._reject_mutation(503, "ASH08_API_TOKEN is not configured")
            return False
        if not self._authorized():
            self._reject_mutation(401, "valid API token required")
            return False
        client_id = self.client_address[0] if self.client_address else "unknown"
        if not rate_limit_allows(client_id):
            self._reject_mutation(429, "mutation rate limit exceeded")
            return False
        return True

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self.json(400, {"ok": False, "error": "invalid Content-Length"})
            return None
        if length < 0 or length > CONFIG.MAX_BODY_BYTES:
            if 0 < length <= 1024 * 1024:
                self.rfile.read(length)
            self.close_connection = True
            self.json(413, {"ok": False, "error": "request body too large"})
            return None
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if length and content_type != "application/json":
            self.json(415, {"ok": False, "error": "Content-Type must be application/json"})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.json(400, {"ok": False, "error": "invalid JSON body"})
            return None
        if not isinstance(body, dict):
            self.json(400, {"ok": False, "error": "JSON body must be an object"})
            return None
        return body

    def do_OPTIONS(self):
        if not self._origin_allowed():
            return self.json(403, {"ok": False, "error": "origin not allowed"})
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-ASH08-Token")
        self.send_header("Content-Length", "0"); self.end_headers()

    def do_POST(self):
        path = unquote(urlparse(self.path).path or "/")
        if path.endswith("/"): path = path[:-1]
        if not self._require_mutation_access():
            return
        body = self._read_json_body()
        if body is None:
            return
        if path == "/api/paper/buy":
            return self.api_paper_buy(body)
        if path == "/api/pnl/tick":
            return self.api_pnl_tick()
        if path == "/api/paper/auto":
            return self.api_paper_auto()
        if path == "/api/demo/run":
            result = seed_demo_local()
            return self.json(200 if result.get("ok") else 403, result)
        if path == "/api/scan/run":
            return self.json(409, {
                "ok": False,
                "error": "No live scanner input was supplied; refusing to fabricate scanner metrics.",
            })
        return self.json(404, {"ok": False, "error": "not found"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if not path.startswith("/"): path = "/" + path
        if len(path) > 1 and path.endswith("/"): path = path[:-1]
        mutation_paths = {
            "/api/demo/run", "/api/scan/run", "/api/paper/auto",
            "/api/paper/buy", "/api/pnl/tick",
        }
        if path in mutation_paths:
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/health":
            eng = get_engine()
            open_n = sum(1 for p in (eng.positions if eng else []) if p.get("status") == "OPEN")
            store_info = {}
            if "store" in MODS:
                try: store_info = MODS["store"]().health()
                except Exception as e: store_info = {"error": str(e)}
            ux = upstox_status()
            return self.json(200, {
                "ok": True, "service": "ash08-desk", "modules": sorted(MODS.keys()),
                "core_seed_count": CORE_COUNT, "paper_open": open_n, "store": store_info,
                "upstox": ux, "upstox_token_set": ux.get("token_set"), "upstox_connected": ux.get("connected"),
                "mutation_auth_configured": bool(CONFIG.API_TOKEN),
                "trade_plan": {
                    "stop_pct": CONFIG.STOP_PCT,
                    "target_pct": CONFIG.TARGET_PCT,
                    "max_hold_days": CONFIG.MAX_HOLD_SESSIONS,
                    "max_open": CONFIG.MAX_OPEN_POSITIONS,
                },
                "note": "Upstox ONLY for LTP. Token must work from this host.",
            })
        if path == "/api/config":
            return self.json(200, {"ok": True, "config": CONFIG.public_config()})
        if path == "/api/universe/core":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_universe("core")
            return self.json(200, data or {
                "count": 0, "symbols": [], "rows": [], "data_status": "EMPTY",
                "notes": ["No universe has been loaded."],
            })
        if path == "/api/scan/latest":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_scan()
            return self.json(200, data or {"rows": [], "data_status": "EMPTY"})
        if path == "/api/paper/book":
            return self.api_paper_book()
        return self.serve_static(path)

    def api_paper_auto(self):
        if "store" not in MODS:
            return self.json(500, {"ok": False, "error": "store missing"})
        scan = MODS["store"]().load_scan() or {}
        if not scan.get("rows"):
            return self.json(409, {
                "ok": False,
                "error": "No scanner rows are available; refusing to seed synthetic candidates.",
            })
        result = auto_buy_from_scan(scan)
        if isinstance(result, dict) and result.get("blocked") == "SYNTHETIC_SCAN":
            return self.json(409, {"ok": False, "auto_paper": result})
        return self.json(200, {"ok": True, "auto_paper": result, "upstox": upstox_status()})

    def api_paper_book(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        live = quotes_for_symbols(opens_sym)
        ux = upstox_status()
        if live:
            ltp_source = "upstox"
        else:
            ltp_source = "upstox_failed"
        if hasattr(eng, "book_payload"):
            book = eng.book_payload(live_prices=live)
        else:
            if hasattr(eng, "mark_to_market"):
                eng.mark_to_market(live)
            book = {
                "open": [p for p in eng.positions if p.get("status") == "OPEN"],
                "closed": [p for p in eng.positions if p.get("status") != "OPEN"][-20:],
                "orders": list(reversed(eng.orders[-50:])),
                "open_count": sum(1 for p in eng.positions if p.get("status") == "OPEN"),
                "order_count": len(eng.orders),
                "unrealized_pnl": 0, "realized_pnl": 0, "total_pnl": 0,
            }
        gov = eng.governor.to_dict() if hasattr(eng.governor, "to_dict") else {
            "level": getattr(eng.governor, "level", "L0"),
            "exposure_pct": getattr(eng.governor, "exposure_pct", 100),
        }
        plan = {
            "stop_pct": CONFIG.STOP_PCT,
            "target_pct": CONFIG.TARGET_PCT,
            "max_hold_days": CONFIG.MAX_HOLD_SESSIONS,
            "max_open": CONFIG.MAX_OPEN_POSITIONS,
            "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            "size": f"{CONFIG.MAX_NAME_PCT}% book x governor exposure",
        }
        return self.json(200, {
            "ok": True, "governor": gov, "plan": plan,
            "orders": book.get("orders") or [], "positions": eng.positions,
            "open": book.get("open") or [], "closed": book.get("closed") or [],
            "open_count": book.get("open_count") or 0, "order_count": book.get("order_count") or 0,
            "unrealized_pnl": book.get("unrealized_pnl") or 0,
            "realized_pnl": book.get("realized_pnl") or 0,
            "total_pnl": book.get("total_pnl") or 0,
            "ltp_source": ltp_source,
            "live_quote_count": len(live),
            "upstox": ux,
            "note": "Upstox ONLY. If ltp_source=upstox_failed, Cloudflare blocked Render IP (token can still be valid on your PC).",
        })

    def api_pnl_tick(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        live = quotes_for_symbols(opens_sym)
        if hasattr(eng, "book_payload"):
            book = eng.book_payload(live_prices=live)
        else:
            if hasattr(eng, "mark_to_market"):
                eng.mark_to_market(live)
            book = {"unrealized_pnl": 0, "realized_pnl": 0, "total_pnl": 0, "open": [], "open_count": 0}
        ux = upstox_status()
        src = "upstox" if live else "upstox_failed"
        return self.json(200, {
            "ok": True, "ltp_source": src, "live_quote_count": len(live),
            "unrealized_pnl": book.get("unrealized_pnl") or 0,
            "realized_pnl": book.get("realized_pnl") or 0,
            "total_pnl": book.get("total_pnl") or 0,
            "open_count": book.get("open_count") or 0,
            "open": book.get("open") or [], "upstox": ux,
        })

    def api_paper_buy(self, body):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            return self.json(400, {"ok": False, "error": "symbol required"})
        if not SYMBOL_RE.fullmatch(symbol):
            return self.json(400, {"ok": False, "error": "invalid NSE symbol format"})
        try:
            qty = int(body.get("qty") or 0)
        except (TypeError, ValueError):
            return self.json(400, {"ok": False, "error": "qty must be a positive integer"})
        if qty <= 0:
            return self.json(400, {"ok": False, "error": "qty must be a positive integer"})
        def _f(v, d=None):
            if v is None or v == "": return d
            try: return float(v)
            except Exception: return d
        price = _f(body.get("price")); stop = _f(body.get("stop")); target = _f(body.get("target"))
        if not price or price <= 0:
            live = quotes_for_symbols([symbol])
            if not live.get(symbol):
                return self.json(400, {"ok": False, "error": "Upstox quote failed for " + symbol, "upstox": upstox_status()})
            price = live[symbol]
        try:
            idempotency_key = str(
                body.get("idempotency_key")
                or self.headers.get("Idempotency-Key")
                or ""
            ).strip() or None
            order = eng.place_order(symbol=symbol, side="BUY", order_type="MARKET",
                                    qty=qty, fill_price=price, stop=stop, target=target, source="manual",
                                    idempotency_key=idempotency_key)
            if hasattr(eng, "book_payload"):
                eng.book_payload(live_prices=quotes_for_symbols([symbol]))
            elif hasattr(eng, "mark_to_market"):
                eng.mark_to_market({symbol: price})
        except Exception as e:
            LOG.exception("buy")
            return self.json(500, {"ok": False, "error": str(e)})
        opens = [p for p in eng.positions if p.get("status") == "OPEN"]
        filled = order.get("status") == "FILLED"
        return self.json(200 if filled else 409, {
            "ok": filled, "order": order, "open_count": len(opens), "positions": opens,
            "error": None if filled else order.get("reason") or "order rejected",
            "message": f"PAPER {order.get('status')}: {symbol} x {order.get('sized_qty') or order.get('qty')} @ {price} | stop={order.get('stop')} target={order.get('target')} hold={order.get('hold_days')}d",
        })

    def serve_static(self, path):
        candidate = DESK / "ASH08_Desk_Dashboard.html" if path in ("/", "") else (DESK / path.lstrip("/")).resolve()
        if path not in ("/", ""):
            try: candidate.relative_to(DESK.resolve())
            except ValueError: return self.json(403, {"ok": False, "error": "forbidden"})
        if not candidate.is_file():
            alt = DESK / Path(path.lstrip("/")).name
            if alt.is_file(): candidate = alt
            else:
                self.send_error(404); return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_cors_headers(self):
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def json(self, code, obj):
        raw = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(raw)

def initialize_runtime():
    DESK.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return get_engine()

def main():
    initialize_runtime()
    LOG.info("ASH08 on 0.0.0.0:%s paper=%s core=%s upstox=%s",
             PORT, "PaperEngine" in MODS, CORE_COUNT, upstox_status().get("detail"))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
