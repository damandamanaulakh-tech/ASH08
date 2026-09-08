"""G3 piano: last scan, one param_id → passed / failed / UNKNOWN names. No demo fill."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ash08.config import (
    ADV20_MIN,
    CORR_MAX,
    MOM_MIN,
    SCORE_NEAR_MISS,
    SCORE_SELECT,
    STALE_MAX_DAYS,
    TURNOVER_CR_MIN,
)
from ash08.scanner import SCAN_GATES

ALIASES = {
    "adv": "P-ADV20",
    "turn": "P-TURNOVER",
    "turnover": "P-TURNOVER",
    "stale": "P-STALE",
    "mom": "P-MOM",
    "score": "P-SCORE",
    "corr": "P-CORR",
    "select": "P-SELECT",
    "near": "P-NEAR_MISS",
    "nearmiss": "P-NEAR_MISS",
    "order": "P-ORDER",
    "gov": "P-GOV",
}

DEFINITIONS = {
    "P-ADV20": f"ADV20 ≥ {int(ADV20_MIN):,}",
    "P-TURNOVER": f"5D turnover ≥ ₹{TURNOVER_CR_MIN:g} Cr",
    "P-STALE": f"stale ≤ {int(STALE_MAX_DAYS)} days",
    "P-MOM": f"6M momentum > {MOM_MIN:g}",
    "P-SCORE": "0.65×mom + 0.35×quality (measured)",
    "P-CORR": f"max |corr| vs open book ≤ {CORR_MAX}",
    "P-SELECT": f"hard pass and score ≥ {SCORE_SELECT:g}",
    "P-NEAR_MISS": f"hard pass and {SCORE_NEAR_MISS:g} ≤ score < {SCORE_SELECT:g} — live buy gate",
    "P-ORDER": "NSE bulk/block/buyback: net buy PASS, net sell FAIL (blocks), none UNKNOWN (not blocking)",
    "P-GOV": "book-level L0–L4 exposure — not a name gate",
}


def normalize_param(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return "P-ADV20"
    low = str(raw or "").strip().lower()
    if low in ALIASES:
        return ALIASES[low]
    if not text.startswith("P-"):
        text = "P-" + text
    return text


def hit_status(hit: Optional[dict]) -> str:
    if not hit:
        return "UNKNOWN"
    status = str(hit.get("status") or "").upper()
    if status in ("PASS", "FAIL", "UNKNOWN"):
        return status
    detail = str(hit.get("detail") or "").upper()
    if "UNKNOWN" in detail or "MISSING" in detail:
        return "UNKNOWN"
    return "PASS" if hit.get("passed") else "FAIL"


def _item(row: dict, hit: Optional[dict]) -> dict:
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "decision": row.get("decision"),
        "score": row.get("score"),
        "detail": (hit or {}).get("detail") or "",
    }


def piano_from_scan(scan: Optional[dict], param_id: str, governor: Optional[dict] = None) -> dict:
    pid = normalize_param(param_id)
    rows = list((scan or {}).get("rows") or [])
    passed: List[dict] = []
    failed: List[dict] = []
    unknown: List[dict] = []

    if pid == "P-GOV":
        return {
            "ok": True,
            "param_id": pid,
            "definition": DEFINITIONS[pid],
            "passed": [],
            "failed": [],
            "unknown": [],
            "counts": {"passed": 0, "failed": 0, "unknown": 0, "n": 0},
            "empty_scan": not rows,
            "note": "governor is book-level, not a per-name gate",
            "governor": governor or {},
        }

    if not rows:
        return {
            "ok": True,
            "param_id": pid,
            "definition": DEFINITIONS.get(pid, pid),
            "passed": [],
            "failed": [],
            "unknown": [],
            "counts": {"passed": 0, "failed": 0, "unknown": 0, "n": 0},
            "empty_scan": True,
            "note": "empty scan — no names",
        }

    for row in rows:
        hits = row.get("hits") or []
        hit = next((h for h in hits if str(h.get("param_id") or "").upper() == pid), None)
        status = hit_status(hit)
        item = _item(row, hit)
        if status == "PASS":
            passed.append(item)
        elif status == "FAIL":
            failed.append(item)
        else:
            unknown.append(item)

    return {
        "ok": True,
        "param_id": pid,
        "definition": DEFINITIONS.get(pid, pid),
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "counts": {
            "passed": len(passed),
            "failed": len(failed),
            "unknown": len(unknown),
            "n": len(rows),
        },
        "empty_scan": False,
        "asof": (scan or {}).get("asof"),
    }


def piano_summary(scan: Optional[dict]) -> dict:
    rows = list((scan or {}).get("rows") or [])
    gates = {}
    for pid in SCAN_GATES:
        bucket = piano_from_scan(scan, pid)
        gates[pid] = bucket["counts"]
    gates["P-GOV"] = {"passed": 0, "failed": 0, "unknown": 0, "n": 0}
    return {
        "ok": True,
        "empty_scan": not rows,
        "asof": (scan or {}).get("asof"),
        "n": len(rows),
        "gates": gates,
    }
