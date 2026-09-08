"""Locked Adaptive Governor YoY + Big Events.

Source: AM07_AdaptiveGovernor_YoY_BigEvents_Report.xlsx v1.0
Period 2007-01-08 → 2022-12-08 · 3315 days · EW internals · ZERO costs in this run.
This is evidence. It is not the live 5 Cr name book.
"""
from __future__ import annotations

GOV_LOCK = {
    "id": "adaptive_risk_governor_v1.0",
    "lock_date": "2026-07-19",
    "period_start": "2007-01-08",
    "period_end": "2022-12-08",
    "days": 3315,
    "source": "AM07_AdaptiveGovernor_YoY_BigEvents_Report.xlsx",
    "module": "adaptive_risk_governor.py",
    "costs_in_this_run": False,
    "universe": "equal-weight market internals (not N200 stock book)",
    "oos_gap": "2023–2026 not included",
    "build": "ash08-5cr-kelly-v1+yoy",
}

FULL_PERIOD = [
    {"metric": "Final Equity", "market": "6.63x", "governor": "14.77x", "diff": "+122.7%", "verdict": "OUTPERFORM"},
    {"metric": "CAGR", "market": "15.46%", "governor": "22.71%", "diff": "+7.25 pts", "verdict": "OUTPERFORM"},
    {"metric": "Max Drawdown", "market": "-70.4%", "governor": "-48.1%", "diff": "+22.3 pts better", "verdict": "MAJOR IMPROVEMENT"},
    {"metric": "Avg Exposure", "market": "100%", "governor": "94.9%", "diff": "-5.1 pts", "verdict": "RISK CONTROLLED"},
    {"metric": "Days < 100% Exposure", "market": "0", "governor": "262", "diff": "262 / 3315", "verdict": "USED SPARINGLY"},
]

SEVERITY = [
    {"level": "L0_NORMAL", "days": 3062, "pct_time": 92.4, "exposure": 100},
    {"level": "L1_DAMAGE_ONLY", "days": 46, "pct_time": 1.4, "exposure": 70},
    {"level": "L2_CONFIRMED", "days": 22, "pct_time": 0.7, "exposure": 50},
    {"level": "L3_HIGH_SEVERITY", "days": 134, "pct_time": 4.0, "exposure": 25},
    {"level": "L4_EXTREME", "days": 51, "pct_time": 1.5, "exposure": 15},
]

LOCKED_LEVELS = [
    {"level": "L0", "name": "NORMAL", "condition": "No DAMAGE_CLUSTER_5IN10", "exposure": 100},
    {"level": "L1", "name": "DAMAGE_ONLY", "condition": "DAMAGE = True, 0 FII confirms", "exposure": 70},
    {"level": "L2", "name": "CONFIRMED", "condition": "DAMAGE = True, 1 FII confirm", "exposure": 50},
    {"level": "L3", "name": "HIGH_SEVERITY", "condition": "DAMAGE = True, ≥2 FII confirms", "exposure": 25},
    {"level": "L4", "name": "EXTREME", "condition": "DAMAGE + Q10 + Sell Cluster", "exposure": 15},
]

LOCKED_FLAGS = [
    {"id": "DAMAGE_CLUSTER_5IN10", "role": "internal damage", "live": "proxied by peak DD ladder, FII stream missing"},
    {"id": "FII_CASH_STRESS_Q10", "role": "FII confirm / L4", "live": "NOT WIRED — no FII feed"},
    {"id": "FII_SELL_CLUSTER_7IN10", "role": "FII confirm / L4", "live": "NOT WIRED — no FII feed"},
    {"id": "FII_ANY_CONFIRM", "role": "L2 vs L1 split", "live": "NOT WIRED — no FII feed"},
    {"id": "REPAIR_AFTER_DAMAGE_CANDIDATE", "role": "stepwise +25%/day max", "live": "NOT WIRED — live jumps by DD only"},
]

YOY = [
    {"year": 2007, "days": 169, "mkt_cagr": 114.05, "strat_cagr": 113.12, "alpha": -0.93, "mkt_dd": -7.8, "strat_dd": -7.8, "dd_improve": 0.0, "avg_exp": 99.1, "defensive_days": 4},
    {"year": 2008, "days": 220, "mkt_cagr": -65.56, "strat_cagr": -25.29, "alpha": 40.27, "mkt_dd": -64.7, "strat_dd": -30.4, "dd_improve": -34.3, "avg_exp": 65.5, "defensive_days": 108},
    {"year": 2009, "days": 213, "mkt_cagr": 155.2, "strat_cagr": 163.68, "alpha": 8.47, "mkt_dd": -23.9, "strat_dd": -18.7, "dd_improve": -5.3, "avg_exp": 89.9, "defensive_days": 41},
    {"year": 2010, "days": 215, "mkt_cagr": 27.4, "strat_cagr": 27.4, "alpha": 0.0, "mkt_dd": -12.7, "strat_dd": -12.7, "dd_improve": 0.0, "avg_exp": 100.0, "defensive_days": 0},
    {"year": 2011, "days": 213, "mkt_cagr": -34.64, "strat_cagr": -39.0, "alpha": -4.36, "mkt_dd": -31.5, "strat_dd": -35.4, "dd_improve": 3.9, "avg_exp": 98.3, "defensive_days": 7},
    {"year": 2012, "days": 208, "mkt_cagr": 26.79, "strat_cagr": 26.79, "alpha": 0.0, "mkt_dd": -10.8, "strat_dd": -10.8, "dd_improve": 0.0, "avg_exp": 100.0, "defensive_days": 0},
    {"year": 2013, "days": 218, "mkt_cagr": -13.88, "strat_cagr": -14.44, "alpha": -0.57, "mkt_dd": -32.2, "strat_dd": -32.6, "dd_improve": 0.4, "avg_exp": 99.2, "defensive_days": 6},
    {"year": 2014, "days": 210, "mkt_cagr": 82.24, "strat_cagr": 77.85, "alpha": -4.39, "mkt_dd": -9.0, "strat_dd": -9.0, "dd_improve": 0.0, "avg_exp": 99.1, "defensive_days": 6},
    {"year": 2015, "days": 210, "mkt_cagr": 25.7, "strat_cagr": 32.85, "alpha": 7.15, "mkt_dd": -14.4, "strat_dd": -12.7, "dd_improve": -1.7, "avg_exp": 97.3, "defensive_days": 7},
    {"year": 2016, "days": 215, "mkt_cagr": 0.53, "strat_cagr": -5.21, "alpha": -5.74, "mkt_dd": -21.7, "strat_dd": -22.6, "dd_improve": 0.9, "avg_exp": 92.4, "defensive_days": 23},
    {"year": 2017, "days": 212, "mkt_cagr": 58.44, "strat_cagr": 58.44, "alpha": 0.0, "mkt_dd": -7.1, "strat_dd": -7.1, "dd_improve": 0.0, "avg_exp": 100.0, "defensive_days": 0},
    {"year": 2018, "days": 219, "mkt_cagr": -28.23, "strat_cagr": -24.3, "alpha": 3.94, "mkt_dd": -35.0, "strat_dd": -29.9, "dd_improve": -5.1, "avg_exp": 94.7, "defensive_days": 16},
    {"year": 2019, "days": 214, "mkt_cagr": -15.89, "strat_cagr": -15.89, "alpha": 0.0, "mkt_dd": -25.7, "strat_dd": -25.7, "dd_improve": 0.0, "avg_exp": 100.0, "defensive_days": 0},
    {"year": 2020, "days": 223, "mkt_cagr": 29.51, "strat_cagr": 63.44, "alpha": 33.94, "mkt_dd": -44.9, "strat_dd": -28.3, "dd_improve": -16.7, "avg_exp": 92.8, "defensive_days": 26},
    {"year": 2021, "days": 217, "mkt_cagr": 63.89, "strat_cagr": 63.89, "alpha": 0.0, "mkt_dd": -9.6, "strat_dd": -9.6, "dd_improve": 0.0, "avg_exp": 100.0, "defensive_days": 0},
    {"year": 2022, "days": 139, "mkt_cagr": 36.0, "strat_cagr": 21.15, "alpha": -14.84, "mkt_dd": -16.5, "strat_dd": -13.4, "dd_improve": -3.1, "avg_exp": 89.5, "defensive_days": 18},
]

EVENTS = [
    {"event": "2008 GFC Crash", "start": "2008-09-01", "end": "2009-03-31", "days": 118, "mkt_ret": -47.7, "strat_ret": -18.2, "alpha": 29.6, "mkt_dd": -54.4, "strat_dd": -28.6, "dd_saved": 25.8, "min_exp": 15},
    {"event": "2008-09 Lehman Week", "start": "2008-09-08", "end": "2008-10-10", "days": 18, "mkt_ret": -29.2, "strat_ret": -13.5, "alpha": 15.6, "mkt_dd": -29.2, "strat_dd": -13.5, "dd_saved": 15.6, "min_exp": 15},
    {"event": "2011 Euro Stress", "start": "2011-07-01", "end": "2011-12-31", "days": 109, "mkt_ret": -26.7, "strat_ret": -27.3, "alpha": -0.6, "mkt_dd": -31.5, "strat_dd": -32.0, "dd_saved": -0.5, "min_exp": 25},
    {"event": "2013 Taper Tantrum", "start": "2013-05-01", "end": "2013-09-30", "days": 91, "mkt_ret": -13.7, "strat_ret": -14.2, "alpha": -0.5, "mkt_dd": -20.1, "strat_dd": -20.5, "dd_saved": -0.4, "min_exp": 70},
    {"event": "2015 China Scare", "start": "2015-08-01", "end": "2016-02-29", "days": 126, "mkt_ret": -13.0, "strat_ret": -9.9, "alpha": 3.1, "mkt_dd": -21.7, "strat_dd": -22.6, "dd_saved": -0.9, "min_exp": 15},
    {"event": "2016 Demonetization", "start": "2016-11-01", "end": "2017-02-28", "days": 72, "mkt_ret": 1.8, "strat_ret": -2.1, "alpha": -3.9, "mkt_dd": -13.3, "strat_dd": -14.3, "dd_saved": -1.0, "min_exp": 15},
    {"event": "2018 IL&FS / NBFC", "start": "2018-09-01", "end": "2019-02-28", "days": 112, "mkt_ret": -15.9, "strat_ret": -13.0, "alpha": 2.8, "mkt_dd": -20.4, "strat_dd": -17.3, "dd_saved": 3.1, "min_exp": 25},
    {"event": "2020 COVID Crash", "start": "2020-02-15", "end": "2020-04-30", "days": 45, "mkt_ret": -26.5, "strat_ret": -11.3, "alpha": 15.3, "mkt_dd": -41.6, "strat_dd": -23.9, "dd_saved": 17.7, "min_exp": 15},
    {"event": "2020 COVID Recovery", "start": "2020-05-01", "end": "2020-12-31", "days": 150, "mkt_ret": 71.9, "strat_ret": 74.9, "alpha": 3.0, "mkt_dd": -9.4, "strat_dd": -8.1, "dd_saved": 1.3, "min_exp": 25},
    {"event": "2022 Rate Hike Stress", "start": "2022-01-01", "end": "2022-06-30", "days": 93, "mkt_ret": -1.2, "strat_ret": -7.3, "alpha": -6.1, "mkt_dd": -16.5, "strat_dd": -13.4, "dd_saved": 3.1, "min_exp": 15},
]


def payload() -> dict:
    return {
        "ok": True,
        "lock": GOV_LOCK,
        "full_period": FULL_PERIOD,
        "severity": SEVERITY,
        "levels": LOCKED_LEVELS,
        "flags": LOCKED_FLAGS,
        "yoy": YOY,
        "events": EVENTS,
        "note": "14.77x is EW internals 2007–2022 with ZERO costs. Live book is 5 Cr ½-Kelly, 0.10% each side, no FII feed.",
    }
