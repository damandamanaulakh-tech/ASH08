# ASH08 Desk (G0 contract)

ASH08 is a paper-only NSE desk. Own repo. Not AshStocks.
Render deploys **this** repo (`damandamanaulakh-tech/ASH08`). That is the only live desk.

## Locked runtime (ash08/config.py)

| Item | Value |
|------|--------|
| Book | ₹5,00,00,000 (5 Cr) |
| SELECT | score ≥ **68** (68–70 is full SELECT, near-miss ledger only) |
| NEAR_MISS (ledger) | 68 ≤ score < 70 on SELECT names |
| WATCH | **55** ≤ score < 68 |
| Corr vs book | ≤ **0.70** |
| ADV20 | ≥ 2,00,000 |
| 5d turnover | ≥ ₹5 Cr |
| Stale | ≤ 7 days |
| 6M momentum | > 0 |
| Size | ½-Kelly (IC 0.05 assumed, cap 5% of book, floor 67) |
| Cash reserve | 5% — cash is tracked, 0.10% buy + 0.10% sell |
| Max open | 500 |
| Stop / target / hold | −3% / +6% / 15 sessions |
| Governor L0–L4 | 100 / 70 / 50 / 25 / 15 % |
| Order family | NSE bulk / block / buyback — net sell blocks |

`backup/aug24-fail-closed` is archive (SELECT 67 / WATCH 60). It is not `main`.

## Honest operating state

- Decision **numbers** are one contract (`ash08/config.py`).
- **Core** is 150–250, rebuilt weekly, persisted. Seed pool (~1401) is not Core.
- **Discovery** is on-demand, cap 5000, never auto-buy.
- Scan **inputs** are measured from cached/Upstox daily bars, or **UNKNOWN**.
- Index tiles: Upstox quote or **failed**. No silent dash.
- LTP is Upstox only. Missing quote ≠ fake fill. P&L stays 0 until live LTP.
- Empty book still shows **Equity ₹5 Cr / Cash ₹5 Cr**.
- Closed trades show entry value, exit value, realized after costs.

## Evidence tabs (wired 2026-09-09)

| Path | What |
|------|------|
| Desk **YoY lock** | Adaptive Governor 2007–2022: 6.63x vs 14.77x, 16 years, 10 events |
| Desk **Factors** | Nifty 200 6m+12m vol-adj vs live 6m raw. F1 34.57 vs F5 32.93 vs EW 24.87 |
| `/api/history` | JSON for YoY / events / severity |
| `/api/factors` | JSON for TAKE/PARK + ranks |
| `/api/gaps` | Infinity report vs live |

Scanner score is still 6m raw. M1 (6+12 vol-adj rank) is TAKE, not shipped.

## Run

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```

Health: `/api/health` includes `contract` and `build`.
