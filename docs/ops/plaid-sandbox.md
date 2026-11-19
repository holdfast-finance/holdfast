# Plaid sandbox test users

These are Plaid's **public** sandbox test logins (the same for every Plaid developer, listed in Plaid's
Sandbox Studio and docs). They aren't secrets and only work against `PLAID_ENV=sandbox`.
Use them in Link, or with `/sandbox/public_token/create` + `options.override_username` for
`make seed` and integration tests. MFA code is `123456` for all of them.

| Username | Password | What you get | Use it for |
|---|---|---|---|
| `user_good` | `pass_good` | Default multi-product test user | Happy-path link, smoke tests |
| `user_transactions_dynamic` | `pass_good` | Realistic transaction history that refreshes over time | Phase 2 `/transactions/sync` cursors, incremental sync, demo data |
| `user_yuppie` | `pass_good` | Young-professional spending | Categorization, budgets demo |
| `user_small_business` | `pass_good` | Small-business transaction pattern | Rules/categorization edge cases |
| `user_credit_joint_account` | `pass_good` | Two salary streams, joint account | Two-person household, per-person visibility (PLAN 20.3) |
| `user_ewa_user` | `pass_good` | Earned-wage-access recurring payouts | Irregular income in forecasts |
| `user_bank_income` | `{}` | Wide variety of income streams | Income detection / forecasts |

Sandbox Studio also lists **profiles** (College Student, Small Business Owner, Retiree, Affluent Investor,
Default Returning User, Trusted Multi-item, OAuth Multi-item, Risk Review), which bundle a phone number and
pre-connected Items for testing Link's returning-user flows. Find them under Dashboard → Build → Sandbox → Profiles.

**Not here:** the client ID and the sandbox/production secrets. Those live only in `.env`
(`PLAID_CLIENT_ID`, `PLAID_SECRET_SANDBOX`, `PLAID_SECRET_PRODUCTION`), and the app never logs them (hard rule 3).
Sandbox Items don't count toward the 10-Item production limit.
