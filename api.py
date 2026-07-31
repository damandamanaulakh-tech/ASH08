"""ASH08 API emergency restore."""
from __future__ import annotations
import json, logging, mimetypes, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

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
        from ash08.upstox_client import fetch_quotes, user_profile, fetch_nse_equity_instruments
        m["fetch_quotes"] = fetch_quotes
        m["profile"] = user_profile
        m["fetch_nse"] = fetch_nse_equity_instruments
    except Exception as e:
        LOG.error("upstox: %s", e)
    return m

MODS = load_mods()
LOG.info("modules: %s", sorted(MODS.keys()))

try:
    from ash08.core_seed import CORE_SYMBOLS, CORE_COUNT
except Exception:
    CORE_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
    CORE_COUNT = len(CORE_SYMBOLS)

_ENGINE = None
def get_engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if "PaperEngine" not in MODS:
        return None
    try:
        from ash08.paper_engine import DEFAULT_BOOK
        book = DEFAULT_BOOK
    except Exception:
        book = 5_000_000.0
    eng = MODS["PaperEngine"](data_dir=str(DATA_DIR), book_value=book)
    _ENGINE = eng
    return eng

def quotes_for_symbols(symbols):
    if "fetch_quotes" not in MODS:
        return {}
    if not (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip():
        return {}
    keys = ["NSE_EQ|%s" % s for s in symbols if s]
    if not keys:
        return {}
    try:
        raw = MODS["fetch_quotes"](keys)
    except Exception as e:
        LOG.warning("quotes: %s", e)
        return {}
    out = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        sym = k.split("|")[-1] if "|" in k else k
        lp = v.get("last_price") or v.get("lastPrice")
        if lp is None and isinstance(v.get("ohlc"), dict):
            lp = v["ohlc"].get("close")
        if lp is not None:
            try:
                out[str(sym).upper()] = float(lp)
            except Exception:
                pass
    return out

def upstox_status():
    tok = bool((os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip())
    info = {"token_set": tok, "connected": False, "detail": "no token" if not tok else "token present"}
    if tok and "profile" in MODS:
        try:
            MODS["profile"]()
            info["connected"] = True
            info["detail"] = "profile ok"
        except Exception as e:
            info["detail"] = "token set but API failed: %s" % e
    return info

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()
    def do_POST(self):
        path = unquote(urlparse(self.path).path or "/")
        if path.endswith("/"):
            path = path[:-1]
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() if n else "{}")
        except Exception:
            body = {}
        if path == "/api/paper/buy":
            return self.api_paper_buy(body)
        if path in ("/api/pnl/tick", "/api/live/refresh"):
            return self.api_live_refresh()
        return self.json(404, {"ok": False, "error": "not found"})
    def do_GET(self):
        path = unquote(urlparse(self.path).path or "/")
        if not path.startswith("/"):
            path = "/" + path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        if path == "/api/health":
            eng = get_engine()
            open_n = eng.open_count() if eng and hasattr(eng, "open_count") else 0
            ux = upstox_status()
            return self.json(200, {"ok": True, "service": "ash08-desk", "modules": sorted(MODS.keys()),
                "paper_open": open_n, "upstox": ux, "upstox_token_set": ux.get("token_set"),
                "trade_plan": {"stop_pct": 3.0, "target_pct": 6.0, "max_hold_days": 15, "max_open": 80, "book": 5000000}})
        if path == "/api/paper/book":
            return self.api_paper_book()
        if path in ("/api/pnl/tick", "/api/live/refresh"):
            return self.api_live_refresh()
        if path == "/api/scan/latest":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_scan()
            return self.json(200, data or {"rows": []})
        if path == "/api/universe/core":
            if "store" not in MODS:
                return self.json(500, {"ok": False, "error": "store missing"})
            data = MODS["store"]().load_universe("core")
            return self.json(200, data or {"count": 0, "symbols": []})
        if path == "/api/paper/auto":
            return self.json(200, {"ok": True, "note": "auto requires scan rows + live quotes"})
        return self.serve_static(path)

    def api_live_refresh(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        live = quotes_for_symbols(opens)
        if not live:
            return self.json(503, {"ok": False, "error": "no Upstox quotes", "ltp_source": "none",
                "upstox": upstox_status(), "open_symbols": opens})
        if hasattr(eng, "mark_to_market"):
            eng.mark_to_market(live)
        else:
            for s, px in live.items():
                eng.update_ltp(s, px)
        book = eng.book_summary() if hasattr(eng, "book_summary") else {}
        return self.json(200, {"ok": True, "ltp_source": "upstox", "marked": len(live), "prices": live,
            "unrealized_pnl": book.get("unrealized_pnl"), "equity": book.get("equity"), "upstox": upstox_status()})

    def api_paper_book(self):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        opens_sym = [p["symbol"] for p in eng.positions if p.get("status") == "OPEN"]
        live = quotes_for_symbols(opens_sym)
        if live:
            if hasattr(eng, "mark_to_market"):
                eng.mark_to_market(live)
            else:
                for s, px in live.items():
                    eng.update_ltp(s, px)
        if hasattr(eng, "book_summary"):
            book = eng.book_summary()
        else:
            book = {"open": [p for p in eng.positions if p.get("status") == "OPEN"],
                    "closed": [p for p in eng.positions if p.get("status") == "CLOSED"],
                    "open_count": sum(1 for p in eng.positions if p.get("status") == "OPEN"),
                    "unrealized_pnl": 0, "realized_pnl": 0}
        ux = upstox_status()
        return self.json(200, {"ok": True, **book,
            "orders": list(reversed(eng.orders[-50:])),
            "order_count": len(eng.orders),
            "ltp_source": "upstox" if live else "no_live_quotes",
            "upstox": ux,
            "max_open": book.get("max_open", 80),
            "note": "If ltp_source is not upstox, P&L stays zero because Entry==LTP seed."})

    def api_paper_buy(self, body):
        eng = get_engine()
        if not eng:
            return self.json(500, {"ok": False, "error": "paper engine missing"})
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            return self.json(400, {"ok": False, "error": "symbol required"})
        try:
            qty = max(1, int(float(body.get("qty") or 1)))
        except Exception:
            qty = 1
        price = body.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except Exception:
            price = None
        if not price or price <= 0:
            live = quotes_for_symbols([symbol])
            price = live.get(symbol)
        if not price or price <= 0:
            return self.json(400, {"ok": False, "error": "no_real_price — need Upstox quote or pass price"})
        order = eng.place_order(symbol=symbol, side="BUY", order_type="MARKET", qty=qty,
                                fill_price=price, source="manual")
        return self.json(200, {"ok": True, "order": order, "open_count": eng.open_count() if hasattr(eng, "open_count") else 0})

    def serve_static(self, path):
        candidate = DESK / "ASH08_Desk_Dashboard.html" if path in ("/", "") else (DESK / path.lstrip("/")).resolve()
        if path not in ("/", ""):
            try:
                candidate.relative_to(DESK.resolve())
            except ValueError:
                return self.json(403, {"ok": False, "error": "forbidden"})
        if not candidate.is_file():
            self.send_error(404)
            return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "text/html")
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
    get_engine()
    LOG.info("ASH08 on 0.0.0.0:%s modules=%s", PORT, sorted(MODS.keys()))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
