"""Infinity Report vs live ASH08 desk. Status is evidence, not a wish list."""
from __future__ import annotations

GAPS = [
    {"id": "G-YOY", "section": "Governor evidence", "item": "YoY + Big Events tables on desk", "report": "Excel only: 6.63x vs 14.77x", "live": "History tab — 16 years, 10 events", "status": "WIRED_THIS_PASS"},
    {"id": "G-L0L4", "section": "Governor evidence", "item": "L0–L4 exposure 100/70/50/25/15", "report": "Locked AdaptiveRiskGovernor v1.0", "live": "Same band on paper book", "status": "WIRED"},
    {"id": "G-FII", "section": "Governor evidence", "item": "DAMAGE_CLUSTER + FII Q10 + sell cluster", "report": "Same-day flags from daily_joined_ifr_fii_cash_signals.csv", "live": "DD ladder + kill + consec only. FII flags not fed", "status": "PROXY"},
    {"id": "G-COSTS-HIST", "section": "Governor evidence", "item": "Costs on historical governor run", "report": "Excel run has ZERO costs", "live": "Paper book charges 0.10% buy + 0.10% sell. History tables still uncosted", "status": "MISSING"},
    {"id": "G-OOS", "section": "Governor evidence", "item": "2023–2026 out of sample", "report": "Data ends 2022-12-08", "live": "Still absent", "status": "MISSING"},
    {"id": "G-CASH", "section": "Collision", "item": "Cash reserve", "report": "AM07 30% CONFIRMED 2026-07-30", "live": "5% (user lock 2026-09-09) tracked on book", "status": "SUPERSEDED"},
    {"id": "G-BOOK", "section": "Collision", "item": "Book size", "report": "50L then 5 Cr", "live": "₹5 Cr visible on desk KPIs", "status": "WIRED"},
    {"id": "G-SELECT", "section": "Collision", "item": "SELECT floor", "report": "70 (AM07)", "live": "68 full buy, near-miss tagged, WATCH 55", "status": "SUPERSEDED"},
    {"id": "G-SIZE", "section": "Collision", "item": "Position size", "report": "AM07 vol-tier", "live": "½-Kelly IC 0.05 floor 67 cap 5%", "status": "WIRED"},
    {"id": "G-MOM", "section": "Selection", "item": "Nifty 200 6m+12m vol-adj rank vs live 6m raw", "report": "AM07 nifty200_momentum + v3 winner", "live": "Factors tab. Scanner still 6m raw. M1 TAKE not shipped.", "status": "WIRED_THIS_PASS"},
    {"id": "G-VIX", "section": "Held", "item": "India VIX regime thresholds", "report": "User: VIX is for exit/size, not selection", "live": "Not a SELECT input. Hold on VIX & 22k", "status": "HELD"},
    {"id": "G-UPSTOX", "section": "Execution", "item": "Live LTP from Upstox", "report": "Wired; user later saw zeros", "live": "No fake REF fill. Missing quote = no buy, P&L 0 until live LTP", "status": "MISSING"},
    {"id": "G-KITE", "section": "Execution", "item": "Zerodha Kite Connect live", "report": "Priority 1 capability gap", "live": "Paper only", "status": "MISSING"},
    {"id": "G-FII-FEED", "section": "Held", "item": "FII cash stress feed", "report": "Needed for L2/L3/L4 split in the Excel lock", "live": "Parked until owner starts FII review", "status": "HELD"},
]


def payload() -> dict:
    counts: dict[str, int] = {}
    for row in GAPS:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"ok": True, "n": len(GAPS), "counts": counts, "rows": GAPS}
