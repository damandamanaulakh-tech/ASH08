"""ASH08 API — must be the Render Start Command: python api.py"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ash08.api")
DESK = ROOT / "desk"
PORT = int(os.environ.get("PORT", "10000"))


def _safe_import():
    mods = {}
    try:
        from ash08.supabase_store import SupabaseStore
        mods["SupabaseStore"] = SupabaseStore
    except Exception as e:
        LOG.error("import SupabaseStore: %s", e)
    try:
        from ash08.upstox_client import (
            fetch_nse_equity_instruments,
            fetch_quotes,
            user_profile,
        )
        mods["fetch_nse"] = fetch_nse_equity_instruments
        mods["fetch_quotes"] = fetch_quotes
        mods["user_profile"] = user_profile
    except Exception as e:
        LOG.error("import upstox_client: %s", e)
    try:
        from ash08.universe import InstrumentRow, UniverseManager
        mods["InstrumentRow"] = InstrumentRow
        mods["UniverseManager"] = UniverseManager
    except Exception as e:
        LOG.error("import universe: %s", e)
    try:
        from ash08.scanner import StockMetrics, run_scan
        mods["StockMetrics"] = StockMetrics
        mods["run_scan"] = run_scan
    except Exception as e:
        LOG.error("import scanner: %s", e)
    return mods


MODS = _safe_import()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DESK if DESK.exists() else ROOT), **kwargs)

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path
        # normalize
        if path.endswith("/") and path != "/":
            path = path.rstrip("/")
        if path == "/api/health":
            return self._health()
        if path == "/api/upstox/profile":
            return self._profile()
        if path == "/api/universe/refresh":
            return self._universe_refresh()
        if path == "/api/universe/core":
            return self._universe_core()
        if path == "/api/scan/latest":
            return self._scan_latest()
        if path == "/api/scan/run":
            return self._scan_run()
        if path in ("/", ""):
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)

    def _health(self):
        store_info = {"supabase_configured": False}
        if "SupabaseStore" in MODS:
            try:
                store_info = MODS["SupabaseStore"]().health()
            except Exception as e:
                store_info = {"error": str(e)}
        return self._json(200, {
            "ok": True,
            "service": "ash08-desk",
            "api": "python api.py",
            "modules_loaded": sorted(MODS.keys()),
            "upstox_token_set": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
            "supabase_url_set": bool(os.environ.get("SUPABASE_URL")),
            "store": store_info,
        })

    def _profile(self):
        if "user_profile" not in MODS:
            return self._json(500, {"ok": False, "error": "upstox_client not loaded"})
        try:
            return self._json(200, {"ok": True, "profile": MODS["user_profile"]()})
        except Exception as e:
            return self._json(401, {"ok": False, "error": str(e)})

    def _universe_core(self):
        if "SupabaseStore" not in MODS:
            return self._json(500, {"ok": False, "error": "store not loaded"})
        data = MODS["SupabaseStore"]().load_universe("core")
        return self._json(200, data or {"count": 0, "symbols": []})

    def _scan_latest(self):
        if "SupabaseStore" not in MODS:
            return self._json(500, {"ok": False, "error": "store not loaded"})
        return self._json(200, MODS["SupabaseStore"]().load_scan() or {"rows": []})

    def _universe_refresh(self):
        need = ["fetch_nse", "InstrumentRow", "UniverseManager", "SupabaseStore"]
        missing = [k for k in need if k not in MODS]
        if missing:
            return self._json(500, {"ok": False, "error": "missing modules", "missing": missing})
        try:
            instruments = MODS["fetch_nse"]()
            Row = MODS["InstrumentRow"]
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
            mgr = MODS["UniverseManager"](data_dir="ash08_data")
            result = mgr.rebuild_from_rows(rows)
            store = MODS["SupabaseStore"]()
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
            LOG.exception("universe refresh")
            return self._json(500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]})

    def _scan_run(self):
        need = ["SupabaseStore", "StockMetrics", "run_scan"]
        missing = [k for k in need if k not in MODS]
        if missing:
            return self._json(500, {"ok": False, "error": "missing modules", "missing": missing})
        try:
            store = MODS["SupabaseStore"]()
            core = store.load_universe("core")
            if not core or not core.get("symbols"):
                return self._json(400, {"ok": False, "error": "Core empty — open /api/universe/refresh first"})
            symbols = core["symbols"]
            rows_meta = {r.get("symbol"): r for r in (core.get("rows") or []) if r.get("symbol")}
            keys = [(rows_meta.get(s) or {}).get("instrument_key") or f"NSE_EQ|{s}" for s in symbols]
            quotes, quote_error = {}, None
            if "fetch_quotes" in MODS:
                try:
                    quotes = MODS["fetch_quotes"](keys[:200])
                except Exception as e:
                    quote_error = str(e)
            metrics = []
            SM = MODS["StockMetrics"]
            for s in symbols[:200]:
                meta = rows_meta.get(s) or {}
                key = meta.get("instrument_key") or f"NSE_EQ|{s}"
                q = quotes.get(key) or quotes.get(s) or {}
                last = None
                if isinstance(q, dict):
                    last = q.get("last_price") or (q.get("ohlc") or {}).get("close")
                metrics.append(SM(
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
            return self._json(200, {
                "ok": True,
                "quote_error": quote_error,
                "scanned": len(metrics),
                "select": snap.select_count,
                "watch": snap.watch_count,
                "reject": snap.reject_count,
                "top": [
                    {"symbol": r["symbol"], "decision": r["decision"], "score": r["score"], "ltp": r.get("ltp")}
                    for r in snap.rows[:20]
                ],
            })
        except Exception as e:
            LOG.exception("scan")
            return self._json(500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]})

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
    LOG.info("ASH08 api starting on 0.0.0.0:%s modules=%s", PORT, sorted(MODS.keys()))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
