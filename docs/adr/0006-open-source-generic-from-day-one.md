# 0006. Build generic (multi-household-ready) from day one

- Status: Accepted
- Date: 2026-11-18
- Deciders: project engineer

## Context
PLAN v1.0 describes a single-household private app, then (Sections 17, 19, and the market research) an open-source product for any self-hoster, with a possible hosted tier.

## Decision
Code is written for any household from the first commit. No personal facts (state, banks, names) in code. `household_id` and Postgres RLS on every household table, per-person visibility (PLAN 20.3), and bring-your-own-keys config. The owner's household is just the first deployment.

## Consequences
A little more work in Phase 1 (RLS, visibility) avoids a rewrite before open-sourcing. The owner's own data and config never enter the repo.
