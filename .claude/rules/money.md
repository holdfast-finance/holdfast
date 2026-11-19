---
paths:
  - "backend/app/**/*.py"
  - "backend/tests/**/*.py"
  - "web/src/**/*.{ts,tsx}"
---
# Money handling

- Python: `decimal.Decimal` only. Construct from `str`, never from `float` (`Decimal("12.34")`, not `Decimal(12.34)`). Quantize to `Decimal("0.01")` with `ROUND_HALF_EVEN` only at presentation or statement-reconciliation boundaries. Keep full precision internally.
- DB: `NUMERIC(14,2)` for currency, `NUMERIC(28,10)` for crypto quantities and security share counts, `NUMERIC(18,8)` for prices.
- JSON/API: amounts are **strings** (`"amount": "-42.10"`). Pydantic: `Decimal` fields with `json_encoders` / serializers that emit `str`.
- TypeScript: amounts stay strings from the API. For arithmetic, use `decimal.js` (or `big.js`), never `Number`. Format with `Intl.NumberFormat` only at render.
- Sign: outflow negative, inflow positive. Plaid's `amount` is positive for outflows, so negate in `integrations/plaid/` and nowhere else. Add a test for every new ingest path that asserts the sign.
- Transfers (`is_transfer`) are excluded from income/spend metrics. Card payments from checking are transfers. The card purchases are the spend.
- Currency is stored per account. Never sum across currencies without an explicit conversion step.
