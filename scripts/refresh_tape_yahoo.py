#!/usr/bin/env python3
"""Rebuild advisory_snapshot.json from Yahoo 5y daily bars.

Tape as-of becomes the last Yahoo session. T6 DELIV_PER and FII stay the
existing official snapshots — this pass does not invent them.
Never mixes a July bar with a September bar on the same name.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ash08.quotes import fetch_yahoo_daily_many  # noqa: E402

SNAP = ROOT / "ash08" / "data" / "advisory_snapshot.json"
MIN_NAMES = 180


def _load_build():
    path = ROOT / "scripts" / "build_advisory_snapshot.py"
    spec = importlib.util.spec_from_file_location("ash08_build_snap", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load build_advisory_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    old = json.loads(SNAP.read_text(encoding="utf-8"))
    old_names = old.get("names") or []
    symbols = [r["symbol"] for r in old_names if r.get("symbol")]
    if not symbols:
        print("no symbols in existing snapshot", file=sys.stderr)
        return 2
    deliv = {
        r["symbol"]: {"deliv_per": r.get("deliv_per"), "deliv_asof": r.get("deliv_asof")}
        for r in old_names
    }
    print("fetching Yahoo 5y daily for", len(symbols), "names", flush=True)
    packed = fetch_yahoo_daily_many(symbols, range_str="5y", workers=4)
    print("yahoo filled", len(packed), "of", len(symbols), flush=True)
    missing = [s for s in symbols if s not in packed]
    if missing:
        print("yahoo miss", len(missing), missing[:12], flush=True)
    if len(packed) < MIN_NAMES:
        print("abort: would drop below", MIN_NAMES, "names; snapshot not overwritten", file=sys.stderr)
        return 3

    build = _load_build()
    bhav_stub = {}
    names = []
    date_closes = {}
    asofs = []
    for sym, bars in packed.items():
        row = build.feat_one(
            sym,
            bars["dates"],
            bars["opens"],
            bars["highs"],
            bars["lows"],
            bars["closes"],
            bars["vols"],
            bhav_stub,
        )
        if not row:
            continue
        d = deliv.get(sym) or {}
        row["deliv_per"] = build.r2(d.get("deliv_per")) if d.get("deliv_per") is not None else None
        row["deliv_asof"] = d.get("deliv_asof")
        names.append(row)
        asofs.append(row["asof"])
        for dte, c in zip(bars["dates"][-260:], bars["closes"][-260:]):
            date_closes.setdefault(dte, []).append(c)

    if len(names) < MIN_NAMES:
        print("abort after features:", len(names), file=sys.stderr)
        return 3

    asof = max(asofs)
    ranked = [r for r in names if r.get("vol_adj") is not None]
    ranked.sort(key=lambda r: r["vol_adj"], reverse=True)
    rank_map = {r["symbol"]: i + 1 for i, r in enumerate(ranked)}
    for r in names:
        r["rank"] = rank_map.get(r["symbol"])

    above = sum(1 for r in names if r.get("above200"))
    n = len(names)
    breadth = round(100.0 * above / n, 1) if n else 0.0
    dates_sorted = sorted(date_closes)
    ew = build.ew_index(date_closes, dates_sorted)
    nifty = ew[-1] if ew else None
    e20 = build.ema(ew, 20) if ew else None
    e50 = build.ema(ew, 50) if ew else None
    s200 = build.sma(ew, 200) if ew else None
    nifty5d = None
    if ew and len(ew) > 5 and ew[-6] > 0:
        nifty5d = (ew[-1] / ew[-6] - 1.0) * 100.0
    trend = 0.0
    if e20 is not None and nifty is not None and nifty > e20:
        trend += 25
    if e50 is not None and nifty is not None and nifty > e50:
        trend += 25
    trend += min(30.0, (breadth / 100.0) * 30.0)
    if nifty5d is not None:
        trend += max(0.0, min(20.0, nifty5d * 2 + 10))
    trend = round(trend, 1)
    names.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 9999))

    fii = dict(old.get("fii") or {})
    payload = {
        "ok": True,
        "asof": asof,
        "universe_n": n,
        "tape": {
            "files": n,
            "source": "Yahoo Finance v8 daily 5y (query1/query2 chart). Replaces 2026-07-17 Mongo bulk.",
            "bhav": (old.get("tape") or {}).get("bhav") or "sec_bhavdata_full_08062026.csv EQ DELIV_PER",
            "fii": fii.get("source"),
            "yahoo_miss": missing,
        },
        "market": {
            "asof": asof,
            "breadth_pct": breadth,
            "above200": above,
            "universe_n": n,
            "ew_last": build.r2(nifty),
            "ew_ema20": build.r2(e20),
            "ew_ema50": build.r2(e50),
            "ew_sma200": build.r2(s200),
            "ew_above200": bool(s200 is not None and nifty is not None and nifty > s200),
            "ew_5d_pct": build.r4(nifty5d),
            "trend": trend,
            "breadth_ok": breadth > 40.0,
            "trend_ok": trend >= 60.0,
        },
        "fii": fii,
        "data_status": {
            "ohlcv": "CLOSED",
            "ohlcv_note": f"Yahoo 5y daily last bar {asof}. Not the 17 Jul freeze.",
            "t1_sma200": "CLOSED",
            "t2_atr": "CLOSED",
            "t3_min_price": "CLOSED_OFF",
            "t6_delivery": "SNAPSHOT_1D",
            "t6_note": "Official NSE DELIV_PER 08-Jun-2026. No 20-session series. Not a fake 20d avg.",
            "t9_breadth": "CLOSED",
            "m1_voladj": "CLOSED",
            "fii_size": "CLOSED",
            "mcap": "PROXY_N200",
            "mcap_note": "fundamentals_n200 has income/cashflow only — no marketCap, no shares. Nifty 200 membership is the proxy. Not a rupee gate.",
            "vix": "HELD",
            "live_ltp": "MISSING",
        },
        "names": names,
    }
    SNAP.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print("wrote", SNAP, "bytes", SNAP.stat().st_size, "n", n, "asof", asof, "breadth", breadth, "trend", trend)
    print("top5", [(r["symbol"], r["rank"], r["vol_adj"], r["close"]) for r in names[:5]])
    print("bharatforg", next((r["close"] for r in names if r["symbol"] == "BHARATFORG"), None))
    print("t6 with deliv", sum(1 for r in names if r.get("deliv_per") is not None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
