"""File 3 selection score — vol-adj 6M+12M, measured quality, no fake 50/100.

SCORE = 0.65 × clip(50 + 25 × vol_adj, 0, 100) + 0.35 × QUALITY

QUALITY = (LOW_VOL + LIQUIDITY) / 2
  LOW_VOL    = clip(100 − (σ% − 10) × 1.7, 0, 100)
  LIQUIDITY  = 90 / 70 / 55 / 30 by ADV20 buckets

Never map 6M-only through 50+200m. Never invent quality=100 or 50.
Missing vol_adj or quality → None (DATA_NEEDED), not a guessed score.
"""
from __future__ import annotations

from typing import Optional

from ash08.config import MOM_WEIGHT, QUAL_WEIGHT


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _finite(value) -> Optional[float]:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def momentum_score(vol_adj) -> Optional[float]:
    """AshStocks / AM07 vol-adj 6M+12M mapped 0–100. Not 50+200×6M."""
    raw = _finite(vol_adj)
    if raw is None:
        return None
    return round(_clip(50.0 + raw * 25.0), 4)


def quality_from_tape(sigma, adv20) -> Optional[float]:
    """Low-vol + ADV20 liquidity. None if either input is missing."""
    sig = _finite(sigma)
    adv = _finite(adv20)
    if sig is None or adv is None or sig <= 0 or adv < 0:
        return None
    low_vol = _clip(100.0 - (sig * 100.0 - 10.0) * 1.7)
    if adv > 1_000_000:
        liq = 90.0
    elif adv > 300_000:
        liq = 70.0
    elif adv > 100_000:
        liq = 55.0
    else:
        liq = 30.0
    return round((low_vol + liq) / 2.0, 2)


def blend_score(mom_s, quality) -> Optional[float]:
    m = _finite(mom_s)
    q = _finite(quality)
    if m is None or q is None:
        return None
    return round(MOM_WEIGHT * _clip(m) + QUAL_WEIGHT * _clip(q), 2)


def compute_score(vol_adj=None, sigma=None, adv20=None, quality=None) -> Optional[float]:
    """Full File 3 score. `quality` is an already-measured 0–100; coverage-100 is not a fill-in."""
    mom_s = momentum_score(vol_adj)
    qual = quality_from_tape(sigma, adv20)
    if qual is None:
        qual = _finite(quality)
        if qual is not None:
            qual = _clip(qual)
    return blend_score(mom_s, qual)
