# ASH08 Desk

**Separate product from AshStocks.**  
Paper-trading desk for Indian NSE equities: weekly Core universe → locked parameter scanner → paper ticket → Adaptive Risk Governor (L0–L4).

## Do not mix with AshStocks

| Product | Repo | Deploy |
|---------|------|--------|
| AshStocks | `AshStocks` | existing Render `ashstocks-api` |
| **ASH08** | **this repo** | **own Render service** |

## Modules

| Path | Phase | Role |
|------|-------|------|
| `ash08/universe.py` | 2 | Core 150–250 (weekly) + Discovery |
| `ash08/scanner.py` | 3 | SELECT / WATCH / REJECT (locked gates) |
| `ash08/paper_engine.py` | 4 | Paper ticket, positions, exits |
| `ash08/adaptive_risk_governor.py` | 4 | L0–L4 exposure 100/70/50/25/15 |
| `desk/ASH08_Desk_Dashboard.html` | 1 | UI layout (locked) |

## Quick run (local)

```bash
pip install -r requirements.txt
python ash08/universe.py --demo --data-dir ash08_data
python ash08/scanner.py --demo --data-dir ash08_data
python ash08/paper_engine.py --demo --data-dir ash08_data
```

## Render (own service)

1. New Web Service → connect **this** repo `ASH08`
2. Runtime: **Python 3**
3. Build: `pip install -r requirements.txt`
4. Start: `python -m http.server $PORT --directory desk`  *(static desk for now)*  
   or later: `uvicorn api:app --host 0.0.0.0 --port $PORT` when API is added
5. Service name suggestion: **`ash08-desk`**
6. Do **not** point AshStocks Render at this repo

## Locked rules (summary)

- Core universe weekly 150–250; Discovery on demand
- Gates: ADV20 ≥ 2L, turnover ≥ ₹5 Cr, stale ≤ 7d, mom > 0, score 0.65/0.35
- SELECT ≥ 70, WATCH ≥ 55
- Paper only; max name 2.5% × governor exposure
- Governor L0–L4: 100 / 70 / 50 / 25 / 15

## Status

Phases 0–4 complete offline. Live API shell is optional next step on **this** repo only.
