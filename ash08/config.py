"""ASH08 G0 runtime contract — single source of truth.

Locked decision numbers are constants. Scanner, paper, API, piano, README,
and CI import from here. 67/60 belongs to backup/aug24-fail-closed only.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMETER_SET_ID = "ash08-50l-g0-v1"

# --- locked. do not env-override (old Render env had 67/60) ---
BOOK_VALUE = 5_000_000.0
MAX_OPEN_POSITIONS = 10
MAX_NAME_PCT = 2.5
MAX_GROSS_PCT = 100.0
STOP_PCT = 3.0
TARGET_PCT = 6.0
MAX_HOLD_SESSIONS = 15
BUY_COST_PCT = 0.10
SELL_COST_PCT = 0.10

ADV20_MIN = 200_000.0
TURNOVER_CR_MIN = 5.0
STALE_MAX_DAYS = 7.0
MOM_MIN = 0.0
CORR_MAX = 0.70
SCORE_SELECT = 70.0
SCORE_WATCH = 55.0
MOM_WEIGHT = 0.65
QUAL_WEIGHT = 0.35

CORE_MIN = 150
CORE_MAX = 250
DISCOVERY_MAX = 5000
CORE_TTL_DAYS = 7
UNIVERSE_POLICY_ID = "ash08-universe-g1-v1"
METRICS_POLICY_ID = "ash08-metrics-g2-v1"
MOM_LOOKBACK_CAL_DAYS = 182
MOM_MIN_SPAN_DAYS = 120
ADV_WINDOW = 20
TURNOVER_WINDOW = 5
QUALITY_TARGET_SESSIONS = 126
QUALITY_MIN_SESSIONS = 60
CORR_MIN_OVERLAP = 20
HISTORY_TTL_HOURS = 20
METRICS_REFRESH_BATCH = 40

# G4 — documented Upstox index keys (see ash08/indices.py)
INDEX_POLICY_ID = "ash08-indices-g4-v1"

GOVERNOR_EXPOSURE = {
    "L0": 100.0,
    "L1": 70.0,
    "L2": 50.0,
    "L3": 25.0,
    "L4": 15.0,
}

DATA_DIR = Path(os.environ.get("ASH08_DATA_DIR") or (ROOT / "ash08_data")).resolve()


def public_config() -> dict:
    return {
        "parameter_set_id": PARAMETER_SET_ID,
        "book_value": BOOK_VALUE,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_name_pct": MAX_NAME_PCT,
        "max_gross_pct": MAX_GROSS_PCT,
        "stop_pct": STOP_PCT,
        "target_pct": TARGET_PCT,
        "max_hold_sessions": MAX_HOLD_SESSIONS,
        "buy_cost_pct": BUY_COST_PCT,
        "sell_cost_pct": SELL_COST_PCT,
        "governor_exposure": dict(GOVERNOR_EXPOSURE),
        "scanner": {
            "adv20_min": ADV20_MIN,
            "turnover_cr_min": TURNOVER_CR_MIN,
            "stale_max_days": STALE_MAX_DAYS,
            "mom_min": MOM_MIN,
            "corr_max": CORR_MAX,
            "score_select": SCORE_SELECT,
            "score_watch": SCORE_WATCH,
            "mom_weight": MOM_WEIGHT,
            "quality_weight": QUAL_WEIGHT,
        },
        "universe": {
            "policy_id": UNIVERSE_POLICY_ID,
            "core_min": CORE_MIN,
            "core_max": CORE_MAX,
            "discovery_max": DISCOVERY_MAX,
            "core_ttl_days": CORE_TTL_DAYS,
        },
        "metrics": {
            "policy_id": METRICS_POLICY_ID,
            "mom_lookback_cal_days": MOM_LOOKBACK_CAL_DAYS,
            "adv_window": ADV_WINDOW,
            "turnover_window": TURNOVER_WINDOW,
            "quality": "coverage of 126 sessions; UNKNOWN if < 60 bars",
            "ltp": "upstox_only",
            "missing": "UNKNOWN",
        },
        "indices": {
            "policy_id": INDEX_POLICY_ID,
            "source": "upstox_only",
            "on_error": "failed",
        },
        "trade_plan": {
            "stop_pct": STOP_PCT,
            "target_pct": TARGET_PCT,
            "max_hold_days": MAX_HOLD_SESSIONS,
            "max_open": MAX_OPEN_POSITIONS,
        },
    }
