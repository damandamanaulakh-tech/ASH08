# ASH08 Scanner — Phase 3

Own repo **ASH08**. Not AshStocks. Numbers from `ash08/config.py`.

## Locked gates

| ID | Rule |
|----|------|
| P-ADV20 | ≥ 200,000 |
| P-TURNOVER | ≥ ₹5 Cr |
| P-STALE | ≤ 7 days |
| P-MOM | 6M return > 0 |
| P-SCORE | 0.65×vol-adj 6M+12M + 0.35×(low-vol + ADV20). Never 6M-only 50+200m. |
| P-CORR | ≤ **0.70** vs book |
| P-SELECT | above 200 DMA and score ≥ **62**. BUY band **[62,70)**. **70+** still SELECT, size ×0.50. |
| P-WATCH | score ∈ **[55,62)** + hard pass |
| Below 200 DMA | **no SELECT**, whatever the score |

Missing mandatory evidence (ADV20, turnover, stale, mom_6m, quality) → **UNKNOWN**, never PASS.

Quality: measured low-vol + ADV20 liquidity — **not** bar-coverage 100.
LTP: Upstox in session, Yahoo after hours. Tape close is not a fill.
