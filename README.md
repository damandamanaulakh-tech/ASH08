# ASH08 Reviewed 50L Desk

ASH08 is a separate paper-trading product for Indian NSE equities. It combines a validated universe, strict scanner, paper-accounting engine, and Adaptive Risk Governor.

## Approved runtime baseline

- Book value: ₹50,00,000
- SELECT: score >= 67
- WATCH: 60 <= score < 67
- Incomplete mandatory evidence: UNKNOWN
- ADV20 >= 2,00,000 shares/day
- Five-day turnover >= ₹5 crore
- Stale data <= 7 days
- Six-month momentum > 0
- Maximum correlation vs book <= 0.85
- Per-name cap: 2.5% multiplied by governor exposure
- Maximum open positions: 10
- Stop / target / maximum hold: -3% / +6% / 15 trading sessions
- Buy and sell cost assumptions: 0.10% each
- Governor exposure L0/L1/L2/L3/L4: 100/70/50/25/15%

## Run locally

```bash
pip install -r requirements.txt
python api.py
```

The dashboard is served from `/`. Health and configuration are available from `/api/health`.

## Render deployment

The repository includes `render.yaml` with:

- service name `ash08-desk`
- start command `python api.py`
- HTTP health gate `/api/health`
- automatic deploys from the linked branch
- the approved non-secret ₹50 lakh runtime parameters

Secrets such as Upstox and Supabase credentials must be configured only in Render environment settings and must not be committed.

## Important operating rule

Core scanning remains blocked until real point-in-time ADV20, turnover, momentum, quality, correlation, and freshness evidence is populated. ASH08 does not invent missing metrics, prices, fills, or profits.

## Main components

| Path | Role |
|---|---|
| `api.py` | HTTP API, dashboard serving, request validation and mutation controls |
| `ash08/config.py` | Central versioned runtime parameters |
| `ash08/universe.py` | Discovery and strict Core universe construction |
| `ash08/scanner.py` | SELECT / WATCH / REJECT / UNKNOWN evaluation |
| `ash08/paper_engine.py` | Durable paper orders, positions, exits, cash and P&L |
| `ash08/upstox_client.py` | Instrument master, exact keys and live quotes |
| `desk/` | Truth-first browser dashboard |
| `tests/` | Reviewed-baseline regression tests |
