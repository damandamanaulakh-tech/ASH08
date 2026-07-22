"""
ASH08 API — Render Start Command MUST be: python api.py

Fixes: previous handler fell through to static 404 for /api/*.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
import traceback
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
    try:
        from ash08.scanner import StockMetrics, run_scan
        m["Metrics"] = StockMetrics
        m["run_scan"] = run_scan
    except Exception as e:
        LOG.error("scanner: %s", e)
    return m


MODS = load_mods()
LOG.info("modules loaded: %s", sorted(MODS.keys()))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if not path.startswith("/"):
            path = "/" + path
        # strip trailing slash except root
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        LOG.info("GET %s", path)

        if path == "/api/health":
            return self.api_health()
        if path == "/api/upstox/profile":
            return self.api_profile()
        if path == "/api/universe/refresh":
            return self.api_universe_refresh()
        if path == "/api/universe/core":
            return self.api_universe_core()
        if path == "/api/scan/latest":
            return self.api_scan_latest()
        if path == "/api/scan/run":
            return self.api_scan_run()

        # static desk files
        return self.serve_static(path)

    def api_health(self):
        store_info = {}
        if "store" in MODS:
            try:
                store_info = MODS["store"]().health()
            except Exception as e:
                store_info = {"error": str(e)}
        self.json(200, {
            "ok": True,
            "service": "ash08-desk",
            "handler": "BaseHTTPRequestHandler",
            "modules": sorted(MODS.keys()),
            "upstox_token_set": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
            "supabase_url_set": bool(os.environ.get("SUPABASE_URL")),
            "store": store_info,
        })

    def api_profile(self):
        if "profile" not in MODS:
            return self.json(500, {"ok": False, "error": "upstox module missing"})
        try:
            return self.json(200, {"ok": True, "profile": MODS["profile"]()})
        except Exception as e:
            return self.json(401, {"ok": False, "error": str(e)})

    def api_universe_core(self):
        if "store" not in MODS:
            return self.json(500, {"ok": False, "error": "store missing"})
        data = MODS["store"]().load_universe("core")
        return self.json(200, data or {"count": 0, "symbols": []})

    def api_scan_latest(self):
        if "store" not in MODS:
            return self.json(500, {"ok": False, "error": "store missing"})
        return self.json(200, MODS["store"]().load_scan() or {"rows": []})

    def api_universe_refresh(self):
        for k in ("fetch_nse", "Row", "Uni", "store"):
            if k not in MODS:
                return self.json(500, {"ok": False, "error": f"missing {k}", "modules": sorted(MODS.keys())})
        try:
            instruments = MODS["fetch_nse"]()
            Row = MODS["Row"]
            rows = [
                Row(
                    symbol=r["symbol"],
                    name=r.get("name") or r["symbol"],
                    instrument_key=r.get("instrument_key") or "",
                    isin=r.get("isin") or "",
                    lot_size=int(r.get("lot_size") or 1),
                    tick_size=float(r.get("tick_size") or 0.05),
                )
                for r in instruments
            ]
            mgr = MODS["Uni"](data_dir="ash08_data")
            result = mgr.rebuild_from_rows(rows)
            store = MODS["store"]()
            store.save_universe("core", result["core"].to_dict())
            store.save_universe("discovery", result["discovery"].to_dict())
            return self.json(200, {
                "ok": True,
                "source": "upstox_nse_instruments",
                "instruments_loaded": len(rows),
                "core_count": result["core"].count,
                "discovery_count": result["discovery"].count,
                "core_sample": result["core"].symbols[:20],
                "supabase": store.health(),
            })
        except Exception as e:
            LOG.exception("universe refresh")
            return self.json(500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-1000:]})

    def api_scan_run(self):
        for k in ("store", "Metrics", "run_scan"):
            if k not in MODS:
                return self.json(500, {"ok": False, "error": f"missing {k}", "modules": sorted(MODS.keys())})
        try:
            store = MODS["store"]()
            core = store.load_universe("core")
            if not core or not core.get("symbols"):
                return self.json(400, {"ok": False, "error": "Core empty. Open /api/universe/refresh first."})
            symbols = core["symbols"]
            rows_meta = {r.get("symbol"): r for r in (core.get("rows") or []) if r.get("symbol")}
            keys = [(rows_meta.get(s) or {}).get("instrument_key") or f"NSE_EQ|{s}" for s in symbols]
            quotes, quote_error = {}, None
            if "fetch_quotes" in MODS:
                try:
                    quotes = MODS["fetch_quotes"](keys[:200])
                except Exception as e:
                    quote_error = str(e)
                    LOG.warning("quotes: %s", e)
            Metrics = MODS["Metrics"]
            metrics = []
            for s in symbols[:200]:
                meta = rows_meta.get(s) or {}
                key = meta.get("instrument_key") or f"NSE_EQ|{s}"
                q = quotes.get(key) or quotes.get(s) or {}
                last = None
                if isinstance(q, dict):
                    last = q.get("last_price") or (q.get("ohlc") or {}).get("close")
                metrics.append(Metrics(
                    symbol=s,
                    adv20=300_000 if last is not None else None,
                    turnover_cr_5d=10.0 if last is not None else None,
                    stale_days=0 if last is not None else 99,
                    mom_6m=0.05 if last is not None else -0.01,
                    quality_score=70.0 if last is not None else 40.0,
                    ltp=float(last) if last is not None else None,
                ))
            snap = MODS["run_scan"](metrics, universe_bucket="core")
            store.save_scan(snap.to_dict())
            return self.json(200, {
                "ok": True,
                "quote_error": quote_error,
                "scanned": len(metrics),
                "select": snap.select_count,
                "watch": snap.watch_count,
                "reject": snap.reject_count,
                "top": [
                    {"symbol": r["symbol"], "decision": r["decision"], "score": r["score"], "ltp": r.get("ltp")}
                    for r in snap.rows[:25]
                ],
            })
        except Exception as e:
            LOG.exception("scan")
            return self.json(500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-1000:]})

    def serve_static(self, path: str):
        if path in ("/", ""):
            rel = "index.html"
        else:
            rel = path.lstrip("/")
        # only allow desk/
        candidate = (DESK / rel).resolve()
        try:
            candidate.relative_to(DESK.resolve())
        except ValueError:
            return self.json(403, {"ok": False, "error": "forbidden"})
        if not candidate.is_file():
            # try under desk anyway for dashboard name
            alt = DESK / Path(rel).name
            if alt.is_file():
                candidate = alt
            else:
                self.send_error(404, "File not found")
                return
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def json(self, code: int, obj):
        raw = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def main():
    DESK.mkdir(parents=True, exist_ok=True)
    LOG.info("ASH08 listening 0.0.0.0:%s desk=%s", PORT, DESK)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
