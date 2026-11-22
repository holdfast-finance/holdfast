# 0030. SimpleFIN connector next to Plaid

- Status: Proposed (revision 2, after Gadfly's S9). It stays Proposed until the owner says yes or
  no to the Bridge subscription. Building it is deferred until after the first Plaid link (Keel,
  2026-11-21).
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel schedules it. Subscribing costs the owner money, so whether
  the owner's own instance uses it is an owner-queue item.

## Context
PLAN 20.2 and 20.3 put a SimpleFIN connector next to Plaid, behind the same `BankConnector`
interface. Hard rule 9 requires an ADR for a new external service. The facts below come from the
public protocol page (`simplefin.org/protocol.html`, version 2.0.0-draft, changelog 2026-03-19) and
the Bridge's developer, pricing and security pages, all read on 2026-11-21:

- **What it is.** SimpleFIN is an open, read-only protocol for sharing balances and transactions.
  Few banks run a SimpleFIN server themselves. In practice the server is **SimpleFIN Bridge**, a
  small paid service that aggregates through MX. Bank credentials go to MX, never to the Bridge and
  never to Holdfast.
- **Cost.** The person subscribes to the Bridge: $1.50 per month or $15.00 per year, plus tax. That
  covers up to 25 institutions and 25 apps. There is no lifetime cap like Plaid Trial's 10 Items, and
  cancelling is reversible. Holdfast never pays and never holds a subscription.
- **Credential model.** The person creates a **setup token** on the Bridge and pastes it into the
  app. The token is base64 of a one-time **claim URL**. A `POST` to that URL returns an **access
  URL** with HTTP Basic credentials inside it (`https://user:pass@host/simplefin`). The access URL
  is the long-lived bearer credential. A second claim of the same token returns `403`, and the
  protocol says to tell the person that the token may be compromised.
- **Data.** `GET <access URL>/accounts?version=2&start-date=&end-date=` returns an account set:
  `errlist` (structured errors), `connections` (one per bank login, `conn_id`), and `accounts`. Each
  account has `id`, `name`, `conn_id`, `currency`, `balance`, optional `available-balance` and
  `balance-date`, plus `transactions`. Each transaction has `id`, `posted` (epoch seconds; 0 when
  pending), `amount`, `description` and optional `transacted_at`, `pending` and `extra`. **Amounts and
  balances are numeric strings**, and a positive amount is money into the account.
- **Quirks:**
  - An account `id` is unique only **within its connection**. A transaction `id` is unique within
    its account.
  - Pending rows are left out unless `pending=1` is sent. The protocol has no pending-to-posted link.
  - One request covers at most **90 days** (`end-date - start-date`). How much history exists
    varies by institution.
  - The quota is about **24 requests a day** per access URL, with some leeway during setup. Going
    past it brings warnings and then a **disabled access URL**. The Bridge asks apps to pick a
    random minute and to overlap windows by about 5 days.
  - Data is refreshed about once a day. `402` means the subscription has lapsed. `403` on
    `/accounts` means access was revoked or the credentials are wrong.
  - `currency` can be a URL naming a custom currency, such as reward points.
  - Apps must show `errlist` messages to the person, sanitized first.
  - The protocol defines no investment holdings. An investment account arrives with a balance only.
  - Trust: the Bridge's security page records an incident at MX (2026-05-28) in which up to 39
    users may have seen each other's transactions. Data passes through two third parties.

## Decision
1. **Build it, as `backend/app/integrations/simplefin/`**, implementing the Phase 2 `BankConnector`
   protocol and writing only through `IngestService` (Phase 2 contract, slices P1 and P2b). It is a
   peer of Plaid, not a fallback.
2. **One `bank_connection` per claimed access URL** (`provider = 'simplefin'`). `external_id` is a
   Holdfast-generated UUID string, because nothing in the access URL is safe to use as an id. A
   Holdfast account's `external_account_id` is `<conn_id>:<account id>`, because account ids are
   unique only per connection. A transaction's `external_id` is the SimpleFIN transaction id,
   unique per account through `(account_id, external_id)`.
3. **Credential storage.** The access URL is sealed as an `hf2` envelope bound to its
   `bank_connection` row (ADR 0015 §6), in `bank_connection.access_token_enc`, exactly like a
   Plaid access token. That needs F5d first (Phase 2 contract). It is opened only inside
   the client call. **It is not a `get_secret` name.** S1 removes `simplefin_access_url` from the
   resolver allowlist, from `Settings` and from `.env.example`. Linked-connection credentials live on
   the connection (ADR 0015 already says so for Plaid), and one deployment can hold several access
   URLs. The setup token is transient: it is pasted in the app, claimed at once, and never stored,
   logged or audited. Logs and audit rows name the connection id only. The access URL is never
   returned by the API, not even masked.
4. **Entry is in the app.** It is owner-only, needs a step-up re-auth (adding a connection, like a
   Plaid Item), and is audited as `simplefin.connect`. Before claiming, the server decodes the token,
   validates the claim URL with `app.security.outbound.resolve_outbound` (ADR 0015 §5: `https` only,
   globally routable, pinned address) and shows the person the host it points at. The access URL
   gets the same check before every fetch. A `403` on claim becomes "this token was already used;
   if you didn't use it, disable it on the Bridge now".
5. **Quirk handling, fixed in S1:**
   - **No pending rows.** S1 never sends `pending=1`. Without a pending-to-posted link, pending rows
     would be orphaned.
   - **Windows.** The initial backfill walks back in 90-day windows for 8 windows (about 24
     months, 8 requests, because one request covers every account). An empty window does **not**
     stop it, because a quiet savings account can have empty quarters. It stops early only when the
     window start is before `extra.account-open-date` for every account that reports one. A
     scheduled sync fetches from `cursor - 5 days` to now, in one request. The cursor is
     `sf1:<epoch seconds>`, the end of the last committed window, and is written in the same
     transaction as the batch (ADR 0017).
   - **Schedule.** One sync per connection every 6 hours, at a minute derived from the connection
     id. A per-connection budget of 20 requests per rolling 24 hours is enforced before every
     request. "Sync now" counts against it.
   - **Errors, keyed per bank login.** One access URL spans up to 25 banks, so status is kept per
     `conn_id`, on `connector_account_link.status_code` for that login's accounts (ADR 0033), never
     on the whole `bank_connection`. `con.auth` marks only that login's accounts `needs_relink`, and
     the other banks keep syncing. `act.*` is a per-account warning in Sync Health. `gen.*` fails
     the run. `402` gives status `error`, code `payment_required`. `403` gives `needs_relink`, code
     `revoked`. Message text is stored only after sanitizing: control characters stripped, length
     capped at 200, rendered as text and never as HTML. It is never logged.
   - **Currency.** An account whose `currency` is not a three-letter ISO code is skipped, and Sync
     Health lists it under `unsupported_currency`. It is never summed and never stored as USD.
   - **Sign.** No negation: SimpleFIN is already outflow-negative. Balances of credit and loan
     accounts are stored owed-negative. S1's first recorded fixture pins the sign the Bridge
     actually uses for a card balance, and a test fails if that assumption changes.
   - **Parsing.** Amounts are parsed with `Decimal(str)` from the numeric string and refused if they
     have more than 2 decimal places. JSON is parsed with `parse_float=Decimal` as a backstop.
   - **Investments.** Balance snapshots only. No holdings.
   - **Categories.** SimpleFIN rows carry no PFC, so they arrive uncategorized until rules
     (Phase 4) categorize them. The Connections copy says so.
6. **When.** After M-live, P6, P7 and M1, and only if the owner subscribes. For this owner it is
   worth it only if the slot plan exceeds 10 logins, or for an institution Plaid lacks: M1 manual
   accounts cover low-activity accounts for free. S1 reuses P1, P2a, P2b and P4a unchanged. It needs
   no migration: `bank_connection.provider` already allows `simplefin`, and
   `connector_account_link` carries per-login status.

## Alternatives considered
- **Plaid only:** the 10-Item lifetime cap forces people with many institutions onto statements, and
  everyone needs a Plaid developer account. That is the friction PLAN 20.2 sells against.
- **Store the access URL as a household setting (`get_secret`):** that allows only one access URL per
  household, and puts a linked credential in the operator-key table, against ADR 0015's split.
- **Request pending rows:** with no pending-to-posted link, every pending row becomes a duplicate
  or needs fuzzy matching. Posted-only costs a day of latency.
- **Keep `SIMPLEFIN_ACCESS_URL` in `.env` as a headless path:** it is a second source for the same
  credential and would need its own claim and rotation story. A CLI claim command can come later if
  someone asks.
- **Direct bank SimpleFIN servers only:** almost no bank runs one.

## Consequences
- The owner decides whether to subscribe (money). Without a subscription, S1 is still built and
  tested against recorded fixtures and the Bridge's reusable demo token.
- People get a no-slot path for institutions that Plaid's Trial would otherwise spend a slot on.
- Data is daily, posted-only, uncategorized and holdings-free, and it passes through two third
  parties. Sync Health shows "updates daily" for SimpleFIN connections.
- S1 edits `app/security/secrets.py` (protected path) to drop the allowlist name, so it needs a
  Bulwark verdict. It depends on F5d (`hf2`).
- Client rules from Bulwark:
  - send `Authorization: Basic` itself, because `resolve_outbound` strips userinfo from the URL
  - `trust_env=False` and `follow_redirects=False`
  - keep the claim body out of all request logging
- Tests: recorded, sanitized JSON only, with the socket guard on. Fixtures cover claim `200` and
  `403`; `/accounts` with `errlist` of each prefix; a custom currency; a 90-day window walk; a
  repeated id across two connections; and replaying the same batch twice, which inserts nothing.
