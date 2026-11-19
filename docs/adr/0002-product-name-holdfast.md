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

## Addendum (2026-11-18): primary domain
An SEO strategist and a brand/marketing strategist reviewed this independently. Both ranked `holdfastfinance.com` and `holdfast.finance` first, but both are registered, as are `holdfast.money`, `holdfastmoney.com`, `getholdfast.com`, `useholdfast.com`, `holdfast.sh` and every common bare-`holdfast` TLD.

Decision, among unregistered names (RDAP/WHOIS, 2026-11-18):
- **Primary: `holdfastfinance.app`**. It matches the spoken brand "Holdfast Finance" and the GitHub org `holdfast-finance`. It has no hyphen, carries the category word that separates us from the game, and .app is HSTS-preloaded (HTTPS-only), which builds trust for a finance product.
  - Install: `curl -fsSL https://get.holdfastfinance.app | sh`, with the script readable at `/install.sh` and its checksum published.
  - Docs, comparison pages and the demo are paths on the primary (`/docs`, `/vs/monarch`, `/demo`), never separate domains, so authority stays in one place.
- **Redirect (301) to the primary:** `runholdfast.com`, the .com fallback that is easy to say on a podcast and reads as "run it yourself". Also `holdfast-finance.com`, a cheap typo catcher that matches the org name exactly.
- **Rejected:** .fund/.capital (sound regulated), .fi (Finland ccTLD, so Google geotargets it), .host/.software/.tech (no finance signal), .cash/.money-style names (read cheap), hyphenated names as primary.
- **Brand usage:** product name "Holdfast"; title tags, org and app-store listings use "Holdfast Finance".
