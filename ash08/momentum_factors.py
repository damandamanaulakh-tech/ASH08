"""Nifty 200 momentum factor lab.

Close-only panel nse_all_stock_data.csv, 1991-01-02 → 2024-07-05.
AM07 NIFTY_200_TICKERS mapped (190 names). SELECT lock is unchanged.
M1/M2/M4 are evidence. They are not live scanner gates until owner ships them.
"""
from __future__ import annotations

MOM_ASOF = "2024-07-05"
MOM_PANEL = "nse_all_stock_data.csv close-only · 1991-01-02 → 2024-07-05"
MOM_UNIVERSE_N = 190
MOM_SNAPSHOT_N = 189

CAVEATS = [
    "Close-only. No ADV, turnover, CN-022, or AM07 liquidity_score on this panel.",
    "Ends 2024-07-05. Misses the v3 cold window 2024-mid-26 (−4.6 vs EW). 2024 H1 here is HOT — do not read it as now.",
    "Current N200 membership backfilled (survivorship). Same handicap as v3 before PIT.",
    "Monthly equal-weight rotation, 0.10%/side on names that change. Not 15d / 3% stop / ½-Kelly / 5 Cr.",
    "AM07 maps NSE sharpe onto 0–100 as 50+25×raw and saturates at +2. 48 names hit 100 here. Rank used unclipped raw.",
    "NSE factsheet ~29.7% 5y CAGR is the published index (mcap-tilt, semi-annual) — not this EW book and not ASH08.",
]

SNAPSHOT = {
    "spearman_nse_ash": 0.82,
    "spearman_nse_m6": 0.897,
    "overlap_top30": 9,
    "overlap_s6": 22,
    "select68": 154,
    "nse_floor62": 143,
    "quality_full": 189,
    "ash_capped100": 85,
    "nse_saturated100": 48,
    "abs_fail": 34,
    "roc20_pos": 165,
    "above_sma200": 169,
    "px_ge100": 185,
}

BOOKS = [
    {"id": "F1", "label": "6m+12m vol-adj N30 abs", "cagr": 34.57, "maxdd": -26.2, "r2024": 26.3, "pick": "TAKE"},
    {"id": "F2", "label": "6m+12m vol-adj N25 abs", "cagr": 35.62, "maxdd": -25.5, "r2024": 27.5, "pick": "TAKE"},
    {"id": "F3", "label": "6m+12m vol-adj N30 abs+SMA200", "cagr": 34.75, "maxdd": -26.2, "r2024": 26.3, "pick": "TAKE"},
    {"id": "F4", "label": "6m vol-adj only N30 abs", "cagr": 32.57, "maxdd": -24.5, "r2024": 19.3, "pick": "PARK"},
    {"id": "F5", "label": "6m raw N30 abs (ASH08 rank today)", "cagr": 32.93, "maxdd": -27.3, "r2024": 28.2, "pick": "KEEP"},
    {"id": "F7", "label": "AM07 65/35 quality N30 abs", "cagr": 30.06, "maxdd": -27.0, "r2024": 19.5, "pick": "PARK"},
    {"id": "EW", "label": "EW N200 eligible", "cagr": 24.87, "maxdd": -31.2, "r2024": 21.0, "pick": "BENCH"},
]

TOP_NSE = [
    {"symbol": "TRENT", "raw": 4.495, "m6": 85.7, "m12": 225.8, "nse_rank": 1, "ash_rank": 11},
    {"symbol": "MOTHERSON", "raw": 4.146, "m6": 109.9, "m12": 146.7, "nse_rank": 2, "ash_rank": 5},
    {"symbol": "DIXON", "raw": 3.959, "m6": 93.5, "m12": 189.1, "nse_rank": 3, "ash_rank": 8},
    {"symbol": "RVNL", "raw": 3.865, "m6": 176.8, "m12": 305.3, "nse_rank": 4, "ash_rank": 1},
    {"symbol": "HAL", "raw": 3.748, "m6": 97.9, "m12": 205.0, "nse_rank": 5, "ash_rank": 7},
    {"symbol": "CUMMINSIND", "raw": 3.616, "m6": 113.0, "m12": 123.9, "nse_rank": 6, "ash_rank": 3},
    {"symbol": "SOLARINDS", "raw": 3.482, "m6": 78.9, "m12": 224.6, "nse_rank": 7, "ash_rank": 16},
    {"symbol": "OIL", "raw": 3.445, "m6": 91.9, "m12": 201.2, "nse_rank": 8, "ash_rank": 9},
    {"symbol": "ZOMATO", "raw": 3.386, "m6": 68.5, "m12": 179.9, "nse_rank": 9, "ash_rank": 20},
    {"symbol": "ZYDUSLIFE", "raw": 3.329, "m6": 69.4, "m12": 108.7, "nse_rank": 10, "ash_rank": 19},
    {"symbol": "SIEMENS", "raw": 3.314, "m6": 99.1, "m12": 114.5, "nse_rank": 11, "ash_rank": 6},
    {"symbol": "BHEL", "raw": 3.282, "m6": 64.3, "m12": 273.8, "nse_rank": 12, "ash_rank": 22},
    {"symbol": "OFSS", "raw": 3.244, "m6": 148.4, "m12": 173.4, "nse_rank": 13, "ash_rank": 2},
    {"symbol": "BOSCHLTD", "raw": 3.15, "m6": 58.7, "m12": 88.4, "nse_rank": 14, "ash_rank": 25},
    {"symbol": "BAJAJ-AUTO", "raw": 3.116, "m6": 43.8, "m12": 108.5, "nse_rank": 15, "ash_rank": 40},
]

TOP_ASH_M6 = [
    {"symbol": "RVNL", "raw": 3.865, "m6": 176.8, "nse_rank": 4, "ash_rank": 1},
    {"symbol": "OFSS", "raw": 3.244, "m6": 148.4, "nse_rank": 13, "ash_rank": 2},
    {"symbol": "CUMMINSIND", "raw": 3.616, "m6": 113.0, "nse_rank": 6, "ash_rank": 3},
    {"symbol": "INDUSTOWER", "raw": 2.921, "m6": 112.6, "nse_rank": 18, "ash_rank": 4},
    {"symbol": "MOTHERSON", "raw": 4.146, "m6": 109.9, "nse_rank": 2, "ash_rank": 5},
    {"symbol": "SIEMENS", "raw": 3.314, "m6": 99.1, "nse_rank": 11, "ash_rank": 6},
    {"symbol": "HAL", "raw": 3.748, "m6": 97.9, "nse_rank": 5, "ash_rank": 7},
    {"symbol": "DIXON", "raw": 3.959, "m6": 93.5, "nse_rank": 3, "ash_rank": 8},
    {"symbol": "OIL", "raw": 3.445, "m6": 91.9, "nse_rank": 8, "ash_rank": 9},
    {"symbol": "ABB", "raw": 2.47, "m6": 86.9, "nse_rank": 29, "ash_rank": 10},
    {"symbol": "TRENT", "raw": 4.495, "m6": 85.7, "nse_rank": 1, "ash_rank": 11},
    {"symbol": "EXIDEIND", "raw": 2.849, "m6": 84.6, "nse_rank": 21, "ash_rank": 12},
    {"symbol": "VEDL", "raw": 1.98, "m6": 84.0, "nse_rank": 51, "ash_rank": 13},
    {"symbol": "IRCON", "raw": 2.897, "m6": 81.9, "nse_rank": 20, "ash_rank": 14},
    {"symbol": "JSWENERGY", "raw": 2.686, "m6": 80.5, "nse_rank": 22, "ash_rank": 15},
]

CANDIDATES = [
    {"id": "M1", "item": "Replace 6m raw with unclipped 6m+12m vol-adj rank", "verdict": "TAKE", "vs_lock": "scanner momScore = clip(50+200×m6)", "evidence": "F1 34.57% CAGR vs F5 32.93 vs EW 24.87. Top-30 overlap 9/30."},
    {"id": "M2", "item": "Rank top 25–30. Stop using SELECT ≥68 while quality = coverage", "verdict": "TAKE", "vs_lock": "SELECT 68 + quality 35% coverage", "evidence": "189/189 quality=100 so 154/189 pass 68. Floor admits almost every 6m>0 name."},
    {"id": "M3", "item": "Keep absolute-momentum 6m > 0", "verdict": "ALREADY", "vs_lock": "P-MOM hard fail", "evidence": "v3 chose absgate ON in 4/4 windows."},
    {"id": "M4", "item": "close > SMA200 as a SELECT gate", "verdict": "TAKE", "vs_lock": "not wired", "evidence": "F3 34.75% vs F1 34.57. All 30 F1 names above SMA200 on 2024-07-05."},
    {"id": "M5", "item": "Rank on raw sharpe, do not clip 50+25×raw", "verdict": "TAKE", "vs_lock": "AM07 momentum_score clips", "evidence": "48 names saturate at 100. Clipped score cannot rank TRENT vs RVNL."},
    {"id": "M6", "item": "N = 25 concentrated (optional tighten from 30)", "verdict": "TAKE", "vs_lock": "Kelly + 500 slots, not a 30-name book", "evidence": "F2 35.62% / DD −25.5 vs F1 34.57 / −26.2."},
    {"id": "Q1", "item": "Replace coverage-quality with AM07 low-vol + liquidity 35%", "verdict": "PARK", "vs_lock": "QUAL_WEIGHT 0.35 = bar coverage", "evidence": "F7 30.06% lags F1 34.57."},
    {"id": "V1", "item": "Volume-expansion as a membership rule", "verdict": "PARK", "vs_lock": "CN-022 vol≥1 is already a SELECT gate", "evidence": "v3 OFF in all winning windows."},
    {"id": "V2", "item": "ROC20 > 0 / near 20d high", "verdict": "KEEP", "vs_lock": "CN-018 / CN-021 live SELECT", "evidence": "Stay as entry timing, not score. User lock."},
    {"id": "N15", "item": "Shrink book to 15 names", "verdict": "PARK", "vs_lock": "not the ASH08 book", "evidence": "v3 N=15 won only W3. Overall winner N=25."},
    {"id": "F4P", "item": "6m vol-adj only (drop 12m leg)", "verdict": "PARK", "vs_lock": "6m raw today", "evidence": "F4 32.57% and 2024 +19.3 under EW +21.0."},
]

V3_WINDOWS = [
    {"window": "2019", "strat": 22.8, "bench": 14.1, "alpha": 8.8, "pit": 7.4},
    {"window": "2020–21", "strat": 51.2, "bench": 33.9, "alpha": 17.3, "pit": -12.4},
    {"window": "2022–23", "strat": 42.7, "bench": 28.5, "alpha": 14.2, "pit": 0.8},
    {"window": "2024–mid-26", "strat": 4.9, "bench": 9.6, "alpha": -4.6, "pit": -4.5},
]


def payload() -> dict:
    takes = [c["id"] for c in CANDIDATES if c["verdict"] == "TAKE"]
    parks = [c["id"] for c in CANDIDATES if c["verdict"] == "PARK"]
    return {
        "ok": True,
        "asof": MOM_ASOF,
        "panel": MOM_PANEL,
        "universe_n": MOM_UNIVERSE_N,
        "snapshot_n": MOM_SNAPSHOT_N,
        "scanner_lock": "File 3 live — vol-adj 6M+12M, SELECT 62–70 above 200 DMA, 70+ size ×0.50. M1/M2/M4 shipped. 2024-07-05 lab panel unchanged.",
        "snapshot": SNAPSHOT,
        "books": BOOKS,
        "top_nse": TOP_NSE,
        "top_ash_m6": TOP_ASH_M6,
        "candidates": CANDIDATES,
        "v3_windows": V3_WINDOWS,
        "take_ids": takes,
        "park_ids": parks,
        "caveats": CAVEATS,
    }
