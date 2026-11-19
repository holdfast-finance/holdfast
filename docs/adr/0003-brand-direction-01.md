# 0003. Brand: Direction 01 (Futuristic / Decentralized)

- Status: Accepted
- Date: 2026-11-18
- Deciders: project engineer (owner delegated)

## Context
The brand PDF offers three directions: 01 Futuristic (dark glass, Signal mint + Deepwater violet), B Terminal/Brutalist (acid green, mono everything), and C Holo (light, iridescent, friendly).

## Decision
Use **Direction 01**. Tokens and rules are in `docs/brand/BRAND.md`.

## Alternatives considered
- **B (Terminal):** strong with self-hosters, but all-mono UI and stepped charts hurt readability in dense finance views (Transactions, Workpapers) and alienate the couples/FIRE/public-sector niches.
- **C (Holo):** friendliest for mainstream users, but it undersells the self-custody/ownership positioning and the beachhead audience (self-hosters). Its light-first gradients are also harder to keep AA-accessible.

## Consequences
01 maps cleanly onto shadcn CSS variables and supports both dark and light themes. Its status-chip colors double as the sync-health language (a top differentiator). Direction B's ASCII logomark can still be used for CLI/README art.
