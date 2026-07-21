# ASH08 Universe — Phase 2 (LOCKED policy)

Own repo **ASH08**. Not AshStocks.

## Policy

| Bucket | Size | Refresh | Use |
|--------|------|---------|-----|
| **Core** | 150–250 | **Weekly** | Paper desk |
| **Discovery** | up to 5000 | On demand | Research |

Filters when metrics exist: ADV20 ≥ 200,000 · 5D ₹ turnover ≥ 5 Cr

## Run

```bash
python ash08/universe.py --demo --data-dir ash08_data
python ash08/universe.py --from-json instruments.json --data-dir ash08_data
python ash08/universe.py --status --data-dir ash08_data
```
