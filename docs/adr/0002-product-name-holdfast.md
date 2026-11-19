# 0002. Product name: Holdfast

- Status: Accepted
- Date: 2026-11-18
- Deciders: owner (brand direction), project engineer

## Context
PLAN.md v1.0 used the working name "Ledgerline". The owner's brand direction (`docs/brand/Holdfast_Brand_Direction.pdf`) names the product **Holdfast**: the root that anchors kelp, meaning everything you own anchored to hardware you control.

## Decision
The product, repo, packages, containers and docs are named `holdfast`. PLAN.md was renamed in Revision 1.1. Python package `holdfast` (backend app module stays `app` per the PLAN layout). Containers: `holdfast-api`, `holdfast-worker`, `holdfast-web`. DB name/role: `holdfast`.

## Alternatives considered
- Keep "Ledgerline": superseded by the brand work.

## Consequences
Before a public launch, confirm the trademark and domain (`holdfast.app` or similar) are available. That's an owner to-do in STATUS.md.

## Addendum (2026-11-18): GitHub namespace
The `holdfast` GitHub user/org is taken (also `holdfast-dev`, `holdfast-labs`). Use **org `holdfast-finance`, repo `holdfast`** → `github.com/holdfast-finance/holdfast`.
- The hyphen makes "holdfast" and "finance" separate search tokens, and "finance" separates us from the other big "Holdfast" results: the game *Holdfast: Nations at War* and the kelp anatomy term.
- The product name stays plain "Holdfast". The qualifier lives only in URLs.
- SEO comes mostly from the repo: description, topics, README H1/tagline, homepage URL and social preview image (see STATUS to-do).
