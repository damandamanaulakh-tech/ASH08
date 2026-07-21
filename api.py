"""ASH08 API for Render — desk static + /api/health + demo."""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ash08.supabase_store import SupabaseStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DESK), **kwargs)

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/health":
            store = SupabaseStore()
            return self._json(200, {"ok": True, "service": "ash08-desk", "store": store.health()})
        if path == "/api/scan/latest":
            return self._json(200, SupabaseStore().load_scan() or {"rows": []})
        if path == "/api/universe/core":
            return self._json(200, SupabaseStore().load_universe("core") or {"count": 0, "symbols": []})
        if path == "/api/demo/run":
            return self._run_demo()
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _run_demo(self):
        try:
            from ash08.universe import UniverseManager, _demo_rows
            from ash08.scanner import demo_metrics, run_scan
            from ash08.paper_engine import PaperEngine, evaluate_governor

            store = SupabaseStore()
            mgr = UniverseManager(data_dir="ash08_data")
            uni = mgr.rebuild_from_rows(_demo_rows(80))
            store.save_universe("core", uni["core"].to_dict())
            store.save_universe("discovery", uni["discovery"].to_dict())

            snap = run_scan(demo_metrics(), universe_bucket="demo")
            store.save_scan(snap.to_dict())

            eng = PaperEngine(data_dir="ash08_data")
            eng.governor = evaluate_governor(damage=True)
            eng.place_order("TCS", "BUY", "MARKET", 20, 3840, stop=3700, target=4000)
            state = {
                "governor": eng.governor.to_dict() if hasattr(eng.governor, "to_dict") else eng.governor,
                "orders": eng.orders if isinstance(eng.orders[0], dict) else [o.to_dict() for o in eng.orders],
                "positions": eng.positions if eng.positions and isinstance(eng.positions[0], dict) else [p.to_dict() for p in eng.positions],
            }
            store.save_paper_state(state)
            return self._json(200, {
                "ok": True,
                "core_count": uni["core"].count,
                "scan": {"select": snap.select_count, "watch": snap.watch_count, "reject": snap.reject_count},
                "paper_orders": len(eng.orders),
                "supabase": store.health(),
            })
        except Exception as e:
            LOG.exception("demo failed")
            return self._json(500, {"ok": False, "error": str(e)})

    def _json(self, code, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)


def main():
    DESK.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    LOG.info("ASH08 api on :%s", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
