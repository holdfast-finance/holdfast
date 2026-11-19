# Domain, email and website infrastructure

Configured 2026-11-19 by Claude Code with the `cf` CLI (OAuth login) and `gh`. No secrets are stored in this repo.

## Domain: `holdfastfinance.app`
- Registrar and DNS: **Cloudflare** (Cloudflare Registrar, at cost). Zone ID `fa68cebcecd93becccef85d0129d9109`. Nameservers `rodney`/`sue.ns.cloudflare.com`.
- DNSSEC: enabled. Cloudflare Registrar publishes the DS record automatically.
- Zone settings: SSL **Full (strict)**, Always Use HTTPS, minimum TLS 1.2, TLS 1.3 on, Automatic HTTPS Rewrites, Opportunistic Encryption. `.app` is HSTS-preloaded, so the whole TLD is HTTPS-only.

## Email: Cloudflare Email Routing (receive-only)
| Address | Forwards to |
|---|---|
| `hello@holdfastfinance.app` | owner's Proton inbox (primary address for all accounts and sign-ups) |
| `security@holdfastfinance.app` | owner's Proton inbox (listed in `/.well-known/security.txt` and SECURITY.md) |
| `privacy@holdfastfinance.app` | owner's Proton inbox |
| catch-all `*@holdfastfinance.app` | owner's Proton inbox |

DNS: Cloudflare-managed MX (`route1-3.mx.cloudflare.net`), SPF `v=spf1 include:_spf.mx.cloudflare.net ~all`, DKIM `cf2024-1._domainkey`. DMARC: `v=DMARC1; p=reject; adkim=s; aspf=r`. The domain doesn't send mail, so spoofed "From: @holdfastfinance.app" mail is rejected.

**To send as hello@ later** (e.g. Proton custom domain): add the provider's DKIM/SPF include, and keep DMARC at `p=reject` only once aligned DKIM passes.

Manage with: `cf email-routing rules list-account -z holdfastfinance.app`, `cf email-routing rules create …`.

## Website: GitHub Pages
- Repo: `holdfast-finance/holdfast-finance.github.io` (public, org site), local clone `~/Documents/CODE/holdfast-site`. A coming-soon page for now. The Astro site (PLAN 19.1) replaces it in Phase 9.
- DNS (DNS-only / grey cloud, so GitHub can issue and renew its Let's Encrypt cert):
  - `@` A → 185.199.108–111.153, AAAA → 2606:50c0:8000–8003::153
  - `www` CNAME → `holdfast-finance.github.io` (GitHub redirects www → apex)
- Pages custom domain `holdfastfinance.app`, HTTPS enforced.
- **Verified domain** for the org (2026-11-19), which prevents takeover by other GitHub accounts. TXT `_github-pages-challenge-holdfast-finance` = `481396e08071f8a5b67f592ee1f1fa`. **Keep this record**, because GitHub re-checks it.
- Org profile: name "Holdfast Finance", website https://holdfastfinance.app, public and billing email `hello@holdfastfinance.app`. Owner: GrayManSam (admin).
- Why not proxied (orange cloud): proxying interferes with GitHub's certificate issuance and renewal, and GitHub Pages already serves over a CDN.

## GitHub org: `holdfast-finance`
- `holdfast-finance.github.io`: public website.
- `holdfast`: product monorepo, private until the open-source milestone (PLAN 17.6). Pages for a private repo needs a paid plan, which is why the site is a separate public repo.

## Owner to-do (UI-only steps)
1. Org settings: require 2FA for members (https://github.com/organizations/holdfast-finance/settings/security).
