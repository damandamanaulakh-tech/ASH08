"""ASH08 API. Start: python api.py
Session last is Upstox. Yahoo last only after hours. Never REF_LTP.
"""
from __future__ import annotations
import json, logging, mimetypes, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import threading
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ash08.config import CORE_MAX, CORE_MIN, public_config 
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR = Path("ash08_data")
BUILD = "2026-09-10-desk-honest"
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
_LAST_PACK = {"prices": {}, "source": "no_live_ltp", "upstox_n": 0, "yahoo_n": 0}
def get_engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if "PaperEngine" not in MODS:
        return None
    eng = MODS["PaperEngine"](data_dir=str(DATA_DIR))
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

def quotes_pack_for(symbols):
    global _LAST_PACK
    try:
        from ash08.quotes import quotes_pack
        pack = quotes_pack([str(s).upper() for s in (symbols or []) if s], DATA_DIR)
    except Exception as e:
        LOG.warning("quotes: %s", e)
        pack = {"prices": {}, "source": "no_live_ltp", "upstox_n": 0, "yahoo_n": 0, "error": str(e)}
    _LAST_PACK = pack
    return pack

def quotes_for_symbols(symbols):
    return quotes_pack_for(symbols).get("prices") or {}

def auto_buy_from_scan(scan_dict):
    eng = get_engine()
    if not eng or not hasattr(eng, "auto_buy_selects"):
        return None
    picks = [
        r for r in (scan_dict.get("rows") or [])
        if str(r.get("decision") or "").upper() in ("SELECT", "NEAR_MISS")
    ]
    if not picks:
        return {"bought": 0, "skipped": 0, "open_count": len(eng.open_symbols())}
    pack = quotes_pack_for([r.get("symbol") for r in picks])
    live = pack.get("prices") or {}
    priced = []
    skipped_px = []
    for r in picks:
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
    result["ltp_source"] = pack.get("source")
    return result


def run_robot_tick(force_buy=False):
    from ash08.robot import tick as robot_tick
    eng = get_engine()
    if not eng:
        return {"ok": False, "error": "paper engine missing", "armed": False}
    try:
        body = robot_tick(eng, quote_fn=quotes_pack_for, force_buy=force_buy)
    except Exception as e:
        LOG.exception("robot tick")
        return {"ok": False, "error": str(e), "armed": True}
    body["upstox"] = upstox_status()
    return body


def _robot_status():
    try:
        from ash08.robot import status as robot_status
        return robot_status()
    except Exception as e:
        return {"ok": False, "error": str(e), "armed": False}


def _clock_pulse():
    try:
        from ash08.clock import pulse
        eng = get_engine()
        if eng:
            return pulse(eng, quotes_pack_for)
    except Exception:
        LOG.exception("clock pulse")
    return {}


def _robot_loop():
    try:
        _clock_pulse()
        run_robot_tick(force_buy=False)
    except Exception:
        LOG.exception("robot first tick")
    while True:
        time.sleep(45)
        try:
            _clock_pulse()
            run_robot_tick(force_buy=False)
        except Exception:
            LOG.exception("robot loop")


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
    pack = quotes_pack_for(symbols[:50] + opens)
    live = pack.get("prices") or {}
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
        f"ltp_source={pack.get('source')}",
    ]
    store = MODS["store"]()
    store.save_scan(scan_dict)
    auto = auto_buy_from_scan(scan_dict) if auto_buy else None
    return {
        "ok": True,
        "core_count": len(symbols),
        "select": snap.select_count,
        "near_miss": getattr(snap, "near_miss_count", 0),
        "watch": snap.watch_count,
        "reject": snap.reject_count,
        "unknown": getattr(snap, "unknown_count", 0),
        "auto_paper": auto,
        "upstox": upstox_status(),
        "notes": scan_dict["notes"],
        "ltp_source": pack.get("source") or "no_live_ltp",
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
        if path in ("/api/paper/order", "/api/paper/ticket"):
            return self.api_paper_order(body)
        if path == "/api/paper/sell":
            return self.api_paper_sell(body)
        if path in ("/api/paper/close-all", "/api/paper/close_all"):
            return self.api_paper_close_all(body)
        if path in ("/api/paper/notes", "/api/positions/notes"):
            return self.api_paper_notes(body)
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
            advise_n = {}
            try:
                from ash08.advisory import payload as advise
                eng = get_engine()
                book = {"open_n": len(eng.open_symbols())} if eng else None
                a = advise(book=book)
                advise_n = {"buy": a.get("buy_n"), "watch": a.get("watch_n"), "asof": a.get("asof")}
            except Exception as e:
                advise_n = {"error": str(e)}
            return self.json(200, {
                "ok": True, "service": "ash08-desk", "modules": sorted(MODS.keys()),
                "core_seed_count": CORE_COUNT, "core_count": len(core_symbols_live()),
                "paper_open": open_n, "store": store_info,
                "upstox": ux, "upstox_token_set": ux.get("token_set"), "upstox_connected": ux.get("connected"),
                "trade_plan": public_config()["trade_plan"],
                "contract": public_config(),
                "universe": (_mgr().status() if _mgr() else {}),
                "build": BUILD,
                "parameter_set_id": public_config()["parameter_set_id"],
                "advise": advise_n,
                "robot": _robot_status(),
                "ltp": {"source": _LAST_PACK.get("source"), "n": len(_LAST_PACK.get("prices") or {}),
                        "upstox_n": _LAST_PACK.get("upstox_n"), "yahoo_n": _LAST_PACK.get("yahoo_n")},
                "note": "Paper robot: Upstox last in NSE session, Yahoo last after hours. New buys need the session unless force=1.",
            })
        if path == "/api/quotes":
            syms = [s.strip().upper() for s in ((qs.get("symbols") or [""])[0]).split(",") if s.strip()]
            if not syms:
                try:
                    from ash08.advisory import payload as advise
                    syms = [r["symbol"] for r in (advise().get("buy") or [])]
                except Exception:
                    syms = []
            pack = quotes_pack_for(syms)
            return self.json(200, {"ok": True, **pack, "symbols": syms})
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
            from ash08.session import session_state
            ux = upstox_status()
            sess = session_state()
            if sess.get("quote_mode") == "yahoo":
                payload = fetch_index_tiles(lambda _keys: {}, False, after_hours=True)
                payload["session"] = sess.get("why")
                payload["quote_mode"] = "yahoo"
                return self.json(200, payload)
            if "fetch_quotes" not in MODS:
                payload = fetch_index_tiles(lambda _keys: (_ for _ in ()).throw(RuntimeError("upstox module missing")), False)
                payload["detail"] = "upstox module missing"
                return self.json(200, payload)
            payload = fetch_index_tiles(MODS["fetch_quotes"], bool(ux.get("token_set")))
            payload["upstox"] = ux
            payload["session"] = sess.get("why")
            payload["quote_mode"] = "upstox"
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
        if path == "/api/chitty":
            from ash08.chitty_adopted import registry_payload
            return self.json(200, registry_payload())
        if path == "/api/orders" or path.startswith("/api/orders/"):
            from ash08.orders import evidence_for, load_order_map
            extra = path[len("/api/orders/"):] if path.startswith("/api/orders/") else ""
            param = extra or (qs.get("symbol") or [""])[0]
            if not param:
                pack = load_order_map()
                return self.json(200, {"asof": pack.get("asof"), "n": pack.get("n"), "source": pack.get("source")})
            return self.json(200, evidence_for(param))
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
        if path in ("/api/desk", "/api/dashboard"):
            return self.api_desk()
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
                "side": "BUY",
            }
            return self.api_paper_buy(body)
        if path == "/api/paper/sell":
            body = {
                "symbol": (qs.get("symbol") or [""])[0],
                "price": (qs.get("price") or [""])[0],
            }
            return self.api_paper_sell(body)
        if path == "/api/paper/auto":
            return self.json(200, run_robot_tick(force_buy=True))
        if path in ("/api/robot/tick", "/api/robot/run"):
            force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
            return self.json(200, run_robot_tick(force_buy=force))
        if path in ("/api/robot", "/api/robot/status"):
            st = _robot_status()
            st["upstox"] = upstox_status()
            st["ltp"] = {"source": _LAST_PACK.get("source"), "n": len(_LAST_PACK.get("prices") or {})}
            return self.json(200, st)
        if path in ("/api/history", "/api/history/yoy"):
            from ash08.governor_lock import payload as hist
            return self.json(200, hist())
        if path in ("/api/factors", "/api/momentum"):
            from ash08.momentum_factors import payload as mom
            return self.json(200, mom())
        if path in ("/api/advise", "/api/advice", "/api/picks"):
            from ash08.advisory import payload as advise
            eng = get_engine()
            book = {"open_n": len(eng.open_symbols())} if eng else None
            return self.json(200, advise(book=book))
        if path in ("/api/gaps", "/api/infinity"):
            from ash08.infinity_gaps import payload as gaps
            return self.json(200, gaps())
        if path == "/api/reports":
            return self.api_ops("reports")
        if path == "/api/risk":
            return self.api_ops("risk")
        if path == "/api/alerts":
            return self.api_ops("alerts")
        if path == "/api/settings":
            return self.api_ops("settings")
        if path in ("/api/engine", "/api/clock"):
            return self.api_ops("engine")
        if path == "/api/strategy":
            from ash08.ops import strategy
            return self.json(200, strategy())
        if path == "/api/register":
            return self.api_ops("register")
        if path == "/api/shadow":
            return self.api_ops("shadow")
        if path == "/api/schedule":
            from ash08.clock import schedule_payload
            eng = get_engine()
            return self.json(200, schedule_payload(getattr(eng, "clock_last", None) or {}))
        if path == "/api/triggers":
            return self.api_ops("triggers")
        return self.serve_static(path)

    def api_paper_book(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        pack = quotes_pack_for(opens_sym)
        live = pack.get("prices") or {}
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
        ltp_source = pack.get("source") or ("live" if live else "no_live_ltp")
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
            "cash": book.get("cash"),
            "equity": book.get("equity"),
            "book_value": book.get("book_value"),
            "deployed": book.get("deployed"),
            "reserve_pct": book.get("reserve_pct"),
            "buy_cost_pct": book.get("buy_cost_pct"),
            "sell_cost_pct": book.get("sell_cost_pct"),
            "max_open": book.get("max_open"),
            "journal": book.get("journal") or getattr(eng, "journal", []),
            "pending": book.get("pending") or [],
            "closed_count": book.get("closed_count") or 0,
            "closed_stats": book.get("closed_stats") or {},
            "deployed_pct": book.get("deployed_pct"),
            "parameter_set_id": cfg.get("parameter_set_id"),
            "build": BUILD,
            "note": "Cash is tracked. Book persists. P&L needs live last. Missing quote ≠ fake fill. SELL is first-class.",
        })

    def api_pnl_tick(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        pack = quotes_pack_for(opens_sym)
        live = pack.get("prices") or {}
        if hasattr(eng, "book_payload"):
            book = eng.book_payload(live_prices=live)
        else:
            if hasattr(eng, "mark_to_market"):
                eng.mark_to_market(live)
            book = {"unrealized_pnl": 0, "realized_pnl": 0, "total_pnl": 0, "open": [], "open_count": 0}
        return self.json(200, {
            "ok": True,
            "ltp_source": pack.get("source") or ("live" if live else "no_live_ltp"),
            "unrealized_pnl": book.get("unrealized_pnl") or 0,
            "realized_pnl": book.get("realized_pnl") or 0,
            "total_pnl": book.get("total_pnl") or 0,
            "open_count": book.get("open_count") or 0,
            "open": book.get("open") or [],
            "upstox": upstox_status(),
        })

    def _num(self, v, d=None):
        if v is None or v == "":
            return d
        try:
            return float(v)
        except Exception:
            return d

    def api_paper_buy(self, body):
        body = dict(body or {})
        body["side"] = "BUY"
        return self.api_paper_order(body)

    def api_paper_sell(self, body):
        body = dict(body or {})
        body["side"] = "SELL"
        return self.api_paper_order(body)

    def api_paper_order(self, body):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            return self.json(400, {"ok": False, "error": "symbol required"})
        side = str(body.get("side") or "BUY").upper()
        ot = str(body.get("order_type") or body.get("type") or "MARKET").upper()
        try:
            qty = max(1, int(float(body.get("qty") or 50)))
        except Exception:
            qty = 50
        price = self._num(body.get("price") or body.get("limit_price"))
        stop = self._num(body.get("stop"))
        target = self._num(body.get("target"))
        live = quotes_for_symbols([symbol])
        live_px = live.get(symbol)
        if side == "SELL":
            px = price if price and price > 0 else live_px
            if not px or px <= 0:
                return self.json(400, {"ok": False, "error": "no_live_ltp — type a fill or connect quotes. Will not invent a sell."})
            try:
                order = eng.close_position(symbol, px, reason="OWNER_SELL", source="manual")
            except Exception as e:
                LOG.exception("sell")
                return self.json(500, {"ok": False, "error": str(e)})
            if order.get("status") == "REJECTED":
                return self.json(400, {"ok": False, "error": order.get("reason") or "rejected", "order": order})
            return self.json(200, {
                "ok": True, "order": order,
                "open_count": len(eng.open_symbols()),
                "message": f"PAPER SELL {symbol} x {order.get('qty')} @ {px} → Closed Trades ({order.get('exit_reason')})",
            })
        if ot == "LIMIT":
            if not price or price <= 0:
                return self.json(400, {"ok": False, "error": "limit price required"})
            order = eng.place_order(
                symbol=symbol, side="BUY", order_type="LIMIT", qty=qty,
                fill_price=price, stop=stop, target=target, source="manual",
            )
            return self.json(200, {
                "ok": True, "order": order,
                "message": f"PAPER LIMIT BUY {symbol} @ {price} day-resting — fills if live last ≤ limit",
            })
        px = price if price and price > 0 else live_px
        if not px or px <= 0:
            return self.json(400, {"ok": False, "error": "no_live_ltp — will not invent a fill"})
        try:
            order = eng.place_order(
                symbol=symbol, side="BUY", order_type="MARKET", qty=qty,
                fill_price=px, stop=stop, target=target, source="manual",
                why=body.get("why"),
                mode=body.get("mode") or "momentum",
            )
            if hasattr(eng, "book_payload"):
                eng.book_payload(live_prices=quotes_for_symbols([symbol]))
        except Exception as e:
            LOG.exception("buy")
            return self.json(500, {"ok": False, "error": str(e)})
        opens = [p for p in eng.positions if p.get("status") == "OPEN"]
        msg = order.get("reason") or order.get("status")
        return self.json(200, {
            "ok": order.get("status") == "FILLED",
            "order": order, "open_count": len(opens), "positions": opens,
            "message": f"PAPER {order.get('status')}: {symbol} x {order.get('sized_qty') or order.get('qty')} @ {px} | stop={order.get('stop')} target={order.get('target')} hold={order.get('hold_days')}d"
            if order.get("status") == "FILLED" else f"PAPER REJECTED {symbol}: {msg}",
        })

    def api_paper_close_all(self, body=None):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        pack = quotes_pack_for(opens)
        live = pack.get("prices") or {}
        result = eng.close_all(live)
        return self.json(200, {
            "ok": True,
            **result,
            "ltp_source": pack.get("source"),
            "open_count": len(eng.open_symbols()),
            "message": f"Closed {result.get('closed') or 0} at live last. {len(result.get('skipped') or [])} left open (no quote).",
        })

    def api_desk(self):
        eng = get_engine()
        advise = {}
        try:
            from ash08.advisory import payload as advise_fn
            book_n = {"open_n": len(eng.open_symbols())} if eng else None
            advise = advise_fn(book=book_n)
        except Exception as e:
            advise = {"ok": False, "error": str(e)}
        book = {}
        if eng and hasattr(eng, "book_payload"):
            opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
            pack = quotes_pack_for(opens_sym)
            book = eng.book_payload(live_prices=pack.get("prices") or {})
            book["ltp_source"] = pack.get("source")
        else:
            pack = {"source": "no_live_ltp"}
        why_map = {str(r.get("symbol") or "").upper(): r for r in (advise.get("buy") or []) + (advise.get("watch") or [])}
        for p in book.get("open") or []:
            row = why_map.get(str(p.get("symbol") or "").upper())
            if row and not p.get("why"):
                p["why"] = row.get("why")
            if row:
                p["steps"] = row.get("steps") or []
                p["rank"] = row.get("rank")
        tiles = []
        try:
            from ash08.indices import fetch_index_tiles
            ux = upstox_status()
            if "fetch_quotes" in MODS:
                tiles = fetch_index_tiles(MODS["fetch_quotes"], bool(ux.get("token_set"))).get("tiles") or []
            else:
                tiles = fetch_index_tiles(lambda _k: (_ for _ in ()).throw(RuntimeError("missing")), False).get("tiles") or []
        except Exception:
            tiles = []
        robot = _robot_status()
        cfg = public_config()
        return self.json(200, {
            "ok": True,
            "build": BUILD,
            "parameter_set_id": cfg.get("parameter_set_id"),
            "contract": {
                "book_value": cfg["book_value"],
                "stop_pct": cfg["stop_pct"],
                "target_pct": cfg["target_pct"],
                "max_hold_sessions": cfg["max_hold_sessions"],
                "score_select": cfg["scanner"]["score_select"],
                "kelly": cfg["sizing"],
            },
            "advise": advise,
            "book": book,
            "robot": robot,
            "indices": tiles,
            "upstox": upstox_status(),
            "governor": eng.governor.to_dict() if eng and hasattr(eng.governor, "to_dict") else {},
            "skipped": (robot.get("skipped_detail") or [])[:20],
        })

    def api_paper_notes(self, body):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            return self.json(400, {"ok": False, "error": "symbol required"})
        ok = eng.set_notes(symbol, body.get("notes") or "", body.get("tags"))
        if not ok:
            return self.json(404, {"ok": False, "error": "not_open"})
        return self.json(200, {"ok": True, "symbol": symbol})

    def api_ops(self, kind):
        from ash08 import ops
        eng = get_engine()
        if not eng and kind not in ("strategy",):
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        robot = _robot_status()
        ux = upstox_status()
        advise = {}
        if kind in ("register", "triggers"):
            try:
                from ash08.advisory import payload as advise_fn
                book_n = {"open_n": len(eng.open_symbols())} if eng else None
                advise = advise_fn(book=book_n)
            except Exception as e:
                advise = {"ok": False, "error": str(e)}
        if kind == "reports":
            return self.json(200, ops.reports(eng))
        if kind == "risk":
            return self.json(200, ops.risk(eng))
        if kind == "alerts":
            return self.json(200, ops.alerts(eng, robot))
        if kind == "settings":
            return self.json(200, ops.settings(ux, robot, eng, BUILD))
        if kind == "engine":
            return self.json(200, ops.engine_status(eng, robot, ux))
        if kind == "register":
            return self.json(200, ops.register(advise))
        if kind == "shadow":
            return self.json(200, ops.shadow_payload(eng))
        if kind == "triggers":
            return self.json(200, ops.triggers(advise))
        return self.json(404, {"ok": False, "error": "unknown ops"})

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
    t = threading.Thread(target=_robot_loop, name="ash08-robot", daemon=True)
    t.start()
    LOG.info("ASH08 on 0.0.0.0:%s paper=%s seed_pool=%s core=%s upstox=%s robot=on ltp=upstox-session|yahoo-after-hours",
             PORT, "PaperEngine" in MODS, CORE_COUNT, len(core_symbols_live()),
             upstox_status().get("detail"))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
