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
            "core_min": CORE_MIN,
            "core_max": CORE_MAX,
            "discovery_max": DISCOVERY_MAX,
        },
        "trade_plan": {
            "stop_pct": STOP_PCT,
            "target_pct": TARGET_PCT,
            "max_hold_days": MAX_HOLD_SESSIONS,
            "max_open": MAX_OPEN_POSITIONS,
        },
    }
