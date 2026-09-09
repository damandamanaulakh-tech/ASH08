# ASH08 Desk — paper robot

ASH08 is a paper-only NSE desk. Own repo. Not AshStocks. Not AM07.
Render deploys **this** repo (`damandamanaulakh-tech/ASH08`).

ASH08 is the **full paper desk** — bigger than AM07 — on **ASH08 numbers only** (₹5 Cr, ½-Kelly, SELECT 68, −3 / +6 / 15d).

It runs the AM07-style day (09:20 scan, 14:30 top-up, 15:25 square-off + expire limits, 15:35 mark) **and** the 45s catch-up robot. Pages are jobs: dashboard, scan, why, piano, register, triggers, ticket, open, closed, shadow book, risk, alerts, engine/clock, strategy, reports, YoY, factors, universe, segments, settings.

Paper only. Live last or skip — no invented fill.

## What the robot does

| When (IST) | Action |
|------|--------|
| 09:20 | Morning scan + auto-buy Today's BUY (½-Kelly) |
| 14:30 | Top-up if deployed < 60% |
| 15:25 | Expire day-limits · square **intraday** names at live last |
| 15:35 | Mark book · fire −3 / +6 / 15d |
| Every 45s | Catch-up buy + rail exits whenever a live last exists |
| No live LTP | **No buy, no sell.** P&L stays 0 on that name |

Manual: `/api/robot/tick?force=1`. Clock status: `/api/engine`.

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
| `/` | Dashboard: indices, KPIs, BUY cards with why, open, closed stats, journal |
| `/api/advise` | BUY / WATCH / AVOID |
| `/api/desk` | One payload: advise + book + robot + index tiles |
| `/api/robot/tick` | Run one paper cycle (`?force=1` ignores session window) |
| `/api/robot/status` | Last tick |
| `/api/paper/book` | Cash / equity / opens / **all** closed + win-rate |
| `/api/paper/order` | Two-sided ticket: BUY / SELL, MARKET / LIMIT |
| `/api/paper/sell` | Owner SELL → Closed Trades (live last or typed price) |
| `/api/paper/close-all` | Sell every open at live last; no quote stays open |
| `/api/desk` | Advise + book + robot + indices |
| `/api/reports` `/api/risk` `/api/alerts` | Closed-trade reports, live risk, journal alerts |
| `/api/register` `/api/triggers` `/api/shadow` | Selection register, Chitty/T gates, opportunity-cost book |
| `/api/engine` `/api/schedule` `/api/settings` | IST clock, last jobs, locked formula |
| `/api/health` | `build=2026-09-10-full-desk` |

## Run

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```
