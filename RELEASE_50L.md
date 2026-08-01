# ASH08 reviewed ₹50 lakh baseline

Approved baseline date: 2026-08-01

## Locked configuration

- Book value: ₹50,00,000
- SELECT: score >= 67
- WATCH: 60 <= score < 67
- Mandatory scanner evidence: 100%; incomplete rows are UNKNOWN
- Per-name cap: 2.5% multiplied by governor exposure
- Maximum open positions: 10
- Default stop / target / hold: -3% / +6% / 15 trading sessions
- Buy and sell cost assumptions: 0.10% each
- Governor exposure: L0 100%, L1 70%, L2 50%, L3 25%, L4 15%

## Implemented in this release branch

- Central configuration shared by API, scanner and engine
- Correct Upstox URL encoding and exact instrument-key usage
- Strict universe and scanner missing-data policy
- Durable, atomic paper-state loading and saving
- Cash, gross exposure, cumulative name headroom and maximum-position enforcement
- Idempotent orders and fresh-price-only marks/fills
- Net P&L after configured costs
- Verified-evidence governor and required-cut plan
- Restored scan, auto-buy, close, mark and governor API routes
- Request limits, JSON validation, rate limits, same-origin CSRF/bearer auth and security headers
- Truth-first dashboard without static market values or unsafe untrusted HTML insertion
- CI checks for compilation, reviewed formulas and literal-placeholder regressions

## Deliberately blocked

The Core universe remains BLOCKED until real ADV20 and five-day turnover evidence is populated. Refreshing the Upstox instrument master creates a validated Discovery universe but does not invent liquidity, momentum, quality or correlation metrics.

No finding is represented as fully verified in production until the branch CI and deployment smoke checks pass.
