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
   and run the check again.

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
