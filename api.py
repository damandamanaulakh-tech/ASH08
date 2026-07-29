"""ASH08 API — paper trade book wired. Start: python api.py"""
from __future__ import annotations
import json, logging, mimetypes, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR = Path("ash08_data")

def load_mods():
    m = {}
    try:
        from ash08.supabase_store import SupabaseStore
        m["store"] = SupabaseStore
    except Exception as e:
        LOG.error("store: %s", e)
    try:
        from ash08.scanner import StockMetrics, run_scan
        m["Metrics"] = StockMetrics
        m["run_scan"] = run_scan
    except Exception as e:
        LOG.error("scanner: %s", e)
    try:
        from ash08.paper_engine import PaperEngine
        m["PaperEngine"] = PaperEngine
    except Exception as e:
        LOG.error("paper: %s", e)
    try:
        from ash08.upstox_client import fetch_nse_equity_instruments, fetch_quotes, user_profile
        m["fetch_nse"] = fetch_nse_equity_instruments
        m["fetch_quotes"] = fetch_quotes
        m["profile"] = user_profile
    except Exception as e:
        LOG.error("upstox: %s", e)
    try:
        from ash08.universe import InstrumentRow, UniverseManager
        m["Row"] = InstrumentRow
        m["Uni"] = UniverseManager
    except Exception as e:
        LOG.error("universe: %s", e)
    return m

MODS = load_mods()
LOG.info("modules: %s", sorted(MODS.keys()))
try:
    from ash08.core_seed import CORE_SYMBOLS, CORE_COUNT
except Exception:
    CORE_SYMBOLS = ["TCS","HDFCBANK","RELIANCE","INFY","ICICIBANK","SBIN","ITC","HAL","BEL","MTARTECH"]
    CORE_COUNT = len(CORE_SYMBOLS)

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

def seed_demo_local():
    if "store" not in MODS or "Metrics" not in MODS or "run_scan" not in MODS:
        return {"ok": False, "error": "modules missing"}
    from datetime import datetime, timezone
    store = MODS["store"]()
    symbols = list(CORE_SYMBOLS)
    rows = [{"symbol": s, "name": s, "instrument_key": f"NSE_EQ|{s}"} for s in symbols]
    store.save_universe("core", {
        "bucket": "core", "asof": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "seed", "count": len(symbols), "symbols": symbols, "rows": rows, "notes": []
    })
    Metrics = MODS["Metrics"]
    metrics = [Metrics(symbol=s, adv20=300000, turnover_cr_5d=10, stale_days=0,
                       mom_6m=0.1 - (i%5)*0.02, quality_score=70 - (i%7)*2, ltp=None)
               for i, s in enumerate(symbols[:300])]
    snap = MODS["run_scan"](metrics, universe_bucket="core")
    store.save_scan(snap.to_dict())
    return {"ok": True, "core_count": len(symbols), "select": snap.select_count,
            "watch": snap.watch_count, "reject": snap.reject_count}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Length","0"); self.end_headers()
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.send_header("Content-Length","0"); self.end_headers()
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
        return self.json(404, {"ok": False, "error": "not found"})
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if not path.startswith("/"): path = "/" + path
        if len(path) > 1 and path.endswith("/"): path = path[:-1]
        qs = parse_qs(parsed.query or "")
        if path == "/api/health":
            eng = get_engine()
            open_n = sum(1 for p in (eng.positions if eng else []) if p.get("status")=="OPEN")
            store_info = {}
            if "store" in MODS:
                try: store_info = MODS["store"]().health()
                except Exception as e: store_info = {"error": str(e)}
            return self.json(200, {"ok": True, "service": "ash08-desk", "modules": sorted(MODS.keys()),
                "core_seed_count": CORE_COUNT, "paper_open": open_n, "store": store_info})
        if path == "/api/universe/core":
            if "store" not in MODS: return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_universe("core")
            if not data or not data.get("symbols"):
                seed_demo_local(); data = MODS["store"]().load_universe("core")
            return self.json(200, data or {"count": 0, "symbols": []})
        if path == "/api/scan/latest":
            if "store" not in MODS: return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_scan()
            if not data or not data.get("rows"):
                seed_demo_local(); data = MODS["store"]().load_scan()
            return self.json(200, data or {"rows": []})
        if path == "/api/scan/run":
            return self.json(200, seed_demo_local())
        if path == "/api/demo/run":
            return self.json(200, seed_demo_local())
        if path == "/api/paper/book":
            return self.api_paper_book()
        if path == "/api/paper/buy":
            body = {"symbol": (qs.get("symbol") or [""])[0], "qty": (qs.get("qty") or ["1"])[0],
                    "price": (qs.get("price") or [""])[0], "stop": (qs.get("stop") or [""])[0],
                    "target": (qs.get("target") or [""])[0]}
            return self.api_paper_buy(body)
        return self.serve_static(path)
    def api_paper_book(self):
        eng = get_engine()
        if not eng: return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens = [p for p in eng.positions if p.get("status") == "OPEN"]
        gov = eng.governor.to_dict() if hasattr(eng.governor, "to_dict") else {
            "level": getattr(eng.governor, "level", "L0"), "exposure_pct": getattr(eng.governor, "exposure_pct", 100)}
        return self.json(200, {"ok": True, "governor": gov, "orders": list(reversed(eng.orders[-50:])),
            "positions": eng.positions, "open": opens, "open_count": len(opens), "order_count": len(eng.orders)})
    def api_paper_buy(self, body):
        eng = get_engine()
        if not eng: return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol: return self.json(400, {"ok": False, "error": "symbol required"})
        try: qty = max(1, int(float(body.get("qty") or 1)))
        except Exception: qty = 1
        def _f(v, d=None):
            if v is None or v == "": return d
            try: return float(v)
            except Exception: return d
        price = _f(body.get("price"))
        stop = _f(body.get("stop"))
        target = _f(body.get("target"))
        defaults = {"TCS":3840,"HDFCBANK":1690,"RELIANCE":2950,"INFY":1850,"ICICIBANK":1180,
                    "SBIN":820,"ITC":450,"MTARTECH":1850,"COCHINSHIP":1450,"HAL":4200,"BEL":280}
        if not price or price <= 0:
            price = defaults.get(symbol, 100.0)
        if stop is None: stop = round(price * 0.97, 2)
        if target is None: target = round(price * 1.06, 2)
        try:
            order = eng.place_order(symbol=symbol, side="BUY", order_type="MARKET",
                                    qty=qty, fill_price=price, stop=stop, target=target)
        except Exception as e:
            LOG.exception("buy"); return self.json(500, {"ok": False, "error": str(e)})
        opens = [p for p in eng.positions if p.get("status") == "OPEN"]
        return self.json(200, {"ok": True, "order": order, "open_count": len(opens), "positions": opens,
            "message": f"PAPER {order.get('status')}: {symbol} x {order.get('sized_qty') or order.get('qty')} @ {price}"})
    def serve_static(self, path):
        candidate = DESK / "ASH08_Desk_Dashboard.html" if path in ("/", "") else (DESK / path.lstrip("/")).resolve()
        if path not in ("/", ""):
            try: candidate.relative_to(DESK.resolve())
            except ValueError: return self.json(403, {"ok": False, "error": "forbidden"})
        if not candidate.is_file():
            alt = DESK / Path(path.lstrip("/")).name
            if alt.is_file(): candidate = alt
            else: self.send_error(404); return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers(); self.wfile.write(data)
    def json(self, code, obj):
        raw = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(raw)

def main():
    DESK.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try: seed_demo_local()
    except Exception as e: LOG.warning("seed: %s", e)
    get_engine()
    LOG.info("ASH08 on 0.0.0.0:%s paper=%s core=%s", PORT, "PaperEngine" in MODS, CORE_COUNT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
