"""ASH08 API restored baseline. Start: python api.py
Paper P&L works without Upstox (deterministic paper marks).
Upstox is optional: live LTP only when Cloudflare allows the host.
"""
from __future__ import annotations
import json, logging, mimetypes, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ash08.config import CORE_MAX, CORE_MIN, public_config 
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR = Path("ash08_data")
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
    if _ENGINE is not None:
        return _ENGINE
    if "PaperEngine" not in MODS:
        return None
    eng = MODS["PaperEngine"](data_dir=str(DATA_DIR))
    p = DATA_DIR / "paper_state.json"
    if p.exists():
        try:
            st = json.loads(p.read_text())
            eng.orders = st.get("orders") or []
            eng.positions = st.get("positions") or []
            g = st.get("governor") or {}
            if g and hasattr(eng, "governor"):
                eng.governor.level = g.get("level", eng.governor.level)
                eng.governor.exposure_pct = float(g.get("exposure_pct", 100))
        except Exception as e:
            LOG.warning("paper load: %s", e)
    _ENGINE = eng
    return eng

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

def quotes_for_symbols(symbols):
    if "fetch_quotes" not in MODS or not (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip():
        return {}
    try:
        from ash08.upstox_client import ltp_by_symbol
        return ltp_by_symbol([str(s).upper() for s in (symbols or []) if s], DATA_DIR)
    except Exception as e:
        LOG.warning("quotes: %s", e)
        return {}

def auto_buy_from_scan(scan_dict):
    eng = get_engine()
    if not eng or not hasattr(eng, "auto_buy_selects"):
        return None
    selects = [r for r in (scan_dict.get("rows") or []) if str(r.get("decision") or "").upper() == "SELECT"]
    if not selects:
        return {"bought": 0, "skipped": 0, "open_count": len(eng.open_symbols())}
    live = quotes_for_symbols([r.get("symbol") for r in selects])
    priced = []
    skipped_px = []
    for r in selects:
        sym = str(r.get("symbol") or "").upper()
        px = live.get(sym)
        if px is None:
            skipped_px.append(sym)
            continue
        r = dict(r)
        r["ltp"] = px
        priced.append(r)
    try:
        result = eng.auto_buy_selects(priced, price_map=live)
    except Exception as e:
        LOG.exception("auto_buy")
        return {"error": str(e)}
    result["skipped_no_upstox_ltp"] = skipped_px
    return result


def _mgr():
    if "Uni" not in MODS:
        return None
    return MODS["Uni"](data_dir=str(DATA_DIR))


def core_symbols_live():
    """Active Core membership only (150–250). Not the 1401 seed pool."""
    mgr = _mgr()
    data = None
    if mgr:
        data = mgr.load_core()
    if not data and "store" in MODS:
        try:
            data = MODS["store"]().load_universe("core")
        except Exception:
            data = None
    symbols = [str(s).upper() for s in (data or {}).get("symbols") or [] if s]
    if CORE_MIN <= len(symbols) <= CORE_MAX:
        return symbols
    return []


def ensure_core(force=False):
    mgr = _mgr()
    if not mgr:
        return {"ok": False, "error": "UniverseManager missing", "count": 0, "symbols": []}
    snap, rebuilt = mgr.ensure_core(CORE_SYMBOLS, force=force)
    if "store" in MODS:
        try:
            MODS["store"]().save_universe("core", snap)
        except Exception as e:
            LOG.warning("save_universe: %s", e)
    return {
        "ok": True,
        "rebuilt": rebuilt,
        "count": snap.get("count") or len(snap.get("symbols") or []),
        "symbols": snap.get("symbols") or [],
        "asof": snap.get("asof"),
        "notes": snap.get("notes") or [],
        "policy_id": snap.get("policy_id"),
        "source": snap.get("source"),
        "bucket": "core",
        "rows": snap.get("rows") or [],
    }


def scan_core(auto_buy=False):
    """Scan Core with G2 metrics. No synthetic mom/quality/LTP."""
    if "Metrics" not in MODS or "run_scan" not in MODS or "store" not in MODS:
        return {"ok": False, "error": "modules missing"}
    core = ensure_core(force=False)
    symbols = core.get("symbols") or []
    if not symbols:
        return {"ok": False, "error": "core empty", "core": core}
    from ash08.config import METRICS_POLICY_ID
    from ash08.metrics import build_metrics_for_core
    eng = get_engine()
    opens = list(eng.open_symbols()) if eng else []
    live = quotes_for_symbols(symbols[:50] + opens)
    metrics = build_metrics_for_core(
        symbols, str(DATA_DIR), quotes=live, open_symbols=opens,
    )
    snap = MODS["run_scan"](metrics, universe_bucket="core")
    scan_dict = snap.to_dict()
    scan_dict["notes"] = list(scan_dict.get("notes") or []) + [
        f"metrics_policy={METRICS_POLICY_ID}",
        "no_synthetic_metrics",
        f"core_count={len(symbols)}",
        f"ltp_live={len(live)}",
    ]
    store = MODS["store"]()
    store.save_scan(scan_dict)
    auto = auto_buy_from_scan(scan_dict) if auto_buy else None
    return {
        "ok": True,
        "core_count": len(symbols),
        "select": snap.select_count,
        "watch": snap.watch_count,
        "reject": snap.reject_count,
        "unknown": getattr(snap, "unknown_count", 0),
        "auto_paper": auto,
        "upstox": upstox_status(),
        "notes": scan_dict["notes"],
        "ltp_source": "upstox" if live else "upstox_failed",
    }


def refresh_metrics(force=False):
    from ash08.history import HistoryStore
    from ash08.upstox_client import load_eq_keymap
    symbols = core_symbols_live() or list(CORE_SYMBOLS[:CORE_MAX])
    store = HistoryStore(DATA_DIR)
    try:
        km = load_eq_keymap(DATA_DIR)
    except Exception as e:
        LOG.warning("keymap: %s", e)
        km = {}
    results = store.refresh_many(symbols, instrument_keys=km, force=force)
    ok_n = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
    return {
        "ok": True,
        "attempted": len(results),
        "fetched": ok_n,
        "keymap": len(km),
        "results": results[:40],
        "upstox": upstox_status(),
    }


def seed_demo_local():
    """Back-compat name for /api/demo/run — scan Core, do not dump 1401."""
    ensure_core(force=False)
    return scan_core(auto_buy=False)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0"); self.end_headers()
    def do_POST(self):
        path = unquote(urlparse(self.path).path or "/")
        if path.endswith("/"): path = path[:-1]
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() if n else "{}")
        except Exception:
            body = {}
        if path == "/api/paper/buy":
            return self.api_paper_buy(body)
        if path == "/api/pnl/tick":
            return self.api_pnl_tick()
        return self.json(404, {"ok": False, "error": "not found"})
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if not path.startswith("/"): path = "/" + path
        if len(path) > 1 and path.endswith("/"): path = path[:-1]
        qs = parse_qs(parsed.query or "")
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
                "core_seed_count": CORE_COUNT, "core_count": len(core_symbols_live()),
                "paper_open": open_n, "store": store_info,
                "upstox": ux, "upstox_token_set": ux.get("token_set"), "upstox_connected": ux.get("connected"),
                "trade_plan": public_config()["trade_plan"],
                "contract": public_config(),
                "universe": (_mgr().status() if _mgr() else {}),
                "note": "G4 index tiles: Upstox quote or failed. Never a silent dash.",
            })
        if path == "/api/universe/core":
            core = ensure_core(force=False)
            return self.json(200, core)
        if path == "/api/universe/refresh":
            core = ensure_core(force=True)
            return self.json(200, core)
        if path == "/api/universe/discovery":
            mgr = _mgr()
            if not mgr:
                return self.json(500, {"ok": False, "error": "UniverseManager missing"})
            snap = mgr.rebuild_discovery(CORE_SYMBOLS)
            return self.json(200, {"ok": True, "auto_buy": False, **snap})
        if path == "/api/metrics/refresh":
            force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
            return self.json(200, refresh_metrics(force=force))
        if path in ("/api/indices", "/api/index"):
            from ash08.indices import fetch_index_tiles
            ux = upstox_status()
            if "fetch_quotes" not in MODS:
                payload = fetch_index_tiles(lambda _keys: (_ for _ in ()).throw(RuntimeError("upstox module missing")), False)
                payload["detail"] = "upstox module missing"
                return self.json(200, payload)
            payload = fetch_index_tiles(MODS["fetch_quotes"], bool(ux.get("token_set")))
            payload["upstox"] = ux
            return self.json(200, payload)
        if path == "/api/segments":
            from ash08.segments import segment_snapshot
            core = core_symbols_live()
            scan = {}
            if "store" in MODS:
                try:
                    scan = MODS["store"]().load_scan() or {}
                except Exception:
                    scan = {}
            payload = segment_snapshot(core, scan.get("rows") or [])
            payload["asof"] = scan.get("asof")
            return self.json(200, payload)
        if path == "/api/piano" or path.startswith("/api/piano/"):
            from ash08.piano import piano_from_scan, piano_summary
            scan = {}
            if "store" in MODS:
                try:
                    scan = MODS["store"]().load_scan() or {}
                except Exception:
                    scan = {}
            gov = None
            eng = get_engine()
            if eng and hasattr(eng, "governor") and hasattr(eng.governor, "to_dict"):
                gov = eng.governor.to_dict()
            extra = path[len("/api/piano/"):] if path.startswith("/api/piano/") else ""
            param = extra or (qs.get("param") or [""])[0]
            if not param:
                return self.json(200, piano_summary(scan))
            return self.json(200, piano_from_scan(scan, param, governor=gov))
        if path == "/api/scan/latest":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_scan()
            if not data or not data.get("rows"):
                scan_core(auto_buy=False)
                data = MODS["store"]().load_scan()
            return self.json(200, data or {"rows": []})
        if path in ("/api/scan/run", "/api/demo/run"):
            return self.json(200, scan_core(auto_buy=False))
        if path == "/api/paper/book":
            return self.api_paper_book()
        if path == "/api/pnl/tick":
            return self.api_pnl_tick()
        if path == "/api/paper/buy":
            body = {
                "symbol": (qs.get("symbol") or [""])[0],
                "qty": (qs.get("qty") or ["1"])[0],
                "price": (qs.get("price") or [""])[0],
                "stop": (qs.get("stop") or [""])[0],
                "target": (qs.get("target") or [""])[0],
            }
            return self.api_paper_buy(body)
        if path == "/api/paper/auto":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            scan = MODS["store"]().load_scan() or {}
            if not scan.get("rows"):
                scan_core(auto_buy=False)
                scan = MODS["store"]().load_scan() or {}
            return self.json(200, {"ok": True, "auto_paper": auto_buy_from_scan(scan), "upstox": upstox_status()})
        return self.serve_static(path)

    def api_paper_book(self):
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
        cfg = public_config()
        plan = {
            **cfg["trade_plan"],
            "exits": ["STOP_HIT", "TARGET_HIT", "MAX_HOLD", "GOVERNOR_CUT", "ROTATION"],
            "size": f"{cfg['max_name_pct']}% book x governor exposure",
        }
        ltp_source = "upstox" if live else "paper_marks"
        return self.json(200, {
            "ok": True, "governor": gov, "plan": plan,
            "orders": book.get("orders") or [], "positions": eng.positions,
            "open": book.get("open") or [], "closed": book.get("closed") or [],
            "open_count": book.get("open_count") or 0, "order_count": book.get("order_count") or 0,
            "unrealized_pnl": book.get("unrealized_pnl") or 0,
            "realized_pnl": book.get("realized_pnl") or 0,
            "total_pnl": book.get("total_pnl") or 0,
            "ltp_source": ltp_source,
            "upstox": upstox_status(),
            "note": "Orders FILLED = buy history. Open = live positions. P&L works without Upstox (paper_marks).",
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
        return self.json(200, {
            "ok": True,
            "ltp_source": "upstox" if live else "paper_marks",
            "unrealized_pnl": book.get("unrealized_pnl") or 0,
            "realized_pnl": book.get("realized_pnl") or 0,
            "total_pnl": book.get("total_pnl") or 0,
            "open_count": book.get("open_count") or 0,
            "open": book.get("open") or [],
            "upstox": upstox_status(),
        })

    def api_paper_buy(self, body):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            return self.json(400, {"ok": False, "error": "symbol required"})
        try:
            qty = max(1, int(float(body.get("qty") or 50)))
        except Exception:
            qty = 50
        def _f(v, d=None):
            if v is None or v == "":
                return d
            try:
                return float(v)
            except Exception:
                return d
        price = _f(body.get("price")); stop = _f(body.get("stop")); target = _f(body.get("target"))
        if not price or price <= 0:
            live = quotes_for_symbols([symbol])
            price = live.get(symbol) or REF_LTP.get(symbol, 100.0)
        try:
            order = eng.place_order(symbol=symbol, side="BUY", order_type="MARKET",
                                    qty=qty, fill_price=price, stop=stop, target=target, source="manual")
            if hasattr(eng, "book_payload"):
                eng.book_payload(live_prices=quotes_for_symbols([symbol]))
            elif hasattr(eng, "mark_to_market"):
                eng.mark_to_market({symbol: price})
        except Exception as e:
            LOG.exception("buy")
            return self.json(500, {"ok": False, "error": str(e)})
        opens = [p for p in eng.positions if p.get("status") == "OPEN"]
        return self.json(200, {
            "ok": True, "order": order, "open_count": len(opens), "positions": opens,
            "message": f"PAPER {order.get('status')}: {symbol} x {order.get('sized_qty') or order.get('qty')} @ {price} | stop={order.get('stop')} target={order.get('target')} hold={order.get('hold_days')}d",
        })

    def serve_static(self, path):
        candidate = DESK / "ASH08_Desk_Dashboard.html" if path in ("/", "") else (DESK / path.lstrip("/")).resolve()
        if path not in ("/", ""):
            try:
                candidate.relative_to(DESK.resolve())
            except ValueError:
                return self.json(403, {"ok": False, "error": "forbidden"})
        if not candidate.is_file():
            alt = DESK / Path(path.lstrip("/")).name
            if alt.is_file():
                candidate = alt
            else:
                self.send_error(404)
                return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def json(self, code, obj):
        raw = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

def main():
    DESK.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        core = ensure_core(force=False)
        LOG.info("core count=%s rebuilt=%s", core.get("count"), core.get("rebuilt"))
    except Exception as e:
        LOG.warning("ensure_core: %s", e)
    get_engine()
    LOG.info("ASH08 on 0.0.0.0:%s paper=%s seed_pool=%s core=%s upstox=%s",
             PORT, "PaperEngine" in MODS, CORE_COUNT, len(core_symbols_live()),
             upstox_status().get("detail"))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
