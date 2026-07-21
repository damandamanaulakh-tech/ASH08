# ASH08 Scanner — Phase 3

Own repo **ASH08**. Not AshStocks.

## Locked gates

| ID | Rule |
|----|------|
| P-ADV20 | ≥ 200,000 |
| P-TURNOVER | ≥ ₹5 Cr |
| P-STALE | ≤ 7 days |
| P-MOM | 6M return > 0 |
| P-SCORE | 0.65×mom + 0.35×quality |
| P-CORR | ≤ 0.85 vs book |
| P-SELECT | score ≥ 70 + hard pass |
| P-WATCH | score ∈ [55,70) + hard pass |

```bash
python ash08/scanner.py --demo --data-dir ash08_data
```
