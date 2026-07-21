# ASH08 — Own Render service (do not use AshStocks Render)

## Repo

https://github.com/damandamanaulakh-tech/ASH08

## Create Render service (you do this once)

1. Open https://dashboard.render.com
2. **New → Web Service**
3. Connect GitHub repo **`ASH08`** (not AshStocks)
4. Settings:
   - Name: `ash08-desk`
   - Runtime: Python 3
   - Build: `pip install -r requirements.txt`
   - Start: `python -m http.server $PORT --directory desk`
5. Create Web Service

You will get a URL like `https://ash08-desk.onrender.com`

## Hard rule

| Service | Repo |
|---------|------|
| `ashstocks-api` | AshStocks only |
| `ash08-desk` | ASH08 only |

Never point AshStocks Render at ASH08 or the reverse.

## Sync core modules

Full Python modules live under `ash08/` in this repo (universe, scanner, paper_engine, adaptive_risk_governor).
If any file is missing after first push, copy from local `artifacts/ash08/` or from AshStocks branch `ash08-adaptive-governor` path `ash08/` — **copy only, do not merge repos.**

```bash
# local check
python ash08/universe.py --demo
python ash08/scanner.py --demo
python ash08/paper_engine.py --demo
```
