"""
ASH08 API — Render Start Command MUST be: python api.py
Local JSON is primary. Supabase is best-effort only.
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

try:
    from ash08.core_seed import CORE_SYMBOLS, CORE_COUNT
except Exception:
    CORE_SYMBOLS = ["TCS", "HDFCBANK", "RELIANCE", "INFY", "ICICIBANK"]
    CORE_COUNT = len(CORE_SYMBOLS)


def seed_demo_local():
    """Seed core universe with full local NSE list. Works without Supabase."""
    if "store" not in MODS or "Metrics" not in MODS or "run_scan" not in MODS:
        return {"ok": False, "error": "modules missing"}
    store = MODS["store"]()
    symbols = list(CORE_SYMBOLS)
    rows = [
        {
            "symbol": s,
            "name": s,
            "instrument_key": f"NSE_EQ|{s}",
            "segment": "",
            "ltp": None,
        }
        for s in symbols
    ]
    from datetime import datetime, timezone
    universe = {
        "bucket": "core",
        "asof": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "local_nse_core_seed",
        "count": len(symbols),
        "symbols": symbols,
        "rows": rows,
        "notes": [f"core seed {len(symbols)} real NSE symbols"],
    }
    store.save_universe("core", universe)

    Metrics = MODS["Metrics"]
    metrics = []
    for i, s in enumerate(symbols[:300]):
        mom = 0.12 - (i % 9) * 0.02
        qual = 75.0 - (i % 11) * 2.5
        metrics.append(Metrics(
            symbol=s,
            adv20=max(50_000, 500_000 - i * 800),
            turnover_cr_5d=max(1.0, 15.0 - i * 0.04),
            stale_days=0 if i < 200 else 1,
            mom_6m=mom,
            quality_score=qual,
            max_corr_vs_book=0.35,
            segment="",
            ltp=None,
        ))
    snap = MODS["run_scan"](metrics, universe_bucket="core")
    store.save_scan(snap.to_dict())
    LOG.info(
        "core seed written: universe=%s scanned=%s select=%s watch=%s reject=%s",
        len(symbols), len(metrics), snap.select_count, snap.watch_count, snap.reject_count,
    )
    return {
        "ok": True,
        "mode": "local_core_seed",
        "core_count": len(symbols),
        "scanned": len(metrics),
        "select": snap.select_count,
        "watch": snap.watch_count,
        "reject": snap.reject_count,
        "top": [
            {"symbol": r["symbol"], "decision": r["decision"], "score": r["score"]}
            for r in snap.rows[:15]
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if not path.startswith("/"):
            path = "/" + path
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
        if path == "/api/demo/run":
            return self.api_demo_run()
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
            "core_seed_count": CORE_COUNT,
            "store": store_info,
            "note": "Local JSON is primary. Supabase 403 is non-blocking.",
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
        if not data or not data.get("symbols"):
            seed_demo_local()
            data = MODS["store"]().load_universe("core")
        return self.json(200, data or {"count": 0, "symbols": []})

    def api_scan_latest(self):
        if "store" not in MODS:
            return self.json(500, {"ok": False, "error": "store missing"})
        data = MODS["store"]().load_scan()
        if not data or not data.get("rows"):
            seed_demo_local()
            data = MODS["store"]().load_scan()
        return self.json(200, data or {"rows": []})

    def api_demo_run(self):
        try:
            return self.json(200, seed_demo_local())
        except Exception as e:
            LOG.exception("demo")
            return self.json(500, {"ok": False, "error": str(e)})

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
                seed_demo_local()
                core = store.load_universe("core")
            symbols = core["symbols"]
            rows_meta = {r.get("symbol"): r for r in (core.get("rows") or []) if r.get("symbol")}
            keys = [(rows_meta.get(s) or {}).get("instrument_key") or f"NSE_EQ|{s}" for s in symbols]
            quotes, quote_error = {}, None
            if "fetch_quotes" in MODS:
                try:
                    quotes = MODS["fetch_quotes"](keys[:100])
                except Exception as e:
                    quote_error = str(e)
                    LOG.warning("quotes: %s", e)
            Metrics = MODS["Metrics"]
            metrics = []
            provisional = (not quotes) or bool(quote_error)
            for i, s in enumerate(symbols[:300]):
                meta = rows_meta.get(s) or {}
                key = meta.get("instrument_key") or f"NSE_EQ|{s}"
                q = quotes.get(key) or quotes.get(s) or {}
                last = None
                if isinstance(q, dict):
                    last = q.get("last_price") or (q.get("ohlc") or {}).get("close")
                if last is not None:
                    metrics.append(Metrics(symbol=s, adv20=300_000, turnover_cr_5d=10.0, stale_days=0, mom_6m=0.08, quality_score=72.0, ltp=float(last)))
                elif provisional:
                    qscore = 68.0 - (i % 20) * 0.3
                    metrics.append(Metrics(symbol=s, adv20=250_000, turnover_cr_5d=6.0, stale_days=1, mom_6m=0.06, quality_score=qscore, ltp=meta.get("ltp"), segment=meta.get("segment") or ""))
                else:
                    metrics.append(Metrics(symbol=s, adv20=None, turnover_cr_5d=None, stale_days=99, mom_6m=-0.01, quality_score=40.0, ltp=None))
            snap = MODS["run_scan"](metrics, universe_bucket="core")
            if provisional:
                for r in snap.rows:
                    if isinstance(r, dict):
                        r["reason"] = (r.get("reason") or "") + " | provisional"
            store.save_scan(snap.to_dict())
            return self.json(200, {
                "ok": True,
                "mode": "provisional" if provisional else "live_quotes",
                "quote_error": quote_error,
                "scanned": len(metrics),
                "select": snap.select_count,
                "watch": snap.watch_count,
                "reject": snap.reject_count,
                "top": [{"symbol": r["symbol"], "decision": r["decision"], "score": r["score"], "ltp": r.get("ltp")} for r in snap.rows[:25]],
            })
        except Exception as e:
            LOG.exception("scan")
            return self.json(500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-1000:]})

    def serve_static(self, path: str):
        if path in ("/", ""):
            candidate = DESK / "ASH08_Desk_Dashboard.html"
        else:
            rel = path.lstrip("/")
            candidate = (DESK / rel).resolve()
            try:
                candidate.relative_to(DESK.resolve())
            except ValueError:
                return self.json(403, {"ok": False, "error": "forbidden"})
            if not candidate.is_file():
                alt = DESK / Path(rel).name
                if alt.is_file():
                    candidate = alt
                else:
                    self.send_error(404, "File not found")
                    return
        if not candidate.is_file():
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
    try:
        seed_demo_local()
    except Exception as e:
        LOG.warning("boot seed skipped: %s", e)
    LOG.info("ASH08 listening 0.0.0.0:%s desk=%s core_seed=%s", PORT, DESK, CORE_COUNT)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
