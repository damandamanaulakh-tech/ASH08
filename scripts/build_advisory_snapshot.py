#!/usr/bin/env python3
"""Build compact advisory snapshot from ASH08 release tape + bhav + FII.

Does not commit the 25MB CSVs. Output is ash08/data/advisory_snapshot.json.
Tape last bar is the as-of. Never invents mcap.
"""
from __future__ import annotations

import csv
import json
import math
import os
from glob import glob
from pathlib import Path

N200 = Path(os.environ.get("ASH08_N200") or "/tmp/ash08-data/n200")
BHAV = Path(os.environ.get("ASH08_BHAV") or "/tmp/ash08-data/sec_bhavdata_full_08062026.csv")
FII = Path(os.environ.get("ASH08_FII") or "/tmp/ash08-data/fii_dii_net_recent.csv")
OUT = Path(os.environ.get("ASH08_SNAP_OUT") or Path(__file__).resolve().parents[1] / "ash08/data/advisory_snapshot.json")

YAHOO_TO_NSE = {
    "M.M": "M&M",
    "M.MFIN": "M&MFIN",
}
LOOK6 = 126
LOOK12 = 252
TRADING_DAYS = 252.0


def _f(x):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        return None
    return None


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema(vals, n):
    if len(vals) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / n
    for x in vals[n:]:
        e = x * k + e * (1 - k)
    return e


def mom(closes, lb):
    if len(closes) < lb + 1:
        return None
    b = closes[-1 - lb]
    if b <= 0:
        return None
    return closes[-1] / b - 1.0


def roc_pct(closes, lb):
    m = mom(closes, lb)
    return None if m is None else m * 100.0


def annual_vol(closes, days=63):
    if len(closes) < 21:
        return None
    window = closes[-(days + 1) :]
    rets = []
    for i in range(1, len(window)):
        prev = window[i - 1]
        if prev <= 0:
            continue
        rets.append(window[i] / prev - 1.0)
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def atr_pct(highs, lows, closes):
    if len(closes) < 15:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sum(trs[-14:]) / 14.0
    if closes[-1] <= 0:
        return None
    return (atr / closes[-1]) * 100.0


def rsi14(closes):
    if len(closes) < 15:
        return None
    gain = loss = 0.0
    for i in range(len(closes) - 14, len(closes)):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gain += d
        else:
            loss -= d
    ag, al = gain / 14.0, loss / 14.0
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def r4(x):
    if x is None:
        return None
    return round(float(x), 4)


def r2(x):
    if x is None:
        return None
    return round(float(x), 2)


def load_bhav(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            rec = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            if rec.get("SERIES") != "EQ":
                continue
            sym = rec.get("SYMBOL") or ""
            out[sym] = {
                "date": rec.get("DATE1"),
                "close": _f(rec.get("CLOSE_PRICE")),
                "deliv_per": _f(rec.get("DELIV_PER")),
                "deliv_qty": _f(rec.get("DELIV_QTY")),
                "qty": _f(rec.get("TTL_TRD_QNTY")),
                "turnover_lacs": _f(rec.get("TURNOVER_LACS")),
            }
    return out


def load_fii(path: Path) -> dict:
    rows = []
    if path.exists():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                d = (row.get("date") or "").strip()
                fn = _f(row.get("fii_net_cr"))
                dn = _f(row.get("dii_net_cr"))
                if d and fn is not None:
                    rows.append({"date": d, "fii_net_cr": fn, "dii_net_cr": dn})
    last = rows[-1] if rows else {}
    last5 = rows[-5:] if rows else []
    fii5 = sum(r["fii_net_cr"] for r in last5) if last5 else None
    dii5 = sum((r["dii_net_cr"] or 0) for r in last5) if last5 else None
    return {
        "asof": last.get("date"),
        "fii_net_cr": last.get("fii_net_cr"),
        "dii_net_cr": last.get("dii_net_cr"),
        "fii_5d_cr": r2(fii5),
        "dii_5d_cr": r2(dii5),
        "rows": len(rows),
        "source": "AM07 data/fii_dii_net_recent.csv (read-only copy)",
    }


def load_bars(path: Path):
    dates, opens, highs, lows, closes, vols = [], [], [], [], [], []
    with path.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            d = (row.get("Date") or "").strip()
            o, h, l, c, v = _f(row.get("Open")), _f(row.get("High")), _f(row.get("Low")), _f(row.get("Close")), _f(row.get("Volume"))
            if not d or c is None or c <= 0:
                continue
            dates.append(d)
            opens.append(o if o is not None else c)
            highs.append(h if h is not None else c)
            lows.append(l if l is not None else c)
            closes.append(c)
            vols.append(v if v is not None else 0.0)
    return dates, opens, highs, lows, closes, vols


def feat_one(sym, dates, opens, highs, lows, closes, vols, bhav):
    n = len(closes)
    if n < 60:
        return None
    last = closes[-1]
    s200 = sma(closes, 200)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    m6 = mom(closes, LOOK6)
    m12 = mom(closes, LOOK12)
    sig = annual_vol(closes, 63)
    vol_adj = None
    if sig and sig > 0 and m6 is not None:
        a = m6 / sig
        b = (m12 / sig) if m12 is not None else a
        vol_adj = 0.5 * a + 0.5 * b
    adv20 = sum(vols[-20:]) / min(20, n)
    t5 = list(range(max(0, n - 5), n))
    turnover_cr = sum(closes[i] * vols[i] for i in t5) / 1e7
    roc20 = roc_pct(closes, 20)
    vol_ratio = None
    if n >= 21:
        avg = sum(vols[-21:-1]) / 20.0
        vol_ratio = (vols[-1] / avg) if avg > 0 else None
    near20 = None
    if n >= 21:
        prior_high = max(highs[-21:-1])
        near20 = (last / prior_high) if prior_high > 0 else None
    bh = bhav.get(sym) or {}
    quality = min(100.0, n / 126.0 * 100.0)
    return {
        "symbol": sym,
        "asof": dates[-1],
        "bars": n,
        "close": r2(last),
        "sma200": r2(s200),
        "ema20": r2(e20),
        "ema50": r2(e50),
        "ema200": r2(e200),
        "above200": bool(s200 is not None and last > s200),
        "above_ema20": bool(e20 is not None and last > e20),
        "above_ema50": bool(e50 is not None and last > e50),
        "stack": bool(e20 is not None and e50 is not None and e200 is not None and e20 > e50 > e200),
        "mom6": r4(m6),
        "mom12": r4(m12),
        "vol_adj": r4(vol_adj),
        "sigma": r4(sig),
        "atr_pct": r4(atr_pct(highs, lows, closes)),
        "rsi14": r2(rsi14(closes)),
        "roc20": r4(roc20),
        "vol_ratio": r4(vol_ratio),
        "near20h": r4(near20),
        "adv20": r2(adv20),
        "turnover_cr": r2(turnover_cr),
        "quality": r2(quality),
        "deliv_per": r2(bh.get("deliv_per")),
        "deliv_asof": bh.get("date"),
        "first": dates[0],
    }


def ew_index(by_date_closes: dict, dates_sorted):
    out = []
    for d in dates_sorted:
        xs = by_date_closes.get(d) or []
        if xs:
            out.append(sum(xs) / len(xs))
    return out


def main():
    bhav = load_bhav(BHAV)
    fii = load_fii(FII)
    files = sorted(glob(str(N200 / "*.NS.csv")))
    names = []
    date_closes = {}
    asofs = []
    for fp in files:
        p = Path(fp)
        sym = p.name.replace(".NS.csv", "").replace(".", "").upper()
        # M.M.NS.csv → MM? Keep NSE ticker: M&M is M.M in yahoo. Map later.
        stem = p.name[: -len(".NS.csv")]
        sym = YAHOO_TO_NSE.get(stem, stem)
        dates, opens, highs, lows, closes, vols = load_bars(p)
        if len(closes) < 60:
            continue
        row = feat_one(sym, dates, opens, highs, lows, closes, vols, bhav)
        if not row:
            continue
        names.append(row)
        asofs.append(dates[-1])
        for d, c in zip(dates[-260:], closes[-260:]):
            date_closes.setdefault(d, []).append(c)

    asof = max(asofs) if asofs else None
    ranked = [r for r in names if r.get("vol_adj") is not None]
    ranked.sort(key=lambda r: r["vol_adj"], reverse=True)
    rank_map = {r["symbol"]: i + 1 for i, r in enumerate(ranked)}
    for r in names:
        r["rank"] = rank_map.get(r["symbol"])

    above = sum(1 for r in names if r.get("above200"))
    n = len(names)
    breadth = round(100.0 * above / n, 1) if n else 0.0
    dates_sorted = sorted(date_closes)
    ew = ew_index(date_closes, dates_sorted)
    nifty = ew[-1] if ew else None
    e20 = ema(ew, 20) if ew else None
    e50 = ema(ew, 50) if ew else None
    s200 = sma(ew, 200) if ew else None
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

    payload = {
        "ok": True,
        "asof": asof,
        "universe_n": n,
        "tape": {
            "files": len(files),
            "source": "ASH08 release FIIDII30000stocksdata *.NS.csv (Mongo/Upstox bulk)",
            "bhav": "sec_bhavdata_full_08062026.csv EQ DELIV_PER",
            "fii": fii.get("source"),
        },
        "market": {
            "asof": asof,
            "breadth_pct": breadth,
            "above200": above,
            "universe_n": n,
            "ew_last": r2(nifty),
            "ew_ema20": r2(e20),
            "ew_ema50": r2(e50),
            "ew_sma200": r2(s200),
            "ew_above200": bool(s200 is not None and nifty is not None and nifty > s200),
            "ew_5d_pct": r4(nifty5d),
            "trend": trend,
            "breadth_ok": breadth > 40.0,
            "trend_ok": trend >= 60.0,
        },
        "fii": fii,
        "data_status": {
            "ohlcv": "CLOSED",
            "t1_sma200": "CLOSED",
            "t2_atr": "CLOSED",
            "t3_min_price": "CLOSED_OFF",
            "t6_delivery": "SNAPSHOT_1D",
            "t6_note": "Official NSE DELIV_PER 08-Jun-2026. No 20-session series in the release. Not a fake 20d avg.",
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print("wrote", OUT, "bytes", OUT.stat().st_size, "n", n, "asof", asof, "breadth", breadth, "trend", trend)
    print("top5", [(r["symbol"], r["rank"], r["vol_adj"], r["close"]) for r in names[:5]])
    print("fii", fii.get("asof"), fii.get("fii_net_cr"))
    print("t6 with deliv", sum(1 for r in names if r.get("deliv_per") is not None))


if __name__ == "__main__":
    main()
