# ASH08 Desk — Today's Advice

ASH08 is a paper-only NSE **advisory** desk. Own repo. Not AshStocks. Not AM07.
Render deploys **this** repo (`damandamanaulakh-tech/ASH08`). That is the only live desk.

Front of the app is **Today's Advice**: BUY / WATCH / AVOID with ½-Kelly size, stop −3%, target +6%, hold 15d, and a written why. YoY lock and factor-comparison tables live under **Lab**, not the home screen.

## Locked runtime (ash08/config.py)

| Item | Value |
|------|--------|
| Book | ₹5,00,00,000 (5 Cr) |
| SELECT / BUY | score ≥ **68** (68–70 is full BUY, near-miss ledger only) |
| WATCH | **55** ≤ score < 68 |
| Rank | M1 6m+12m vol-adj, M6 N = **25** |
| Corr vs book | ≤ **0.70** |
| ADV20 | ≥ 2,00,000 |
| 5d turnover | ≥ ₹5 Cr |
| 6M momentum | > 0 |
| T1 / T2 / T3 | close > SMA200 / ATR% ≤ 8 / **no** min-price bar |
| Size | ½-Kelly (IC 0.05 assumed, cap 5% of book, floor 67) |
| FII | size throttle only, not SELECT |
| Cash reserve | 5% — cash is tracked, 0.10% buy + 0.10% sell |
| Max open | 500 |
| Stop / target / hold | −3% / +6% / 15 sessions |
| Governor L0–L4 | 100 / 70 / 50 / 25 / 15 % |

`backup/aug24-fail-closed` is archive (SELECT 67 / WATCH 60). It is not `main`.

## Tape (release `FIIDII30000stocksdata`)

| Feed | Status |
|------|--------|
| 191 `*.NS.csv` through **2026-07-17** | CLOSED — T1 T2 T3 T9 M1 M6 ROC20 ADV turnover |
| NSE bhav 08-Jun-2026 `DELIV_PER` | SNAPSHOT_1D — official, not a fake 20d avg |
| FII/DII net through **2026-08-07** | CLOSED for size throttle (+480 Cr last print) |
| Mcap ≥ ₹5,000 Cr | PROXY_N200 — fundamentals have income/cashflow only, no `marketCap` |
| India VIX / 22k | HELD |
| Live Upstox LTP | MISSING — paper fill stays 0, no invented mark |

Reference price on an advice card is the last tape close. Paper BUY still needs live LTP.

## Endpoints

| Path | What |
|------|------|
| `/` | Today's Advice |
| `/api/advise` | BUY / WATCH / AVOID JSON |
| `/api/paper/book` | Cash / equity / opens / closed |
| `/api/history` | YoY lock (Lab) |
| `/api/factors` | 2024-07-05 factor lab (Lab) |
| `/api/gaps` | Infinity vs desk |
| `/api/health` | `build=2026-09-09-advisory` |

## Run

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```
