"""Strict ASH08 scanner: missing mandatory evidence is UNKNOWN, never a pass."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import (
    ADV20_MIN,
    CORR_MAX,
    MIN_CONFIDENCE,
    MOM_MIN,
    MOM_WEIGHT,
    PARAMETER_SET_ID,
    QUAL_WEIGHT,
    SCORE_SELECT,
    SCORE_WATCH,
    STALE_MAX_DAYS,
    TURNOVER_CR_MIN,
)

REQUIRED_FIELDS = (
    "adv20",
    "turnover_cr_5d",
    "stale_days",
    "mom_6m",
    "quality_score",
    "max_corr_vs_book",
)


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
    instrument_key: str = ""
    feature_asof: str = ""
    feature_source: str = ""


@dataclass
class ParamHit:
    param_id: str
    passed: bool
    observed: bool
    detail: str


@dataclass
class ScanRow:
    symbol: str
    decision: str
    score: Optional[float]
    confidence: float
    coverage: float
    segment: str = ""
    ltp: Optional[float] = None
    instrument_key: str = ""
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
    unknown_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    parameter_set_id: str = PARAMETER_SET_ID
    formula_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mom_return_to_score(value: float) -> float:
    return max(0.0, min(100.0, 50.0 + float(value) * 200.0))


def compute_final_score(mom_6m: Optional[float], quality_score: Optional[float]) -> Optional[float]:
    if mom_6m is None or quality_score is None:
        return None
    momentum = mom_return_to_score(float(mom_6m))
    quality = max(0.0, min(100.0, float(quality_score)))
    return round(MOM_WEIGHT * momentum + QUAL_WEIGHT * quality, 2)


def _hit(param_id: str, value: Optional[float], predicate, detail: str) -> ParamHit:
    observed = value is not None
    passed = bool(observed and predicate(float(value)))
    return ParamHit(param_id, passed, observed, detail)


def evaluate_stock(metrics: StockMetrics, require_metrics: bool = True) -> ScanRow:
    hits = [
        _hit("P-ADV20", metrics.adv20, lambda x: x >= ADV20_MIN, f"adv20={metrics.adv20}; min={ADV20_MIN}"),
        _hit("P-TURNOVER", metrics.turnover_cr_5d, lambda x: x >= TURNOVER_CR_MIN, f"turnover_cr_5d={metrics.turnover_cr_5d}; min={TURNOVER_CR_MIN}"),
        _hit("P-STALE", metrics.stale_days, lambda x: x <= STALE_MAX_DAYS, f"stale_days={metrics.stale_days}; max={STALE_MAX_DAYS}"),
        _hit("P-MOM", metrics.mom_6m, lambda x: x > MOM_MIN, f"mom_6m={metrics.mom_6m}; min>{MOM_MIN}"),
        _hit("P-CORR", metrics.max_corr_vs_book, lambda x: x <= CORR_MAX, f"max_corr={metrics.max_corr_vs_book}; max={CORR_MAX}"),
    ]
    observed_count = sum(getattr(metrics, name) is not None for name in REQUIRED_FIELDS)
    coverage = round(observed_count / len(REQUIRED_FIELDS), 4)
    confidence = coverage
    score = compute_final_score(metrics.mom_6m, metrics.quality_score)
    mandatory_complete = observed_count == len(REQUIRED_FIELDS)
    hard_pass = all(hit.passed for hit in hits)

    if require_metrics and not mandatory_complete:
        decision = "UNKNOWN"
        reason = f"missing mandatory evidence; coverage={coverage:.0%}"
    elif score is None:
        decision = "UNKNOWN"
        reason = "momentum or quality score unavailable"
    elif not hard_pass:
        decision = "REJECT"
        failed = ",".join(hit.param_id for hit in hits if not hit.passed)
        reason = f"hard gate failed: {failed}"
    else:
        effective_score = score * confidence
        if confidence < MIN_CONFIDENCE:
            decision = "UNKNOWN"
            reason = f"confidence {confidence:.2f} below {MIN_CONFIDENCE:.2f}"
        elif effective_score >= SCORE_SELECT:
            decision = "SELECT"
            reason = f"effective score {effective_score:.2f} >= {SCORE_SELECT:.2f}"
        elif effective_score >= SCORE_WATCH:
            decision = "WATCH"
            reason = f"effective score {effective_score:.2f} in {SCORE_WATCH:.2f}-{SCORE_SELECT:.2f} band"
        else:
            decision = "REJECT"
            reason = f"effective score {effective_score:.2f} < {SCORE_WATCH:.2f}"

    score_observed = score is not None
    score_passed = bool(score_observed and score >= SCORE_WATCH)
    hits.append(ParamHit("P-SCORE-WATCH", score_passed, score_observed, f"score={score}; min={SCORE_WATCH}"))
    hits.append(ParamHit("P-COVERAGE", mandatory_complete, True, f"coverage={coverage:.0%}; required=100%"))

    return ScanRow(
        symbol=metrics.symbol,
        decision=decision,
        score=score,
        confidence=confidence,
        coverage=coverage,
        segment=metrics.segment,
        ltp=metrics.ltp,
        instrument_key=metrics.instrument_key,
        reason=reason,
        hits=hits,
        hard_pass=hard_pass and mandatory_complete,
    )


def formula_hash() -> str:
    payload = {
        "parameter_set_id": PARAMETER_SET_ID,
        "weights": [MOM_WEIGHT, QUAL_WEIGHT],
        "thresholds": [ADV20_MIN, TURNOVER_CR_MIN, STALE_MAX_DAYS, MOM_MIN, CORR_MAX, SCORE_WATCH, SCORE_SELECT],
        "required_fields": REQUIRED_FIELDS,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def run_scan(metrics: Sequence[StockMetrics], universe_bucket: str = "core", require_metrics: bool = True) -> ScanSnapshot:
    rows = [evaluate_stock(item, require_metrics=require_metrics) for item in metrics]
    rank = {"SELECT": 0, "WATCH": 1, "REJECT": 2, "UNKNOWN": 3}
    rows.sort(key=lambda row: (rank.get(row.decision, 9), -(row.score or -1), row.symbol))
    return ScanSnapshot(
        asof=_utc_now_iso(),
        universe_bucket=universe_bucket,
        universe_count=len(rows),
        select_count=sum(row.decision == "SELECT" for row in rows),
        watch_count=sum(row.decision == "WATCH" for row in rows),
        reject_count=sum(row.decision == "REJECT" for row in rows),
        unknown_count=sum(row.decision == "UNKNOWN" for row in rows),
        rows=[row.to_dict() for row in rows],
        notes=[
            "mandatory metrics fail closed",
            f"SELECT>={SCORE_SELECT}",
            f"WATCH>={SCORE_WATCH}",
            f"parameter_set={PARAMETER_SET_ID}",
        ],
        formula_hash=formula_hash(),
    )


def demo_metrics() -> List[StockMetrics]:
    return [
        StockMetrics("TCS", 800_000, 25, 1, 0.18, 67, 0.4, "IT", 3840, "NSE_EQ|TCS", feature_source="demo"),
        StockMetrics("HDFCBANK", 1_200_000, 40, 0, 0.12, 70, 0.35, "Finance", 1690, "NSE_EQ|HDFCBANK", feature_source="demo"),
        StockMetrics("INCOMPLETE", None, None, None, 0.2, 80, None, "", 100, "NSE_EQ|INCOMPLETE", feature_source="demo"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--data-dir", default="ash08_data")
    args = parser.parse_args()
    if not args.demo:
        parser.error("use --demo")
    snapshot = run_scan(demo_metrics(), universe_bucket="demo", require_metrics=True)
    path = Path(args.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "scan_latest.json").write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps(snapshot.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
