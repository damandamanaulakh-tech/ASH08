"""ASH08 Scanner — locked gates SELECT / WATCH / REJECT."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

LOG = logging.getLogger("ash08.scanner")
ADV20_MIN, TURNOVER_CR_MIN, STALE_MAX_DAYS = 200_000, 5.0, 7
MOM_MIN, SCORE_SELECT, SCORE_WATCH, CORR_MAX = 0.0, 70.0, 55.0, 0.85
MOM_WEIGHT, QUAL_WEIGHT = 0.65, 0.35


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


def evaluate_stock(m: StockMetrics) -> ScanRow:
    hits: List[ParamHit] = []
    adv_ok = True if m.adv20 is None else m.adv20 >= ADV20_MIN
    hits.append(ParamHit("P-ADV20", adv_ok, f"adv20={m.adv20}"))
    t_ok = True if m.turnover_cr_5d is None else m.turnover_cr_5d >= TURNOVER_CR_MIN
    hits.append(ParamHit("P-TURNOVER", t_ok, f"to={m.turnover_cr_5d}"))
    s_ok = True if m.stale_days is None else m.stale_days <= STALE_MAX_DAYS
    hits.append(ParamHit("P-STALE", s_ok, f"stale={m.stale_days}"))
    mom_ok = True if m.mom_6m is None else m.mom_6m > MOM_MIN
    hits.append(ParamHit("P-MOM", mom_ok, f"mom={m.mom_6m}"))
    c_ok = True if m.max_corr_vs_book is None else m.max_corr_vs_book <= CORR_MAX
    hits.append(ParamHit("P-CORR", c_ok, f"corr={m.max_corr_vs_book}"))
    hard = adv_ok and t_ok and s_ok and mom_ok and c_ok
    score = compute_final_score(m.mom_6m, m.quality_score)
    hits.append(ParamHit("P-SCORE", True, f"score={score}"))
    if hard and score >= SCORE_SELECT:
        decision, reason = "SELECT", f"score {score} >= {SCORE_SELECT}"
    elif hard and score >= SCORE_WATCH:
        decision, reason = "WATCH", f"score {score} in watch band"
    else:
        decision, reason = "REJECT", "hard fail or low score"
    return ScanRow(
        symbol=m.symbol,
        decision=decision,
        score=score,
        segment=m.segment,
        ltp=m.ltp,
        reason=reason,
        hits=hits,
        hard_pass=hard,
    )


def run_scan(
    metrics: Sequence[StockMetrics],
    universe_bucket: str = "core",
    require_metrics: bool = False,
) -> ScanSnapshot:
    rows = [evaluate_stock(m) for m in metrics]
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            0 if r.decision == "SELECT" else 1 if r.decision == "WATCH" else 2,
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
        rows=[r.to_dict() for r in rows_sorted],
        notes=[f"SCORE_SELECT={SCORE_SELECT}", f"SCORE_WATCH={SCORE_WATCH}"],
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
