"""G2 metrics from real bars. Missing field stays None — scanner maps that to UNKNOWN."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ash08.config import (
    ADV_WINDOW,
    CORR_MIN_OVERLAP,
    METRICS_POLICY_ID,
    MOM_LOOKBACK_CAL_DAYS,
    MOM_MIN_SPAN_DAYS,
    TURNOVER_WINDOW,
)
from ash08.history import HistoryStore, normalize_bars
from ash08.scanner import StockMetrics
from ash08.score import quality_from_tape
from ash08.sizing import annual_vol


def _date(s: str):
    return datetime.fromisoformat(s[:10]).date()


def _vol_adj(closes: Sequence[float]) -> Optional[float]:
    n = len(closes)
    if n < 253:
        return None
    last = float(closes[-1])
    p6 = float(closes[-127])
    p12 = float(closes[-253])
    if last <= 0 or p6 <= 0 or p12 <= 0:
        return None
    r6 = last / p6 - 1.0
    r12 = last / p12 - 1.0

    def ann(window: int) -> Optional[float]:
        sl = [float(x) for x in closes[-window:]]
        rets = []
        for i in range(1, len(sl)):
            if sl[i - 1] > 0:
                rets.append(sl[i] / sl[i - 1] - 1.0)
        if len(rets) < 20:
            return None
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        if var <= 0:
            return None
        return math.sqrt(var) * math.sqrt(252)

    v6, v12 = ann(126), ann(252)
    if not v6 or not v12:
        return None
    return (r6 / v6 + r12 / v12) / 2.0


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < CORR_MIN_OVERLAP or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def daily_returns(bars: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    out = {}
    prev = None
    for row in bars:
        close = float(row["close"])
        if prev and prev > 0:
            out[row["date"]] = close / prev - 1.0
        prev = close
    return out


def metrics_from_bars(
    symbol: str,
    bars: Sequence[Dict[str, Any]],
    *,
    ltp: Optional[float] = None,
    asof: Optional[datetime] = None,
    book_returns: Optional[Dict[str, Dict[str, float]]] = None,
) -> StockMetrics:
    rows = normalize_bars(bars)
    now = asof or datetime.now(timezone.utc)
    today = now.date()
    if not rows:
        return StockMetrics(symbol=symbol, ltp=ltp)

    last = rows[-1]
    last_d = _date(last["date"])
    stale = float((today - last_d).days)

    adv20 = None
    if len(rows) >= ADV_WINDOW:
        adv20 = sum(float(r["volume"]) for r in rows[-ADV_WINDOW:]) / ADV_WINDOW

    turnover = None
    if len(rows) >= TURNOVER_WINDOW:
        turnover = sum(float(r["close"]) * float(r["volume"]) for r in rows[-TURNOVER_WINDOW:]) / 1e7

    mom = None
    cutoff = last_d.toordinal() - MOM_LOOKBACK_CAL_DAYS
    window = [r for r in rows if _date(r["date"]).toordinal() >= cutoff]
    if window:
        first_d = _date(window[0]["date"])
        span = (last_d - first_d).days
        if span >= MOM_MIN_SPAN_DAYS and float(window[0]["close"]) > 0:
            mom = float(window[-1]["close"]) / float(window[0]["close"]) - 1.0

    closes = [float(r["close"]) for r in rows]
    sigma = annual_vol(closes)
    quality = quality_from_tape(sigma, adv20)
    vol_adj = _vol_adj(closes)

    max_corr = None
    corr_applicable = True
    if not book_returns:
        corr_applicable = False
    else:
        self_ret = daily_returns(rows)
        corrs = []
        for other, series in book_returns.items():
            if other == symbol.upper():
                continue
            overlap = sorted(set(self_ret).intersection(series))
            if len(overlap) < CORR_MIN_OVERLAP:
                continue
            c = _pearson([self_ret[d] for d in overlap], [series[d] for d in overlap])
            if c is not None:
                corrs.append(abs(c))
        if corrs:
            max_corr = max(corrs)
        elif any(k != symbol.upper() for k in book_returns):
            max_corr = None
            corr_applicable = True
        else:
            corr_applicable = False

    m = StockMetrics(
        symbol=symbol.upper(),
        adv20=adv20,
        turnover_cr_5d=turnover,
        stale_days=stale,
        mom_6m=mom,
        quality_score=quality,
        max_corr_vs_book=0.0 if not corr_applicable else max_corr,
        ltp=ltp,
        vol_sigma=sigma,
        vol_adj=vol_adj,
    )
    return m


def build_metrics_for_core(
    symbols: Sequence[str],
    data_dir: str,
    quotes: Optional[Dict[str, float]] = None,
    open_symbols: Optional[Iterable[str]] = None,
) -> List[StockMetrics]:
    store = HistoryStore(data_dir)
    quotes = quotes or {}
    opens = [str(s).upper() for s in (open_symbols or []) if s]
    book_returns: Dict[str, Dict[str, float]] = {}
    for osym in opens:
        bars = store.load(osym)
        if bars:
            book_returns[osym] = daily_returns(bars)
    out = []
    for raw in symbols:
        sym = str(raw).upper()
        bars = store.load(sym)
        ltp = quotes.get(sym)
        try:
            ltp = float(ltp) if ltp is not None else None
        except Exception:
            ltp = None
        out.append(metrics_from_bars(
            sym, bars, ltp=ltp, book_returns=book_returns if opens else None,
        ))
    return out


def metrics_policy() -> str:
    return METRICS_POLICY_ID
