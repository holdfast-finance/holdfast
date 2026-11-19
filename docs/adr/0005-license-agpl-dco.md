# 0005. License AGPL-3.0 with DCO

- Status: Accepted
- Date: 2026-11-18
- Deciders: project engineer (per PLAN 17.2)

## Context
The plan is open source first and possibly a hosted tier later. AGPL keeps competitors from hosting closed forks.

## Decision
License the repo **AGPL-3.0-only** (`LICENSE`). Contributions use the **Developer Certificate of Origin** (`Signed-off-by:` trailer, see CONTRIBUTING.md) rather than a CLA for now. The project owner holds copyright in their own contributions and keeps the option to dual-license them.

## Alternatives considered
- BSL / fair-source: blocks competing hosting outright but isn't OSI open source, and the self-host audience cares.
- CLA: stronger relicensing rights, but friction for contributors. Revisit before accepting large outside contributions.

## Consequences
Every dependency must be AGPL-compatible (no SSPL, no non-commercial licenses).
