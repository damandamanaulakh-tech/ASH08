# ASH08 Scanner — Phase 3

Own repo **ASH08**. Not AshStocks. Numbers from `ash08/config.py`.

## Locked gates

| ID | Rule |
|----|------|
| P-ADV20 | ≥ 200,000 |
| P-TURNOVER | ≥ ₹5 Cr |
| P-STALE | ≤ 7 days |
| P-MOM | 6M return > 0 |
| P-SCORE | 0.65×mom + 0.35×quality |
| P-CORR | ≤ **0.70** vs book |
| P-SELECT | score ≥ **70** + hard pass |
| P-WATCH | score ∈ **[55,70)** + hard pass |

Missing mandatory evidence (ADV20, turnover, stale, mom_6m, quality) → **UNKNOWN**, never PASS.

Quality proxy (G2): 100 × (bar count / 126), UNKNOWN if fewer than 60 sessions.
LTP: Upstox only.
