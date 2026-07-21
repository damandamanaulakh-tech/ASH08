# ASH08 Paper Engine — Phase 4

Own repo **ASH08**. Not AshStocks.

- Paper only
- Size = min(requested, 2.5% × book × governor%)
- Exits: STOP_HIT, TARGET_HIT, GOVERNOR_CUT, ROTATION
- L0–L4: 100 / 70 / 50 / 25 / 15

```bash
python ash08/paper_engine.py --demo --data-dir ash08_data
```
