# ASH08 Desk (G0 contract)

ASH08 is a paper-only NSE desk. Own repo. Not AshStocks.

## Locked runtime (ash08/config.py)

| Item | Value |
|------|--------|
| Book | ₹5,00,00,000 (5 Cr) |
| SELECT | score ≥ **70** |
| NEAR_MISS (live buy) | **68** ≤ score < 70 |
| WATCH | **55** ≤ score < 68 |
| Corr vs book | ≤ **0.70** |
| ADV20 | ≥ 2,00,000 |
| 5d turnover | ≥ ₹5 Cr |
| Stale | ≤ 7 days |
| 6M momentum | > 0 |
| Size | ₹1,00,000 / name (5 Cr / 500) |
| Max open | 500 |
| Stop / target / hold | −3% / +6% / 15 sessions |
| Governor L0–L4 | 100 / 70 / 50 / 25 / 15 % |
| Order family | NSE bulk / block / buyback — net sell blocks |

`backup/aug24-fail-closed` is archive (SELECT 67 / WATCH 60). It is not `main`.

## Honest operating state (G0 + G1)

- Decision **numbers** are one contract (`ash08/config.py`).
- **Core** is 150–250, rebuilt weekly, persisted. Seed pool (~1401) is not Core.
- **Discovery** is on-demand, cap 5000, never auto-buy.
- Scan **inputs** are measured from cached/Upstox daily bars, or **UNKNOWN**. No `i % 9` momentum.
- Piano click lists **passed / failed / UNKNOWN** from the last scan (empty scan = empty lists).
- Index tiles: Upstox quote or **failed**. No silent dash.
- Segments: Oil / Gold / Metals / IT / Finance from a documented map ∩ Core + last scan.
- LTP is Upstox only. Missing quote ≠ fake fill.

## Run

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```

Health: `/api/health` includes `contract`.

## Main components

| Path | Role |
|------|------|
| `ash08/config.py` | G0 locked numbers |
| `api.py` | HTTP + desk |
| `ash08/scanner.py` | SELECT / WATCH / REJECT |
| `ash08/paper_engine.py` | paper book, size, exits |
| `ash08/universe.py` | Core 150–250 / Discovery ≤5000 policy |
| `desk/` | Sourceborn dashboard |
| `tests/` | contract tests vs this engine |
