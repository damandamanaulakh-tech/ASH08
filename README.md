# ASH08 Desk — paper robot

ASH08 is a paper-only NSE desk. Own repo. Not AshStocks. Not AM07.
Render deploys **this** repo (`damandamanaulakh-tech/ASH08`).

**Front is Today's Advice.** The robot **buys those BUY names itself** and **sells itself** on −3% stop / +6% target / 15d hold. Paper only. Live Upstox LTP or skip — no invented fill.

## What the robot does

| When | Action |
|------|--------|
| NSE session 09:15–15:30 IST | Auto-buy today's BUY list at **live LTP**, ½-Kelly, cash hold 5% |
| Same session + 15:30–15:40 mark | Auto-sell if live LTP hits stop −3% or target +6% |
| 15 calendar days | Auto-sell MAX_HOLD when a live quote exists |
| No live LTP | **No buy, no sell.** P&L stays 0 on that name |

Background tick every 45s while the service is up. Manual: `/api/robot/tick?force=1`.

AM07 was the pattern (robot picker + journal). ASH08 numbers stay: ₹5 Cr, Kelly, SELECT 68, −3 / +6 / 15d. Not AM07's ₹50L / ₹1.25L / −5 / +20.

## Locked runtime (ash08/config.py)

| Item | Value |
|------|--------|
| Book | ₹5,00,00,000 (5 Cr) |
| SELECT / BUY | score ≥ **68** (68–70 is full BUY, near-miss ledger only) |
| WATCH | **55** ≤ score < 68 |
| Rank | M1 6m+12m vol-adj, M6 N = **25** |
| Size | ½-Kelly (IC 0.05 assumed, cap 5% of book, floor 67) |
| FII | size throttle only, not SELECT |
| Cash reserve | 5% — cash is tracked, 0.10% buy + 0.10% sell |
| Max open | 500 |
| Stop / target / hold | −3% / +6% / 15 sessions |
| Governor L0–L4 | 100 / 70 / 50 / 25 / 15 % |

## Tape

| Feed | Status |
|------|--------|
| Yahoo Finance v8 daily 5y, last bar = last session | CLOSED — T1 T2 T3 T9 M1 M6 |
| NSE bhav 08-Jun-2026 `DELIV_PER` | SNAPSHOT_1D — official, not a fake 20d avg |
| FII/DII net through **2026-08-07** | CLOSED for size throttle |
| Mcap ≥ ₹5,000 Cr | PROXY_N200 — no rupee mcap field |
| India VIX / 22k | HELD |

## Endpoints

| Path | What |
|------|------|
| `/` | Today's Advice + robot status |
| `/api/advise` | BUY / WATCH / AVOID |
| `/api/robot/tick` | Run one paper cycle (`?force=1` ignores session window) |
| `/api/robot/status` | Last tick |
| `/api/paper/book` | Cash / equity / opens / closed |
| `/api/health` | `build=2026-09-09-robot` |

## Run

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```
