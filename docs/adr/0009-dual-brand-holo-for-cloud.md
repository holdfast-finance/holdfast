# 0009. Dual brand: Holo for Holdfast Cloud and mainstream marketing

- Status: Accepted (amends ADR 0003)
- Date: 2026-11-18
- Deciders: owner (direction), project engineer (implementation)

## Context
ADR 0003 picked Direction 01 (dark glass, Signal/Deepwater) for everything and rejected Direction C
(Holo) because it "undersells self-custody to the self-hoster beachhead". That reasoning still holds
for self-hosters. But PLAN Section 17 plans a paid hosted tier ("Holdfast Cloud") for people who
don't want to run servers. That's a less technical audience that Direction 01's cypherpunk look and
voice don't reach. The owner reviewed the Holo boards (brand PDF pages 9–12) and chose Holo for the
hosted product and its marketing.

## Decision
Two brands, one product, one codebase:

| Edition / surface | Brand | Voice |
|---|---|---|
| Self-host (open source): GitHub README, docs, OSS project site, default self-host app theme | **Direction 01** | Builder-to-builder, a little cypherpunk |
| Holdfast Cloud (paid hosted): marketing site, signup/checkout, hosted app theme, onboarding emails | **Holo** (Direction C) | Warm, plain-English, a little delighted |

- Holo tokens, AA text variants, type and atoms are in `docs/brand/BRAND.md` ("Holo brand").
- The web app gets **brand themes**, not forked components. Components use only semantic tokens
  (`primary`, `secondary`, `success`, `warning`, `destructive`, `chart-actual`, `chart-forecast`,
  …). A theme is a CSS variable set under `[data-brand="signal"]` or `[data-brand="holo"]`, each with
  light and dark modes. Edition sets the default (`VITE_EDITION=selfhost` → signal, `cloud` → holo).
  Self-hosters can also pick Holo in Settings → Appearance; it costs nothing once themes exist.
- The money font is a theme token: JetBrains Mono (Direction 01) or DM Mono (Holo). Both are tabular.
- The Holo boards' copy was written for self-hosting ("lives on your own server — not ours",
  `curl … | sh`, hardware-wallet signing, tax module). Cloud copy must stay **truthful for the
  hosted edition**: data is on Holdfast's servers (encrypted, exportable, deletable), crypto is
  watch-only permanently (PLAN 18), the tax module is excluded (PLAN 17), and the CTA is
  signup/waitlist, not a shell command.

## Alternatives considered
- **Holo everywhere:** loses the self-hoster beachhead (ADR 0003's original reason).
- **Direction 01 everywhere:** reads as a developer tool to mainstream buyers. The owner rejected it for Cloud.
- **Separate Cloud frontend:** two UIs to maintain and drift between. Themes give the same result at a fraction of the cost.

## Consequences
- Phase 1+ UI must stay token-pure (it already must): no brand-hue names (`bg-signal`) in
  components, only semantic ones. The ui-brand-reviewer checks both themes.
- Theme plumbing (`data-brand`, Holo token set, fonts, AA test for both themes) lands as a small
  web task after Phase 1, before more screens are built. The Cloud marketing site is built when the
  hosted tier is scheduled (PLAN 17.6). It goes on Cloudflare Pages, not GitHub Pages (commercial
  use, PLAN Section 19), and needs no backend.
- The hosted tier itself still needs everything in PLAN 17.3/17.6 (LLC, legal review, Plaid
  Production, Safeguards program, multi-tenant hardening). This ADR is a brand decision only.
