# Connect your banks with Plaid

Holdfast talks to Plaid with **your** keys. Nobody else's account sits between you and your bank
data, and the project never sees it. That means a few steps happen in Plaid's dashboard. Holdfast
checks each one for you afterwards.

Don't want a Plaid account? Use SimpleFIN or import statements instead. Both work without it.

## What you do once

1. **Create a Plaid account** at <https://dashboard.plaid.com/signup> and verify your email.
   You get Sandbox keys straight away.
2. **Copy your keys** from Dashboard → Developers → Keys into `.env`:

   ```
   PLAID_ENV=sandbox
   PLAID_CLIENT_ID=
   PLAID_SECRET_SANDBOX=
   ```

   Then restart: `make down && make up`.
3. **Run the check.** Signed in as the household owner, call `POST /api/plaid/setup/check`
   (the Connections page will do this for you once it ships). Fix whatever step is not `done`.
4. **Try it in Sandbox first.** Link a test bank with `user_good` / `pass_good`
   ([all test users](../ops/plaid-sandbox.md)). Sandbox is unlimited and fake.
5. **Apply for the Trial plan** at <https://dashboard.plaid.com/trial-plan> when Sandbox works.
   It is free and gives you real data for up to 10 bank logins.
6. **Switch to real data:** add `PLAID_SECRET_PRODUCTION`, set `PLAID_ENV=production`, restart,
   and run the check again. Production runs only in a real-data deployment: `make up-prod`
   (`infra/docker-compose.prod.yml`) or the `holdfast-instance` project (`docs/ops/instances.md`).
   Both set `HOLDFAST_REAL_DATA=true`. The dev stack (`make up`) and the sandbox refuse it.

## What the check tells you

| Step | Passes when | If it doesn't |
|---|---|---|
| `keys_present` | The client ID and this environment's secret are set | Add the missing one to `.env` and restart |
| `keys_valid` | Plaid accepts them | `INVALID_API_KEYS`: wrong value, or a Sandbox secret used for Production. `plaid_unreachable`: check the server's internet access |
| `products` | At least one product in `PLAID_PRODUCTS` is on your plan | Products your plan lacks are listed as `not_enabled` and skipped. Enable them under Dashboard → Settings → Products, or drop them from `PLAID_PRODUCTS` |
| `redirect_uri` | `PLAID_REDIRECT_URI` is on Plaid's allowlist, or unset | `not_registered`: add the exact URI under Dashboard → Developers → API → Allowed redirect URIs. `https_required`: Production needs HTTPS |
| `item_slots` | You have Trial slots left | `no_slots_left`: all 10 are used |

## Two things to know before linking real accounts

- **You get 10 logins, ever.** Removing a bank does not give the slot back. Plan the 10 first, and
  when a bank asks you to sign in again, use **Relink**, which reuses the same slot.
- **The redirect URI is optional on a desktop browser.** You need it to link from a phone. Use the
  `suggested_redirect_uri` the check returns, register it in the dashboard, set
  `PLAID_REDIRECT_URI` to the same value and restart.

Your keys stay in `.env` on your server. Holdfast never logs them, shows them or sends them
anywhere but Plaid.

## Accepted risk: Plaid Link script without SRI

Holdfast loads Plaid Link from `https://cdn.plaid.com/link/v2/stable/link-initialize.js` with no
Subresource Integrity hash. Plaid ships that file as a moving "stable" target and publishes no
hashes, so a pinned hash would break Link on every Plaid release. We accept the risk, with these
mitigations:

- The production CSP has no `unsafe-inline` for scripts, and allows scripts only from Holdfast
  itself and `cdn.plaid.com`.
- The script is added only when an owner opens the link sheet on Settings -> Connections, which
  follows a step-up check. Nothing loads it on any other page.
- The Link token and public token are never stored by Holdfast. The one exception is the Link
  token held in `sessionStorage` while a bank's OAuth page is open, cleared when Link exits,
  succeeds or is released.

Open item for the P10 threat model: the CSP allowance for `cdn.plaid.com` is site-wide, so any
page could load a script from that host, not just the Connections page.

## Linking from a phone or an OAuth bank

Banks that sign you in on their own site send the browser back to your redirect URI. Register the
`suggested_redirect_uri` from the check (it ends in `/plaid/oauth-return`), set `PLAID_REDIRECT_URI`
to the same value and restart. Holdfast keeps the link's token in this tab's `sessionStorage` only
while the bank's page is open, and picks the link up again on return. If the tab was closed in
between, start the link again.
