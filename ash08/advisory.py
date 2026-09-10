"""ASH08 advisory — Today's Advice. Not a factor-comparison lab.

Ranks the N200 tape by 6m+12m vol-adj (File 3 / M1), vetoes below 200 DMA,
SELECT in 62–70, size-down at 70+. Quality is low-vol+ADV20, never coverage-100.
Tape close is the reference price. Paper fill still needs live LTP.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from ash08.config import (
    ADV20_MIN,
    BOOK_VALUE,
    CASH_RESERVE_PCT,
    CHITTY_DECISION_IMPACT,
    CORR_MAX,
    DATA_DIR,
    HIGH_SCORE_SIZE_MULT,
    KELLY_MAX_PCT,
    MCAP_MIN_CR,
    MOM_MIN,
    PARAMETER_SET_ID,
    SCORE_SELECT,
    SCORE_SELECT_HIGH,
    SCORE_WATCH,
    STOP_PCT,
    TARGET_PCT,
    MAX_HOLD_SESSIONS,
    TICKER_BLOCKLIST,
    TURNOVER_CR_MIN,
)
from ash08.score import compute_score, quality_from_tape
from ash08.segments import segment_of
from ash08.sizing import kelly_notional

ROOT = Path(__file__).resolve().parents[1]
SNAP_PATH = Path(__file__).resolve().parent / "data" / "advisory_snapshot.json"

M6_RANK_N = 25
T2_ATR_PCT_MAX = 8.0
T9_BREADTH_MIN = 40.0
P8_RSI_LO = 45.0
P8_RSI_HI = 70.0
P8_QUALITY_NEED = 2
P10_TREND_MIN = 60.0
ROC20_MIN = 0.0
VOL_RATIO_MIN = 1.0
BREAKOUT_NEAR = 0.97
OCEAN_SIZE_MULT = 0.5

# Owner lock: ROC20 and the rest stay on the advice path even though the
# telemetry registry still has CHITTY_DECISION_IMPACT=False.
CHITTY_GATES_ON = True


def fii_size_mult(fii_net: Optional[float]) -> tuple[float, str]:
    if fii_net is None:
        return 1.0, "no_fii_print — size unthrottled"
    if fii_net >= 0:
        return 1.0, f"FII +{fii_net:.0f} Cr — full Kelly"
    if fii_net > -2000:
        return 0.70, f"FII {fii_net:.0f} Cr — size ×0.70"
    return 0.50, f"FII {fii_net:.0f} Cr — size ×0.50"


def _snap_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def load_snapshot(path: Optional[Path] = None) -> dict:
    p = Path(path) if path is not None else SNAP_PATH
    return _read_snapshot(str(p.resolve()), _snap_mtime(p))


@lru_cache(maxsize=4)
def _read_snapshot(path_key: str, mtime: float) -> dict:
    p = Path(path_key)
    if not p.exists():
        return {
            "ok": False,
            "error": "advisory_snapshot.json missing",
            "names": [],
            "market": {},
            "fii": {},
            "data_status": {},
        }
    return json.loads(p.read_text())


def _runtime_open_n() -> int:
    """OPEN count from the live runtime file only — not packaged/GitHub fallback."""
    try:
        p = Path(DATA_DIR) / "paper_state.json"
        if not p.exists():
            return 0
        st = json.loads(p.read_text())
        return sum(1 for x in (st.get("positions") or []) if x.get("status") == "OPEN")
    except Exception:
        return 0


def _st(pid: str, status: str, detail: str) -> dict:
    return {"id": pid, "status": status, "detail": detail, "passed": status in ("PASS", "SKIP")}


def evaluate_name(row: dict, market: dict, fii_mult: float, fii_why: str, book: Optional[dict] = None) -> dict:
    sym = str(row.get("symbol") or "").upper()
    close = row.get("close")
    rank = row.get("rank")
    mom6 = row.get("mom6")
    quality = quality_from_tape(row.get("sigma"), row.get("adv20"))
    score = compute_score(row.get("vol_adj"), row.get("sigma"), row.get("adv20"), quality=quality)
    near = score is not None and SCORE_SELECT <= score < SCORE_SELECT_HIGH
    high = score is not None and score >= SCORE_SELECT_HIGH
    in_m6 = rank is not None and rank <= M6_RANK_N
    steps: List[dict] = []

    blocked = sym in TICKER_BLOCKLIST
    steps.append(_st("S0", "FAIL" if blocked else "PASS", "blocklist" if blocked else "N200 tape"))

    steps.append(_st("M1", "UNKNOWN" if row.get("vol_adj") is None else "PASS",
                     f"volAdj={row.get('vol_adj')} rank={rank}"))
    steps.append(_st("M6", "PASS" if in_m6 else "FAIL",
                     f"rank {rank} of {M6_RANK_N}" if in_m6 else f"rank {rank} > {M6_RANK_N}"))

    mom_ok = mom6 is not None and mom6 > MOM_MIN
    steps.append(_st("P-MOM", "UNKNOWN" if mom6 is None else ("PASS" if mom_ok else "FAIL"),
                     f"6m={None if mom6 is None else round(mom6 * 100, 1)}%"))

    t1 = "UNKNOWN" if row.get("sma200") is None else ("PASS" if row.get("above200") else "FAIL")
    steps.append(_st("T1", t1, f"sma200={row.get('sma200')} px={close}"))

    atr = row.get("atr_pct")
    t2 = "UNKNOWN" if atr is None else ("PASS" if atr <= T2_ATR_PCT_MAX else "FAIL")
    steps.append(_st("T2", t2, f"atr={atr}% ≤ {T2_ATR_PCT_MAX}"))

    steps.append(_st("T3", "SKIP", f"px=₹{close} · min-price bar OFF"))

    deliv = row.get("deliv_per")
    if deliv is None:
        t6 = "UNKNOWN"
        t6d = "no NSE bhav print"
    else:
        t6 = "PASS"
        t6d = f"DELIV_PER {deliv}% on {row.get('deliv_asof')} (1-day official, not 20d avg)"
    steps.append(_st("T6", t6, t6d))

    roc20 = row.get("roc20")
    volr = row.get("vol_ratio")
    near20 = row.get("near20h")
    cn018 = "UNKNOWN" if roc20 is None else ("PASS" if roc20 > ROC20_MIN else "FAIL")
    cn022 = "UNKNOWN" if volr is None else ("PASS" if volr >= VOL_RATIO_MIN else "FAIL")
    cn021 = "UNKNOWN" if near20 is None else ("PASS" if near20 >= BREAKOUT_NEAR else "FAIL")
    steps.append(_st("CN-018", cn018, f"ROC20={roc20}"))
    steps.append(_st("CN-022", cn022, f"volRatio={volr}"))
    steps.append(_st("CN-021", cn021, f"vs20h={near20}"))
    chitty_ok = cn018 == "PASS" and cn022 == "PASS" and cn021 == "PASS"
    chitty_reason = "ROC20+vol+breakout" if chitty_ok else ",".join(
        x for x, st in (("CN-018", cn018), ("CN-022", cn022), ("CN-021", cn021)) if st != "PASS"
    )

    breadth_ok = bool(market.get("breadth_ok"))
    trend_ok = bool(market.get("trend_ok"))
    steps.append(_st("T9", "PASS" if breadth_ok else "FAIL",
                     f"breadth={market.get('breadth_pct')}% (>{T9_BREADTH_MIN})"))

    p2 = "UNKNOWN" if row.get("ema20") is None else ("PASS" if row.get("above_ema20") else "FAIL")
    p6 = "UNKNOWN" if row.get("ema50") is None else ("PASS" if row.get("above_ema50") else "FAIL")
    p7 = "UNKNOWN" if row.get("ema200") is None else ("PASS" if row.get("stack") else "FAIL")
    rsi = row.get("rsi14")
    p8 = "UNKNOWN" if rsi is None else ("PASS" if P8_RSI_LO <= rsi <= P8_RSI_HI else "FAIL")
    steps.append(_st("P2", p2, f"ema20={row.get('ema20')}"))
    steps.append(_st("P6", p6, f"ema50={row.get('ema50')}"))
    steps.append(_st("P7", p7, "stack 20>50>200" if p7 == "PASS" else "stack off"))
    steps.append(_st("P8", p8, f"rsi={rsi}"))
    qhits = sum(1 for s in (p2, p6, p7, p8) if s == "PASS")
    qok = qhits >= P8_QUALITY_NEED
    steps.append(_st("P8Q", "PASS" if qok else "FAIL", f"{qhits}/4 of P2·P6·P7·P8"))

    steps.append(_st("P10", "PASS" if trend_ok else "FAIL", f"trend={market.get('trend')}"))
    book = book or {}
    open_n = int(book.get("open_n") or 0)
    if open_n <= 0:
        steps.append(_st("P11", "PASS", "empty book — no damage cluster"))
    else:
        steps.append(_st("P11", "UNKNOWN", f"open book n={open_n} — damage cluster not measured"))

    adv = row.get("adv20")
    adv_ok = adv is not None and adv >= ADV20_MIN
    steps.append(_st("P-ADV20", "UNKNOWN" if adv is None else ("PASS" if adv_ok else "FAIL"), f"adv20={adv}"))
    to = row.get("turnover_cr")
    to_ok = to is not None and to >= TURNOVER_CR_MIN
    steps.append(_st("P-TURNOVER", "UNKNOWN" if to is None else ("PASS" if to_ok else "FAIL"), f"to={to} Cr"))

    corr = (book.get("corr") or {}).get(sym)
    if open_n <= 0:
        steps.append(_st("P-CORR", "SKIP", f"empty book · cap {CORR_MAX}"))
    elif corr is None:
        steps.append(_st("P-CORR", "UNKNOWN", f"open book · no return series · cap {CORR_MAX}"))
    else:
        try:
            c = float(corr)
            steps.append(_st("P-CORR", "PASS" if c <= CORR_MAX else "FAIL", f"corr={c} max={CORR_MAX}"))
        except (TypeError, ValueError):
            steps.append(_st("P-CORR", "UNKNOWN", f"open book · no return series · cap {CORR_MAX}"))
    steps.append(_st("MCAP", "SKIP", f"PROXY_N200 · floor {MCAP_MIN_CR} Cr not measured"))

    kill = blocked or not mom_ok or t1 != "PASS" or t2 != "PASS" or not adv_ok or not to_ok or score is None
    market_stress = not breadth_ok or not trend_ok
    crash_ok = mom_ok and t1 == "PASS" and t2 == "PASS"
    ocean_path = (
        market_stress and crash_ok and in_m6
        and score is not None and score >= SCORE_SELECT
    )
    ocean = False

    action = "AVOID"
    reason = "sequence fail"
    if kill:
        if blocked:
            reason = "blocklist"
        elif score is None:
            reason = "score DATA_NEEDED (need vol_adj and measured quality)"
        elif not mom_ok:
            reason = "6m momentum ≤ 0"
        elif t1 != "PASS":
            reason = "T1 close not above SMA200 — File 3 no SELECT below 200 DMA"
        elif t2 != "PASS":
            reason = f"T2 ATR {atr}% > {T2_ATR_PCT_MAX}"
        elif not adv_ok:
            reason = "ADV20 below 2L"
        else:
            reason = "5d turnover below ₹5 Cr"
        action = "AVOID"
    elif CHITTY_GATES_ON and not chitty_ok:
        action = "WATCH"
        reason = f"score path but {chitty_reason}"
    elif not qok:
        action = "WATCH"
        reason = f"quality pack {qhits}/4 < {P8_QUALITY_NEED}"
    elif market_stress and ocean_path:
        action = "BUY"
        ocean = True
        reason = f"Ocean Mixed · crash-guard on · score {score} · rank {rank}"
    elif market_stress:
        action = "WATCH"
        reason = f"T9/P10 market stress breadth {market.get('breadth_pct')}% trend {market.get('trend')}"
    elif not in_m6:
        action = "WATCH" if score is not None and score >= SCORE_WATCH else "AVOID"
        reason = f"M6 rank {rank} > {M6_RANK_N}"
    elif high:
        action = "BUY"
        reason = f"File 3 70+ size-down {score} · rank {rank}"
    elif score >= SCORE_SELECT:
        action = "BUY"
        reason = f"File 3 BUY band {score} in [{SCORE_SELECT:g},{SCORE_SELECT_HIGH:g}) · rank {rank}"
    elif score >= SCORE_WATCH:
        action = "WATCH"
        reason = f"watch {score} in [{SCORE_WATCH},{SCORE_SELECT})"
    else:
        action = "AVOID"
        reason = f"score {score} < {SCORE_WATCH}"

    size_mult = fii_mult
    if ocean:
        size_mult *= OCEAN_SIZE_MULT
    elif high and action == "BUY":
        size_mult *= HIGH_SCORE_SIZE_MULT
    exposure = 100.0 * size_mult
    notional, diag = kelly_notional(BOOK_VALUE, score, row.get("sigma"), exposure_pct=exposure)
    qty = 0
    if close and close > 0 and notional > 0:
        qty = int(notional // close)
        notional = round(qty * close, 0)
    stop = round(close * (1 - STOP_PCT / 100.0), 2) if close else None
    target = round(close * (1 + TARGET_PCT / 100.0), 2) if close else None

    why = reason
    if action == "BUY":
        why = f"{reason}. {fii_why}. Stop −{STOP_PCT}% / target +{TARGET_PCT}% / {MAX_HOLD_SESSIONS}d."

    return {
        "symbol": sym,
        "segment": segment_of(sym) or "",
        "action": action,
        "decision": {"BUY": "SELECT", "WATCH": "WATCH", "AVOID": "REJECT"}.get(action, "REJECT"),
        "score": score,
        "rank": rank,
        "near_miss": near and action == "BUY",
        "high_score": high and action == "BUY",
        "quality": quality,
        "ocean_mixed": ocean,
        "close": close,
        "asof": row.get("asof"),
        "vol_adj": row.get("vol_adj"),
        "mom6_pct": None if mom6 is None else round(mom6 * 100, 1),
        "mom12_pct": None if row.get("mom12") is None else round(row["mom12"] * 100, 1),
        "atr_pct": atr,
        "roc20": roc20,
        "sma200": row.get("sma200"),
        "deliv_per": deliv,
        "sigma": row.get("sigma"),
        "adv20": adv,
        "turnover_cr": to,
        "quality_hits": qhits,
        "notional": notional if action == "BUY" else 0,
        "qty": qty if action == "BUY" else 0,
        "kelly_pct": diag.get("kelly_pct"),
        "stop": stop,
        "target": target,
        "hold_days": MAX_HOLD_SESSIONS,
        "size_note": fii_why,
        "px_source": f"tape_close_{row.get('asof')}",
        "why": why,
        "steps": steps,
        "engine_a_rank": rank,
        "engine_b_veto": action != "BUY",
    }


def payload(book: Optional[dict] = None) -> dict:
    snap = load_snapshot()
    names = snap.get("names") or []
    market = snap.get("market") or {}
    fii = snap.get("fii") or {}
    fii_mult, fii_why = fii_size_mult(fii.get("fii_net_cr"))
    if book is None:
        book = {"open_n": _runtime_open_n()}
    rows = [evaluate_name(n, market, fii_mult, fii_why, book=book) for n in names]
    buy = [r for r in rows if r["action"] == "BUY"]
    watch = [r for r in rows if r["action"] == "WATCH"]
    avoid = [r for r in rows if r["action"] == "AVOID"]
    buy.sort(key=lambda r: (r.get("rank") or 9999, -r["score"]))
    watch.sort(key=lambda r: (r.get("rank") or 9999, -r["score"]))
    return {
        "ok": True,
        "asof": snap.get("asof"),
        "parameter_set_id": PARAMETER_SET_ID,
        "build": "2026-09-11-file3-62",
        "universe_n": snap.get("universe_n"),
        "buy_n": len(buy),
        "watch_n": len(watch),
        "avoid_n": len(avoid),
        "m6": M6_RANK_N,
        "select": SCORE_SELECT,
        "select_high": SCORE_SELECT_HIGH,
        "watch": SCORE_WATCH,
        "book": BOOK_VALUE,
        "book_open_n": int(book.get("open_n") or 0),
        "cash_reserve_pct": CASH_RESERVE_PCT,
        "stop_pct": STOP_PCT,
        "target_pct": TARGET_PCT,
        "hold_days": MAX_HOLD_SESSIONS,
        "kelly_cap_pct": KELLY_MAX_PCT * 100,
        "chitty_gates": CHITTY_GATES_ON,
        "chitty_registry_flag": CHITTY_DECISION_IMPACT,
        "chitty_note": "ROC20/vol/breakout still gate Today's Advice (CHITTY_GATES_ON). The 31-name telemetry registry stays decision_impact=False.",
        "m1_live": True,
        "m1_note": "File 3: vol-adj 6M+12M score. No SELECT below 200 DMA. BUY 62–70. Size ×0.50 at 70+.",
        "px_note": "Session last is Upstox. After hours Yahoo. Tape close is not a fill.",
        "market": market,
        "fii": {**fii, "size_mult": fii_mult, "size_why": fii_why},
        "data_status": snap.get("data_status") or {},
        "tape": snap.get("tape") or {},
        "buy": buy,
        "watch": watch[:40],
        "avoid": avoid[:20],
        "rows": rows,
    }
