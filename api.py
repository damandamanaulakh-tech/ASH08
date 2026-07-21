"""ASH08 API — real Upstox load + Supabase. Start: python api.py"""
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
from ash08.upstox_client import fetch_nse_equity_instruments, fetch_quotes, user_profile
from ash08.universe import InstrumentRow, UniverseManager

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
            return self._json(200, {
                "ok": True,
                "service": "ash08-desk",
                "store": SupabaseStore().health(),
                "upstox_token_set": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
            })
        if path == "/api/upstox/profile":
            try:
                return self._json(200, {"ok": True, "profile": user_profile()})
            except Exception as e:
                return self._json(401, {"ok": False, "error": str(e)})
        if path == "/api/universe/refresh":
            return self._universe_refresh()
        if path == "/api/universe/core":
            return self._json(200, SupabaseStore().load_universe("core") or {"count": 0, "symbols": []})
        if path == "/api/scan/latest":
            return self._json(200, SupabaseStore().load_scan() or {"rows": []})
        if path == "/api/scan/run":
            return self._scan_run()
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _universe_refresh(self):
        try:
            instruments = fetch_nse_equity_instruments()
            rows = [
                InstrumentRow(
                    symbol=r["symbol"],
                    name=r.get("name") or r["symbol"],
                    instrument_key=r.get("instrument_key") or "",
                    isin=r.get("isin") or "",
                    lot_size=int(r.get("lot_size") or 1),
                    tick_size=float(r.get("tick_size") or 0.05),
                )
                for r in instruments
            ]
            mgr = UniverseManager(data_dir="ash08_data")
            result = mgr.rebuild_from_rows(rows)
            store = SupabaseStore()
            store.save_universe("core", result["core"].to_dict())
            store.save_universe("discovery", result["discovery"].to_dict())
            return self._json(200, {
                "ok": True,
                "source": "upstox_nse_instruments",
                "instruments_loaded": len(rows),
                "core_count": result["core"].count,
                "discovery_count": result["discovery"].count,
                "core_sample": result["core"].symbols[:15],
                "supabase": store.health(),
            })
        except Exception as e:
            LOG.exception("universe refresh failed")
            return self._json(500, {"ok": False, "error": str(e)})

    def _scan_run(self):
        try:
            from ash08.scanner import StockMetrics, run_scan

            store = SupabaseStore()
            core = store.load_universe("core")
            if not core or not core.get("symbols"):
                return self._json(400, {"ok": False, "error": "Core empty — call /api/universe/refresh first"})
            symbols = core["symbols"]
            rows_meta = {r.get("symbol"): r for r in (core.get("rows") or []) if r.get("symbol")}
            keys = []
            for s in symbols:
                meta = rows_meta.get(s) or {}
                keys.append(meta.get("instrument_key") or f"NSE_EQ|{s}")
            quotes, quote_error = {}, None
            try:
                quotes = fetch_quotes(keys[:200])
            except Exception as e:
                quote_error = str(e)
                LOG.warning("quotes failed: %s", e)
            metrics = []
            for s in symbols[:200]:
                meta = rows_meta.get(s) or {}
                key = meta.get("instrument_key") or f"NSE_EQ|{s}"
                q = quotes.get(key) or quotes.get(s) or {}
                last = None
                if isinstance(q, dict):
                    last = q.get("last_price") or (q.get("ohlc") or {}).get("close")
                quality = 70.0 if last is not None else 40.0
                mom = 0.05 if last is not None else -0.01
                metrics.append(StockMetrics(
                    symbol=s,
                    adv20=300_000 if last is not None else None,
                    turnover_cr_5d=10.0 if last is not None else None,
                    stale_days=0 if last is not None else 99,
                    mom_6m=mom,
                    quality_score=quality,
                    ltp=float(last) if last is not None else None,
                ))
            snap = run_scan(metrics, universe_bucket="core")
            store.save_scan(snap.to_dict())
            return self._json(200, {
                "ok": True,
                "quote_error": quote_error,
                "scanned": len(metrics),
                "select": snap.select_count,
                "watch": snap.watch_count,
                "reject": snap.reject_count,
                "top": [{"symbol": r["symbol"], "decision": r["decision"], "score": r["score"], "ltp": r.get("ltp")} for r in snap.rows[:20]],
            })
        except Exception as e:
            LOG.exception("scan failed")
            return self._json(500, {"ok": False, "error": str(e)})

    def _json(self, code, obj):
        raw = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)


def main():
    DESK.mkdir(parents=True, exist_ok=True)
    LOG.info("ASH08 api on :%s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
