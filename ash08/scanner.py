"""ASH08 Scanner - locked gates SELECT / WATCH / REJECT. Numbers from ash08.config."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ash08.config import (
    ADV20_MIN,
    CORR_MAX,
    MOM_MIN,
    MOM_WEIGHT,
    QUAL_WEIGHT,
    SCORE_SELECT,
    SCORE_WATCH,
    STALE_MAX_DAYS,
    TURNOVER_CR_MIN,
)

LOG = logging.getLogger("ash08.scanner")


@dataclass
class StockMetrics:
    symbol: str
    adv20: Optional[float] = None
    turnover_cr_5d: Optional[float] = None
    stale_days: Optional[float] = None
    mom_6m: Optional[float] = None
    quality_score: Optional[float] = None
    max_corr_vs_book: Optional[float] = None
    segment: str = ""
    ltp: Optional[float] = None


@dataclass
class ParamHit:
    param_id: str
    passed: bool
    detail: str
    status: str = "FAIL"  # PASS | FAIL | UNKNOWN


@dataclass
class ScanRow:
    symbol: str
    decision: str
    score: float
    segment: str = ""
    ltp: Optional[float] = None
    reason: str = ""
    hits: List[ParamHit] = field(default_factory=list)
    hard_pass: bool = False
    coverage: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScanSnapshot:
    asof: str
    universe_bucket: str
    universe_count: int
    select_count: int
    watch_count: int
    reject_count: int
    unknown_count: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mom_return_to_score(m: float) -> float:
    return max(0.0, min(100.0, 50.0 + m * 200.0))


def compute_final_score(mom_6m, quality_score):
    mom_s = 50.0 if mom_6m is None else mom_return_to_score(mom_6m)
    qual = 50.0 if quality_score is None else max(0.0, min(100.0, float(quality_score)))
    return round(MOM_WEIGHT * mom_s + QUAL_WEIGHT * qual, 2)


MANDATORY = ("adv20", "turnover_cr_5d", "stale_days", "mom_6m", "quality_score")
SCAN_GATES = ("P-ADV20", "P-TURNOVER", "P-STALE", "P-MOM", "P-CORR", "P-SCORE", "P-SELECT")


def evaluate_stock(m: StockMetrics) -> ScanRow:
    hits: List[ParamHit] = []

    def add(pid: str, status: str, detail: str) -> None:
        hits.append(ParamHit(pid, status == "PASS", detail, status))

    def field(pid: str, value, ok: bool, detail: str) -> Optional[bool]:
        if value is None:
            add(pid, "UNKNOWN", "UNKNOWN missing evidence")
            return None
        add(pid, "PASS" if ok else "FAIL", detail)
        return ok

    adv_ok = field("P-ADV20", m.adv20, (m.adv20 or 0) >= ADV20_MIN, f"adv20={m.adv20}")
    t_ok = field("P-TURNOVER", m.turnover_cr_5d, (m.turnover_cr_5d or 0) >= TURNOVER_CR_MIN, f"to={m.turnover_cr_5d}")
    s_ok = field("P-STALE", m.stale_days, (m.stale_days or 0) <= STALE_MAX_DAYS, f"stale={m.stale_days}")
    mom_ok = field("P-MOM", m.mom_6m, (m.mom_6m or 0) > MOM_MIN, f"mom={m.mom_6m}")
    if m.max_corr_vs_book is None:
        add("P-CORR", "UNKNOWN", "UNKNOWN corr vs book")
        c_ok = None
    else:
        c_ok = m.max_corr_vs_book <= CORR_MAX
        add("P-CORR", "PASS" if c_ok else "FAIL", f"corr={m.max_corr_vs_book} max={CORR_MAX}")

    unknown_fields = [k for k, v in [
        ("adv20", adv_ok), ("turnover_cr_5d", t_ok), ("stale_days", s_ok),
        ("mom_6m", mom_ok), ("corr", c_ok),
    ] if v is None]
    # quality is mandatory for score, not its own piano key
    if m.quality_score is None:
        unknown_fields.append("quality_score")

    score_ready = m.mom_6m is not None and m.quality_score is not None
    score = compute_final_score(m.mom_6m, m.quality_score) if score_ready else 0.0
    if score_ready:
        add("P-SCORE", "PASS", f"score={score}")
    else:
        add("P-SCORE", "UNKNOWN", "UNKNOWN score (mom or quality missing)")

    hard = all(v is True for v in (adv_ok, t_ok, s_ok, mom_ok, c_ok))
    coverage = round(sum(1 for v in (adv_ok, t_ok, s_ok, mom_ok, c_ok) if v is not None) / 5.0, 2)

    if unknown_fields:
        decision, reason = "UNKNOWN", "missing " + ",".join(unknown_fields)
        hard = False
    elif hard and score >= SCORE_SELECT:
        decision, reason = "SELECT", f"score {score} >= {SCORE_SELECT}"
    elif hard and score >= SCORE_WATCH:
        decision, reason = "WATCH", f"score {score} in watch band"
    else:
        decision, reason = "REJECT", "hard fail or low score"

    if decision == "SELECT":
        add("P-SELECT", "PASS", reason)
    elif decision == "UNKNOWN":
        add("P-SELECT", "UNKNOWN", reason)
    else:
        add("P-SELECT", "FAIL", reason)

    return ScanRow(
        symbol=m.symbol,
        decision=decision,
        score=score,
        segment=m.segment,
        ltp=m.ltp,
        reason=reason,
        hits=hits,
        hard_pass=hard,
        coverage=coverage,
    )


def run_scan(
    metrics: Sequence[StockMetrics],
    universe_bucket: str = "core",
) -> ScanSnapshot:
    rows = [evaluate_stock(m) for m in metrics]
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            0 if r.decision == "SELECT" else 1 if r.decision == "WATCH" else 2 if r.decision == "UNKNOWN" else 3,
            -r.score,
            r.symbol,
        ),
    )
    return ScanSnapshot(
        asof=_utc_now_iso(),
        universe_bucket=universe_bucket,
        universe_count=len(rows_sorted),
        select_count=sum(1 for r in rows_sorted if r.decision == "SELECT"),
        watch_count=sum(1 for r in rows_sorted if r.decision == "WATCH"),
        reject_count=sum(1 for r in rows_sorted if r.decision == "REJECT"),
        unknown_count=sum(1 for r in rows_sorted if r.decision == "UNKNOWN"),
        rows=[r.to_dict() for r in rows_sorted],
        notes=[
            f"SCORE_SELECT={SCORE_SELECT}",
            f"SCORE_WATCH={SCORE_WATCH}",
            f"CORR_MAX={CORR_MAX}",
            "missing_metrics=UNKNOWN",
        ],
    )


def demo_metrics():
    return [
        StockMetrics("TCS", 800_000, 25, 1, 0.18, 75, 0.4, "IT", 3840),
        StockMetrics("HDFCBANK", 1_200_000, 40, 0, 0.12, 70, 0.35, "Finance", 1690),
        StockMetrics("ITC", 700_000, 15, 2, -0.05, 55, 0.3, "FMCG", 450),
        StockMetrics("THINNAME", 50_000, 1, 1, 0.2, 80, 0.2, "", 100),
    ]


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--data-dir", default="ash08_data")
    args = p.parse_args()
    if not args.demo:
        p.error("use --demo")
    snap = run_scan(demo_metrics(), universe_bucket="demo")
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    Path(args.data_dir, "scan_latest.json").write_text(json.dumps(snap.to_dict(), indent=2))
    print(json.dumps({"select": snap.select_count, "watch": snap.watch_count, "reject": snap.reject_count}, indent=2))


if __name__ == "__main__":
    main()
