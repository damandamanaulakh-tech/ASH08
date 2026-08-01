"""Central, versioned runtime configuration for ASH08."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMETER_SET_ID = os.environ.get("ASH08_PARAMETER_SET_ID", "ash08-50l-v1")
BOOK_VALUE = float(os.environ.get("ASH08_BOOK_VALUE", "5000000"))
MAX_OPEN_POSITIONS = int(os.environ.get("ASH08_MAX_OPEN_POSITIONS", "10"))
MAX_NAME_PCT = float(os.environ.get("ASH08_MAX_NAME_PCT", "2.5"))
MAX_GROSS_PCT = float(os.environ.get("ASH08_MAX_GROSS_PCT", "100"))
STOP_PCT = float(os.environ.get("ASH08_STOP_PCT", "3"))
TARGET_PCT = float(os.environ.get("ASH08_TARGET_PCT", "6"))
MAX_HOLD_SESSIONS = int(os.environ.get("ASH08_MAX_HOLD_SESSIONS", "15"))
BUY_COST_PCT = float(os.environ.get("ASH08_BUY_COST_PCT", "0.10"))
SELL_COST_PCT = float(os.environ.get("ASH08_SELL_COST_PCT", "0.10"))

ADV20_MIN = float(os.environ.get("ASH08_ADV20_MIN", "200000"))
TURNOVER_CR_MIN = float(os.environ.get("ASH08_TURNOVER_CR_MIN", "5"))
STALE_MAX_DAYS = float(os.environ.get("ASH08_STALE_MAX_DAYS", "7"))
MOM_MIN = float(os.environ.get("ASH08_MOM_MIN", "0"))
CORR_MAX = float(os.environ.get("ASH08_CORR_MAX", "0.85"))
SCORE_SELECT = float(os.environ.get("ASH08_SCORE_SELECT", "67"))
SCORE_WATCH = float(os.environ.get("ASH08_SCORE_WATCH", "60"))
MOM_WEIGHT = float(os.environ.get("ASH08_MOM_WEIGHT", "0.65"))
QUAL_WEIGHT = float(os.environ.get("ASH08_QUAL_WEIGHT", "0.35"))
MIN_CONFIDENCE = float(os.environ.get("ASH08_MIN_CONFIDENCE", "1.0"))

CORE_MIN = int(os.environ.get("ASH08_CORE_MIN", "150"))
CORE_MAX = int(os.environ.get("ASH08_CORE_MAX", "250"))
DISCOVERY_MAX = int(os.environ.get("ASH08_DISCOVERY_MAX", "5000"))

MAX_BODY_BYTES = int(os.environ.get("ASH08_MAX_BODY_BYTES", str(64 * 1024)))
MAX_QUOTE_AGE_SECONDS = int(os.environ.get("ASH08_MAX_QUOTE_AGE_SECONDS", "120"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("ASH08_RATE_LIMIT_PER_MINUTE", "120"))
API_TOKEN = (os.environ.get("ASH08_API_TOKEN") or "").strip()
ALLOW_DEMO = (os.environ.get("ASH08_ALLOW_DEMO") or "0").strip().lower() in {"1", "true", "yes"}
DATA_DIR = Path(os.environ.get("ASH08_DATA_DIR") or (ROOT / "ash08_data")).resolve()
TRUSTED_ORIGINS = tuple(
    x.strip().rstrip("/")
    for x in (os.environ.get("ASH08_TRUSTED_ORIGINS") or "").split(",")
    if x.strip()
)


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
            "min_confidence": MIN_CONFIDENCE,
        },
    }
