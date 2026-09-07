# ASH08 Universe — G1

Own repo **ASH08**. Not AshStocks.

## Policy

| Bucket | Size | Refresh | Use |
|--------|------|---------|-----|
| **Core** | 150–250 | Weekly (`CORE_TTL_DAYS=7`) | Paper desk / scan |
| **Discovery** | up to 5000 | On demand | Research only — **never auto-buy** |

Selection without ADV/turnover evidence: prefer-rank cap from seed order, tagged `liquidity_evidence=pending_G2`. No invented ADV.

A 1401-name dump is **invalid Core** and is rebuilt on load.

```bash
python ash08/universe.py --demo --data-dir ash08_data
python ash08/universe.py --status --data-dir ash08_data
```

API: `GET /api/universe/core` · `GET /api/universe/refresh` · `GET /api/universe/discovery`
