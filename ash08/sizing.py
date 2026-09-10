"""½-Kelly sizing. Copied from AM07 maths, no pandas.

edge = IC * sigma * z, z = (score - 61) / 30
fraction = 0.5 * mu / sigma^2, capped at 5% of equity.
Score exactly at SELECT 62 has a thin edge (floor is 61). No vol → 0, not a guess.
File 3: 70+ names are sized down in advisory, not given more Kelly.
IC 0.05 is assumed — same honest caveat as AM07.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

from ash08.config import (
    BOOK_VALUE,
    KELLY_FLOOR,
    KELLY_FRACTION,
    KELLY_IC,
    KELLY_MAX_PCT,
    KELLY_MIN_NOTIONAL,
    KELLY_VOL_WINDOW,
)

TRADING_DAYS = 252


def annual_vol(closes: Sequence[float], days: int = KELLY_VOL_WINDOW) -> Optional[float]:
    if not closes or len(closes) < 21:
        return None
    window = list(closes)[-(days + 1) :]
    rets = []
    for i in range(1, len(window)):
        prev = float(window[i - 1])
        if prev <= 0:
            continue
        rets.append(float(window[i]) / prev - 1.0)
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def kelly_fraction(mu: float, sigma: float) -> float:
    if sigma <= 0 or mu <= 0:
        return 0.0
    return min(KELLY_MAX_PCT, KELLY_FRACTION * mu / (sigma * sigma))


def kelly_notional(
    equity: float,
    score: Optional[float],
    sigma: Optional[float],
    exposure_pct: float = 100.0,
) -> tuple[float, Dict[str, Any]]:
    if score is None or sigma is None or sigma <= 0 or equity <= 0:
        return 0.0, {"mode": "half_kelly", "reason": "no_vol_or_score", "notional": 0.0}
    z = (float(score) - float(KELLY_FLOOR)) / 30.0
    mu = float(KELLY_IC) * float(sigma) * z
    f = kelly_fraction(mu, float(sigma))
    f *= max(0.0, min(1.0, float(exposure_pct) / 100.0))
    notional = f * float(equity)
    if 0 < notional < KELLY_MIN_NOTIONAL:
        return 0.0, {
            "mode": "half_kelly",
            "reason": "below_min",
            "mu_pct": round(mu * 100, 3),
            "sigma_pct": round(float(sigma) * 100, 2),
            "kelly_pct": round(f * 100, 3),
            "notional": 0.0,
            "ic_assumed": KELLY_IC,
            "floor": KELLY_FLOOR,
        }
    return notional, {
        "mode": "half_kelly",
        "mu_pct": round(mu * 100, 3),
        "sigma_pct": round(float(sigma) * 100, 2),
        "kelly_pct": round(f * 100, 3),
        "notional": round(notional, 0),
        "capped_at_max": bool(f >= KELLY_MAX_PCT - 1e-12 and mu > 0),
        "ic_assumed": KELLY_IC,
        "floor": KELLY_FLOOR,
        "book": BOOK_VALUE,
    }
