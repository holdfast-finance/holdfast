# 0041. Forecast f1: recurring schedule plus residual level, empirical-quantile bands, honest backtest

- Status: Accepted (Keel, 2026-11-24)
- Date: 2026-11-24
- Deciders: Lattice (architect); Keel

## Context
PLAN 8.7 asks for a hybrid cash-flow forecast: recurring items scheduled deterministically, ETS
on weekly residual spend, P10/P50/P90 bands, and a monthly backtest with MAPE. Differentiator 5
(PLAN 20.2) promises a *published* backtest error. The Ledger memo (roundtable 04 §4–5) points out
that MAPE is undefined near zero, and that ETS bands on sparse weekly data are fragile. It proposes
WAPE, P10–P90 coverage and a seasonal-naive baseline instead. ROADMAP B3 adopts that.

Two traps:
- **Leakage.** Recurring series are detected on the full history. A backtest that uses today's
  series at a past origin knows the future.
- **Transfers.** Metrics drop transfers (ADR 0040). But a paired payment from checking to a
  mortgage or brokerage account still lowers the cash a household can spend.

## Decision
1. **What is forecast:** the **liquid position** per currency, meaning the sum of balances on
   visible `depository` and `credit` accounts (cards count as negative). Moves between two liquid
   accounts (card payments, checking to savings) cancel out. Moves to non-liquid accounts count as
   outflows.
2. **Flows used:** live, posted rows on liquid accounts. A transfer leg is dropped only when its
   partner (by `transfer_group_id` or a visible `transfer_link`) is on a liquid account. This is
   the one loader that reads `transaction` directly instead of `v_ledger_lines`.
3. **Model f1:**
   - Recurring series with tier `bill`, not dismissed and not `possibly_cancelled` are projected
     on their cadence at `typical_amount`.
   - Residual = liquid flows minus the recurring members. Its weekly level is the mean of the last
     13 weekly totals, spread evenly over days.
   - Bands come from the empirical quantiles (P10/P50/P90) of the cumulative residual error at
     each horizon *h* weeks, over rolling origins in the history. Past the longest horizon with at
     least 8 error samples, quantiles scale by √(h/H).
   - P50 includes the median error, so it is a median, not a mean.
   - The forecast is computed **on request** in the reader's session. Nothing is stored.
4. **Honest backtest (stored):**
   - The monthly job `forecast.backtest` uses up to 12 monthly origins. At each origin it re-runs
     recurring detection on rows up to that origin only (in the ADR 0029 process pool), builds the
     f1 forecast, and compares it with what happened.
   - Metrics per horizon (30 days = 4 weeks, 90 days = 13 weeks):
     - WAPE on weekly net flow
     - MAPE over weeks with |actual| ≥ a floor
     - P10–P90 coverage of the cumulative change
     - the WAPE of a seasonal-naive baseline: the same week 52 weeks earlier, or the trailing
       4-week mean when the history is shorter
   - Results are stored per person and shown on the Forecast page.
5. **The bar:** on Sounding's synthetic holdout, model WAPE < baseline WAPE at both horizons, and
   coverage between 0.70 and 0.90.

## Alternatives considered
- **ETS (statsmodels) with model bands:** a heavy dependency, floats inside, and fragile intervals
  on short weekly series. The empirical errors are honest by construction.
- **Forecasting each account:** card payments would have to be modelled as flows between
  accounts. Deferred (Phase 5 contract OQ-9).
- **Storing forecasts:** they'd be stale between runs, and they'd add per-person tables. The
  computation is cheap once the recurring series exist.
- **Backtesting with today's series:** cheaper, but it leaks the future. Rejected, because the
  number is a published promise.

## Consequences
- The backtest is CPU-heavy (up to 12 detections per person), so it runs monthly and on demand,
  never on every sync.
- Paired recurring payments to non-liquid accounts (mortgage, brokerage) aren't scheduled.
  Recurring detection ignores transfers (ADR 0028), so they land in the residual and widen the
  bands. Accepted for f1 (Phase 5 contract OQ-10).
- The model version (`f1`) is stored with every backtest, so a later model's numbers aren't mixed
  with older ones.
- What-if (PLAN 8.7) is cut from Q1. It would add hypothetical occurrences to the same schedule.
