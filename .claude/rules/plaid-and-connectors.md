---
paths:
  - "backend/app/integrations/**"
  - "backend/app/api/routes_plaid.py"
  - "web/src/components/PlaidLinkButton.tsx"
  - "scripts/seed_sandbox.py"
---
# Bank connectors (Plaid, SimpleFIN)

- All connectors implement `BankConnector` (`link`, `sync`, `relink`, `health`). Routes and jobs depend on the interface, not on Plaid directly.
- `PLAID_ENV` defaults to `sandbox`. Development and tests use Sandbox or recorded JSON fixtures only. Tests never hit the network (block sockets in `conftest.py`).
- Slot guard before any production `link/token/create`: count Items for the client. At 8 or more, require a typed confirmation phrase. Never allow an 11th. Relink always uses **update mode** (same Item, no new slot).
- `/transactions/sync`: persist the cursor **only after** the DB commit. On `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION`, restart from the last saved cursor. Map pending→posted via `pending_transaction_id`.
- Encrypt access tokens and SimpleFIN access URLs with Fernet before they touch the ORM. The plaintext lives only in the local variable that calls the API.
- Log Plaid `request_id` and `error_code` on every error, and never the token, public_token or full account numbers.
- Errors: `ITEM_LOGIN_REQUIRED` / `PENDING_EXPIRATION` → `needs_relink` + notify. Rate limit / `PRODUCT_NOT_READY` → exponential backoff.
- Each Plaid product sits behind a flag so a product missing from the Trial bundle degrades gracefully.
- Sync Health: every connector reports last success, last error, staleness (> 48h = stale) and post-relink duplicate cleanup counts.
