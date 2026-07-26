"""ASH08 API — python api.py. Local JSON primary. Emergency restore."""
from __future__ import annotations
import json, logging, mimetypes, os, sys
from datetime import datetime, timezone
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
    return m

MODS = load_mods()
LOG.info("modules: %s", sorted(MODS.keys()))

DEMO = [("TCS","IT",3840),("INFY","IT",1850),("HDFCBANK","Finance",1690),
        ("ICICIBANK","Finance",1180),("RELIANCE","Energy",2950),("SBIN","Finance",820),
        ("BHARTIARTL","Telecom",1560),("ITC","FMCG",450),("LT","Infra",3550),
        ("AXISBANK","Finance",1120),("HCLTECH","IT",1620),("MARUTI","Auto",12500),
        ("COCHINSHIP","Defence",1450),("MTARTECH","Defence",1850),("BEL","Defence",280),
        ("HAL","Defence",4200),("SUNPHARMA","Pharma",1680),("TITAN","Consumer",3400),
        ("ASIANPAINT","Consumer",2900),("KOTAKBANK","Finance",1780)]

def seed():
    if "store" not in MODS or "Metrics" not in MODS:
        return {"ok": False, "error": "modules missing"}
    store = MODS["store"]()
    symbols = [s for s,_,_ in DEMO]
    rows = [{"symbol":s,"name":s,"instrument_key":"NSE_EQ|"+s,"segment":seg,"ltp":ltp} for s,seg,ltp in DEMO]
    store.save_universe("core", {"bucket":"core","asof":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":"demo","count":len(symbols),"symbols":symbols,"rows":rows,"notes":["local demo"]})
    M = MODS["Metrics"]
    metrics = [M(symbol=s, adv20=400000-i*10000, turnover_cr_5d=12.0-i*0.3, stale_days=0,
                 mom_6m=0.15-(i%7)*0.03, quality_score=78.0-(i%5)*4.0, max_corr_vs_book=0.4,
                 segment=seg, ltp=ltp) for i,(s,seg,ltp) in enumerate(DEMO)]
    snap = MODS["run_scan"](metrics, universe_bucket="core")
    store.save_scan(snap.to_dict())
    LOG.info("seeded select=%s", snap.select_count)
    return {"ok":True,"mode":"local_demo","core_count":len(symbols),"select":snap.select_count,
            "watch":snap.watch_count,"reject":snap.reject_count}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, f, *a): LOG.info("%s - %s", self.address_string(), f%a)
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Length","0"); self.end_headers()
    def do_GET(self):
        p = unquote(urlparse(self.path).path or "/")
        if p.endswith("/") and len(p)>1: p=p[:-1]
        LOG.info("GET %s", p)
        if p=="/api/health": return self.j(200,{"ok":True,"service":"ash08-desk","modules":sorted(MODS.keys()),
            "store":(MODS["store"]().health() if "store" in MODS else {}),
            "note":"Local JSON primary. Supabase 403 non-blocking."})
        if p=="/api/demo/run":
            try: return self.j(200, seed())
            except Exception as e: return self.j(500,{"ok":False,"error":str(e)})
        if p=="/api/universe/core":
            if "store" not in MODS: return self.j(500,{"ok":False})
            d = MODS["store"]().load_universe("core")
            if not d or not d.get("symbols"): seed(); d=MODS["store"]().load_universe("core")
            return self.j(200, d or {"count":0,"symbols":[]})
        if p=="/api/scan/latest":
            if "store" not in MODS: return self.j(500,{"ok":False})
            d = MODS["store"]().load_scan()
            if not d or not d.get("rows"): seed(); d=MODS["store"]().load_scan()
            return self.j(200, d or {"rows":[]})
        if p=="/api/scan/run":
            try:
                seed()
                d = MODS["store"]().load_scan()
                return self.j(200,{"ok":True,"mode":"local_demo","select":(d or {}).get("select_count"),
                                   "watch":(d or {}).get("watch_count"),"reject":(d or {}).get("reject_count")})
            except Exception as e: return self.j(500,{"ok":False,"error":str(e)})
        c = DESK/"ASH08_Desk_Dashboard.html" if p in ("/","") else (DESK/p.lstrip("/"))
        if not c.is_file(): c = DESK/Path(p).name
        if not c.is_file(): self.send_error(404); return
        data = c.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(c))[0] or "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control","no-cache"); self.end_headers(); self.wfile.write(data)
    def j(self, code, obj):
        raw = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(raw)

def main():
    DESK.mkdir(parents=True, exist_ok=True)
    try: seed()
    except Exception as e: LOG.warning("boot seed: %s", e)
    LOG.info("ASH08 on 0.0.0.0:%s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

if __name__ == "__main__":
    main()
