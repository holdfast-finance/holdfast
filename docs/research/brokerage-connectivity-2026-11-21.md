# Brokerage accounts in Holdfast: routes, constraints and a recommendation

Research pass for Keel, 2026-11-21. Every URL below was read on **2026-11-21** unless a line says otherwise.
"UNVERIFIED" means I could not confirm the claim from the provider's own page in this pass (page blocked,
truncated, or only a secondary source found). No code was written. No private data was read.

How to read the sourcing: `[P]` = provider's own docs/terms, `[S]` = secondary (community library docs,
forum, comparison site). Several provider portals refuse automated fetches (developer.schwab.com,
support.plaid.com, developer.etrade.com, IBKR campus, tiingo.com); for those I used search-result excerpts
of the same pages or community docs and marked the claim accordingly.

---

## 0. Where the repo stands today

- **Schema exists, thin.** `backend/app/models/investments.py` has `security` (shared reference, keyed on
  `plaid_security_id`, plus ticker/name/type/cusip), `holding` (one row per account + security + day:
  quantity, total cost_basis, value) and `investment_txn` (type in `buy/sell/cancel/cash/fee/transfer`,
  free-text subtype, quantity, price, amount, fees, `external_id`). Quantity is `NUMERIC(28,10)`, price
  `NUMERIC(18,8)`. RLS and `summary_only` handling are already in migration 0002.
- **Missing for brokerage work:** tax lots, lot disposals / realized gains, wash-sale fields, corporate
  actions, option contract fields (underlying, strike, expiry, right, multiplier), non-Plaid security
  identifiers (FIGI/ISIN, per-source ids), currency on security/txn, settlement date, a price-history
  table for securities (PLAN 18.5 defines `price_history` for crypto only), and any source/connector
  column on `investment_txn` beyond `external_id`.
- **Plan coverage:** PLAN 7.2 and roadmap story **P6** already cover Plaid `/investments/holdings/get` and
  `/investments/transactions/get` into `holding` / `investment_txn`. Nothing in PLAN covers brokerage file
  import, direct broker APIs, lots, or security pricing. PLAN 3.2's 10-slot table gives the brokerages no
  slots (5 named, 2 tentative, 3 spare).
- **UI:** `/investments` is in `web/src/app/nav.ts` (phase "2", summary "Holdings, allocation,
  contributions against limits and retirement projection") but `web/src/app/routes.tsx` renders
  `PlaceholderPage` for it. There is no investments API route or service yet.

---

## 1. Comparison table

| Route | Coverage | Data depth | Cost | Credential model | Self-host fit | Risk |
|---|---|---|---|---|---|---|
| **A. File import** (CSV positions / transactions / realized G/L, OFX/QFX, 1099-B) | Every broker | Deepest available to a retail user: broker-adjusted basis, lots, wash-sale adjustments (realized G/L and 1099-B); full history in chunks | Free | None. User downloads, Holdfast parses | Excellent: no third party, no ADR for a service, reuses Phase 3 pipeline | Manual; formats drift per broker; each broker is a profile to maintain |
| **B. Plaid Investments** | 3,358 institutions flagged for Investments in Plaid's coverage CSV, including Schwab, Fidelity, Vanguard, Robinhood, E*TRADE, IBKR, Webull, tastytrade, Merrill, M1, Public, SoFi | Holdings with total cost basis, `tax_lots` when the institution supplies them, 24 months of transactions, option contract fields; no realized gains, no wash sales | Free on Trial (10 Items lifetime); per-Item monthly subscription after upgrade | OAuth at Schwab/Fidelity/Robinhood; Plaid holds access; Holdfast stores a Fernet-encrypted access token | Good, already designed (ADR 0023, BYO keys) | Each login burns 1 of 10 slots; Schwab allows one active Item per user per app; 12-month consent refresh; depth varies by institution |
| **C. SnapTrade Personal** (BYO key) | Schwab, Fidelity, Vanguard, Robinhood, E*TRADE, IBKR, Webull, tastytrade, Public, Coinbase, Kraken, Empower, Chase and more | Accounts, balances, positions, transactions, orders; tax lots on some brokers | Personal key free "currently", up to 20 connections | User connects brokers in SnapTrade's flow (OAuth where the broker has it, SnapTrade-held encrypted credentials otherwise); Holdfast stores client id + consumer key | Good for self-host (same BYO model as Plaid; Sure and Wealthfolio both ship it). Cloud would need the $100/mo commercial plan | New external service (ADR). "Free for now" pricing. Same key can trade where the broker allows, so read-only is by Holdfast's conduct, not by scope. Some brokers go through credential-holding, not OAuth |
| **D1. IBKR Flex Web Service** | Interactive Brokers only | Best of any API: trades, closed lots, wash-sale flags, corporate actions, cash transactions (per IBKR reporting guides) | Free with account | Report-only token, lifetime chosen 6 h to 1 year | Excellent: read-only by design, plain HTTPS + XML | IBKR-only; reports are batch, 10 req/min |
| **D2. tastytrade Open API** | tastytrade only | Positions, balances, transactions | Free with account | Personal OAuth app; `read` scope separate from `trade`; refresh token does not expire | Excellent: true read-only scope | Broker-specific build |
| **D3. Public.com Individual API** | Public only | Portfolio, history, tax-lot endpoints listed | Free, personal non-commercial use | Long-lived secret → 15-minute access token | Good | Read-only scoping UNVERIFIED |
| **D4. Schwab Trader API (Individual)** | Schwab incl. thinkorswim accounts | Balances, positions, transactions (max 1-year range per call), orders | Free with account | OAuth; access token ~30 min, refresh token 7 days, then a browser re-login | Poor: weekly manual re-auth; per-user developer app with approval wait | No read-only scope found (UNVERIFIED): the token can place orders. Cost-basis/lot fields UNVERIFIED |
| **D5. E*TRADE API** | E*TRADE only | Accounts, balances, portfolio, transactions | Free with account | OAuth 1.0a; token goes inactive after 2 h idle; daily expiry at midnight ET UNVERIFIED | Poor: frequent re-auth | Same trading-capable token concern |
| **D6. Coinbase / Kraken read-only keys** | Crypto (already PLAN 18.4 via CCXT) | Balances, fills, ledger | Free | Coinbase key has an explicit View permission and a permissions endpoint | Excellent, already planned | Low |
| **D7. Robinhood** | Crypto API only (official). No official equities API for individuals found | — | — | Unofficial private API needs the account password + MFA | **Not acceptable** (hard rules: no scraping, no stored brokerage passwords) | Account restriction risk; use CSV reports + Plaid/SnapTrade instead |
| **E. Enterprise aggregators** (Yodlee, MX, Finicity/Mastercard, Akoya, Vezgo) | Broad | Varies | Sales-quoted; Vezgo starts at a quoted "Business" plan | Contract + compliance onboarding | Poor for one individual | Not obtainable in practice |
| **F. Fidelity / Vanguard direct** | No individual API found (UNVERIFIED as a negative) | — | — | — | Use A, B or C | — |

---

## 2. Route by route

### 2.1 Plaid Investments

What it returns
- `/investments/holdings/get` returns securities (name, ticker, type, CUSIP/ISIN, close price) and holdings
  (quantity, `cost_basis`, `institution_price`, `institution_value`, vested quantity/value). Each holding
  now also carries a **`tax_lots`** array (`institution_lot_id`, `original_purchase_datetime`, `quantity`,
  `purchase_price`, `cost_basis`, `current_value`, `position_type`); "An empty array indicates that
  lot-level data is unavailable for this response." `cost_basis` is nullable. [P]
  https://plaid.com/docs/api/products/investments/ and https://plaid.com/docs/investments/
- `cusip` is nullable and "A verified CUSIP Global Services license is required to receive this data", so
  Holdfast must not key securities on CUSIP from Plaid. [P] same page
- Securities carry `option_contract` (contract type, expiry, strike, underlying ticker) and `fixed_income`
  blocks. [P] same page
- `/investments/transactions/get`: **up to 24 months** before the Item was linked. Types are exactly the six
  already in `INVESTMENT_TXN_TYPES` (`buy, sell, cancel, cash, fee, transfer`) with 44 subtypes including
  `assignment`, `exercise`, `expire`, `dividend`, `qualified dividend`, `dividend reinvestment`,
  `merger`, `spin off`, `split`, `sell short`, `buy to cover`, `margin expense`, `return of principal`,
  `tax withheld`, `contribution`, `distribution`. Sign is inverted (buys positive), which the model already
  handles. [P] same pages
- Not offered: realized gain/loss, closed lots, wash-sale adjustments. (Absence from the API reference; no
  such fields exist.) [P]
- Refresh: Plaid checks at least once per market day; on-demand `/investments/refresh` is a paid add-on
  needing a product request. Teams created on or after 2026-11-18 get async history (30 days at Link, the
  rest signalled by `INVESTMENTS_TRANSACTIONS: HISTORICAL_UPDATE`). Holdfast is poll-first, so the P6 sync
  must tolerate a short first page. [P] https://plaid.com/docs/investments/

Coverage (Plaid's own coverage CSV, generated 2026-10-16, https://plaid.com/documents/us_institution_coverage.csv,
linked from https://plaid.com/docs/institutions/ [P])
- `investments = 1` for: Charles Schwab, Charles Schwab Retirement Plan Center, Fidelity, Vanguard,
  Robinhood, E*TRADE from Morgan Stanley, Interactive Brokers - US, Webull, tastytrade, Merrill, M1 Finance,
  Public, SoFi, Alpaca, Tradier, Firstrade, Morgan Stanley, Betterment, Wealthfront, T. Rowe Price, TIAA,
  and further retirement-plan, HSA, robo-adviser and crypto platforms listed in the same CSV.
- `investments = 0` for Moomoo.
- No separate thinkorswim / TD Ameritrade row exists; thinkorswim accounts sit under the Schwab login
  (UNVERIFIED that Plaid returns them identically).
- The flag says the product is supported, **not how deep the data is**. Whether a given broker fills
  `cost_basis`, `tax_lots`, option legs or full 24 months is UNVERIFIED per broker and can only be learned
  by linking. Third parties warn depth varies by institution and account type. [S]
  https://www.tradesviz.com/blog/auto-import-plaid/

OAuth and access
- Schwab: "Access to Charles Schwab is granted automatically for all customers", up to 5 business days.
  Fidelity: Growth/Custom automatic (~4 weeks after Production); **Pay-as-you-go must request access
  manually**. Whether a Trial team gets Fidelity is UNVERIFIED. [P] https://plaid.com/docs/investments/
- Schwab allows **one active OAuth Item per end user per application**: a second Schwab link invalidates
  the first. Schwab and Fidelity need consent refresh every 12 months. Robinhood uses a data-permissioning
  OAuth flow. Trial teams skip the compliance-centre registration that paid Production needs. [P]
  https://plaid.com/docs/link/oauth/
- Consequence for the slot guard: relinking Schwab must go through update mode, never a new Item.

Plan, price, slots
- Trial: free real Production data, **10 Production Items, removing an Item does not free a slot**, and
  Investments is included. It is for **US/Canada teams created on or after 2026-04-15**. Investments is two
  per-Item monthly subscriptions (Holdings; Transactions adds a second) that start billing on upgrade;
  `/item/remove` ends them. Actual prices are not published. [P] https://plaid.com/docs/account/billing/
- Plaid's two support articles disagree on whether Investments is in the Trial bundle (one lists eight
  products, one lists Auth/Transactions/Balance only); both returned 403 to me, so the conflict is
  UNVERIFIED and ADR 0023's "probe with `/link/token/create`" design is the right answer.
  https://support.plaid.com/hc/en-us/articles/16110502116887 ,
  https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan
- **Every brokerage login is one Item.** Two people at the same broker with separate logins are two Items.
  PLAN 3.2 has at most 3 to 5 slots left after banks, 401(k) and HSA.
- Plaid's Developer Policy forbids sharing client id/secret with third parties, which is why BYO keys is
  the only model (already ADR 0023). [P, via search excerpt] https://plaid.com/developer-policy/

### 2.2 Investment aggregators

**SnapTrade** (the only one an individual can actually get)
- **Personal**: "free for personal projects", no card, **up to 20 brokerage connections**, a Personal API
  key for scripts and tools that access your own accounts; other people cannot use your app under it. Data:
  accounts, balances, positions, transactions, orders. [P] https://snaptrade.com/personal ,
  https://docs.snaptrade.com/docs/personal-vs-commercial ("Personal users are currently completely free")
- **Commercial**: Build $0 (5 connections, for dev/test); Launch **$100/month** plus **$1 per connected user
  per month for Daily (read-only)** or $2 for Real-time (trading included); Tax Lots add-on $0.50 per
  eligible user per month, free for Personal users and on Build keys. Production commercial keys need
  approval. [P] https://snaptrade.com/pricing
- Broker access on the free plan: Vanguard, Robinhood, IBKR read-only and immediate; Fidelity, Empower,
  Chase read-only after ~2 business days' approval; Schwab read-only after ~1 week; E*TRADE, Webull,
  tastytrade, Public, Coinbase, Kraken read and trade. [P] https://docs.snaptrade.com/docs/broker-access-guide.md
  (That page describes commercial keys; whether Personal keys need the same approvals is UNVERIFIED.)
- Tax lots: a `tax_lots` array on supported stock/ETF/fund positions (purchase date, quantity, purchase
  price, cost basis); which brokers support it is listed only in their support matrix. [P]
  https://docs.snaptrade.com/docs/tax-lots
- Credentials: OAuth where the broker offers it; otherwise "credentials are encrypted in transmission and
  storage and not shared with the app". SOC 2 Type II. So for non-OAuth brokers **SnapTrade stores the
  brokerage password**. Holdfast itself never sees it, but the owner should know. [P]
  https://snaptrade.com/personal-security
- Terms on open-source / distributed apps where each user brings a Personal key: I could not find the
  developer terms page (404), so this is UNVERIFIED. Evidence it is tolerated: **Sure** (AGPL) ships a
  SnapTrade provider where each household enters its own client id and consumer key, and its UI text says
  "SnapTrade free tier allows 20 brokerage connections" and "Up to 3 years of transaction history"
  (`config/locales/views/snaptrade_items/en.yml` in https://github.com/we-promise/sure). **Wealthfolio**
  (AGPL) resells SnapTrade-powered read-only sync as "Connect", Essentials $79/year. [P]
  https://wealthfolio.app/connect
- Read-only: SnapTrade has no universally read-only key; brokers marked "Read and Trade" accept orders
  through the same connection. Holdfast would simply never call a trading endpoint, and should say so.

**Yodlee, MX, Finicity (Mastercard Open Finance), Akoya**
- Akoya is the bank-owned network Fidelity routes aggregators through; it publishes three pricing tiers
  with no figures and is for approved data recipients, not individuals. [P] https://akoya.com/pricing ;
  [S] https://developers.byallaccounts.morningstar.com/docs/open_banking_sources
- Yodlee advertises a "no-cost to start" developer option (free data access for up to 100 users); the
  pricing page 404'd for me, so current terms are UNVERIFIED. https://developer.yodlee.com/pricing
- MX and Finicity: pricing is by sales contact; individual access UNVERIFIED and unlikely.
- None publishes terms for self-hosted open-source use. Treat all four as unavailable to a single owner.

**Vezgo**: crypto exchanges, chains and wallets (no brokerages on the pricing page); plans are
"Business Access" (up to 1,000 connections) and Enterprise, both quote-only, no free tier listed. [P]
https://vezgo.com/pricing . CCXT (already in PLAN 18.4) covers the same ground without a vendor.

### 2.3 Direct broker APIs (owner's own account)

- **Interactive Brokers, Flex Web Service.** Token lifetime chosen at creation, 6 hours to 1 year;
  generating a new one invalidates the old; two calls (`SendRequest` → reference code → `GetStatement`);
  limits 1 request/second and 10/minute per token. [P, via search excerpts of]
  https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/ and
  https://www.interactivebrokers.com/docs/web-api/flex-web-service/error-codes
  Activity Flex Queries can include Trades at "Closed Lots" and "Wash Sales" detail, an `LD` code for
  disallowed losses, a wash-sale holding-period field, and a Corporate Actions section with typed codes. [P]
  https://www.ibkrguides.com/reportingreference/reportguide/tradesfq.htm ,
  https://www.interactivebrokers.com/support/faq/content/see-holding-period-date-wash-69646830 ,
  https://www.ibkrguides.com/reportingreference/reportguide/corporateactions.htm
  The 365-day-per-query limit is UNVERIFIED. The token can only fetch reports, so it is read-only by
  construction. Portfolio Performance already ships "Interactive Flex Query Importers", a file-based use of
  the same XML. [P] https://github.com/portfolio-performance/portfolio
  The Client Portal / Web API needs a locally running gateway and a live brokerage session; not suited to
  an unattended server (UNVERIFIED in this pass, not fetched).
- **tastytrade Open API.** Personal OAuth app under My Profile → API; scopes `read`, `trade`, `openid`;
  access token 15 minutes; **refresh tokens do not expire**; a personal app is restricted to your own
  account; third-party use needs tastytrade's review. Free with an account. [P]
  https://developer.tastytrade.com/docs/authentication/oauth2/
- **Public.com.** Individual API for "personal, non-commercial use": a secret key from settings is
  exchanged for a 15-minute access token; endpoints include accounts, portfolio v2, history and tax-lot
  selling. [P, via search excerpts] https://public.com/api/docs/quickstart ,
  https://public.com/api/docs/faq . Whether a read-only key exists is UNVERIFIED.
- **Schwab Trader API (Individual).** Register on the developer portal, create an app requesting "Accounts
  and Trading Production" (and Market Data), wait from "Approved - Pending" to "Ready For Use" (up to a few
  days). Access token ~30 minutes; **refresh token 7 days, "no way to make a refresh token last longer than
  seven days"**, so a human logs in weekly. Transactions: range may not exceed one year per call, default 60
  days. [S] https://schwab-py.readthedocs.io/en/latest/getting-started.html ,
  https://schwab-py.readthedocs.io/en/latest/auth.html ,
  https://schwab-py.readthedocs.io/en/latest/client.html . developer.schwab.com returned 403, so the
  product terms (personal use only, no redistribution), any read-only scope and lot/cost-basis fields are
  UNVERIFIED. The same product includes order placement, so the token is trade-capable.
- **E*TRADE.** OAuth 1.0a (HMAC-SHA1); "your Individual Key will work with any of your E*TRADE accounts";
  a token is inactivated after two hours without requests and must be renewed. Accounts API covers list,
  balances, portfolio, transactions. [P, via search excerpts]
  https://apisb.etrade.com/docs/api/authorization/renew_access_token.html ,
  https://developer.etrade.com/getting-started . Midnight-ET expiry and history depth UNVERIFIED.
- **Alpaca.** Account activities (`FILL`, `DIV` with sub-types such as `DIV.CDIV`, `DIV.ROC`) per its own
  docs. [P] https://docs.alpaca.markets/us/docs/account-activities . Read-only keys UNVERIFIED.
- **Tradier.** Token from the user's API page; `GET /v1/accounts/{id}/gainloss` returns cost basis and
  gain/loss for closed positions, reconciled nightly with the clearing firm; positions carry cost basis and
  date acquired. [P] https://docs.tradier.com/reference/brokerage-api-accounts-get-account-gainloss
- **Webull.** Has an individual OpenAPI application path with App Key/App Secret signing and an account
  positions endpoint. [P] https://developer.webull.com/apis/docs/authentication/IndividualApplicationAPI/
  Read-only scoping UNVERIFIED.
- **Robinhood.** Official: the **Crypto Trading API** (keys created in crypto settings, permissions chosen
  per key, read account information). [P] https://robinhood.com/us/en/support/articles/crypto-api/ .
  I found **no official equities API for individuals**; a 2026 "agentic trading" beta exists (UNVERIFIED
  scope). The unofficial private API (libraries such as robin_stocks) logs in with the account password and
  MFA. I could not find an explicit anti-automation clause in the documents I could open (App Use Standards
  only bars compromising platform security; Customer Agreement text not retrieved), so the contractual
  position is UNVERIFIED. It is excluded here on Holdfast's own rules regardless: it requires storing a
  brokerage password and is scraping in everything but name.
- **Fidelity, Vanguard.** No individual developer API found for either (negative result, UNVERIFIED).
  Fidelity routes third parties through Akoya. Reach them through Plaid, SnapTrade or files.
- **Coinbase.** CDP keys have View / Trade / Transfer permission levels and a key-permissions endpoint
  returning `can_view`, `can_trade`, `can_transfer`, so Holdfast can enforce PLAN 18.0 rule 5. [P]
  https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/data-api/get-api-key-permissions
  **Kraken** permission names not re-read in this pass (UNVERIFIED).

### 2.4 File-based routes (no third party)

- **OFX Direct Connect is effectively dead at the big brokers.** Community and vendor reports: Schwab moved
  retail accounts off Direct Connect; Vanguard dropped OFX direct download; Merrill and Amex ended it in
  2025–2026; Fidelity is replacing it with "Fidelity Access". All [S], no broker's own notice found:
  https://community.quicken.com/discussion/comment/20217509 ,
  https://www.banktivity.com/support/articles/?p=4231 ,
  https://mail.kde.org/pipermail/kmymoney-devel/2025-November/035359.html ,
  https://infinitekind.tenderapp.com/discussions/general-questions/249501-fidelity-access/page/15 .
  Do not build a Direct Connect client.
- **Manual OFX/QFX ("Web Connect") downloads** still exist at several brokers. An investment OFX statement
  carries a position list, a typed transaction list (buy/sell/income/reinvest/transfer/split and option
  actions) and a security list with CUSIPs, each with a stable FITID, which maps cleanly onto
  `investment_txn.external_id`. (From the OFX specification; not re-read in this pass.) Which brokers still
  offer the download, and how far back, is UNVERIFIED per broker; a Vanguard cap of ~18 months is reported
  [S] https://community.quicken.com/discussion/7911300/download-vanguard-transactions-older-than-18-months
- **CSV exports.**
  - Schwab: Transactions CSV (custom range, reported up to two years per export), and a separate **Realized
    Gain/Loss** CSV that carries adjusted cost basis and wash-sale adjustments the Transactions export
    lacks. [S] https://journalplus.co/integrations/schwab ,
    https://support.taxact.com/support/900/export-csv-file-charles-schwab . Lot-detail CSV UNVERIFIED.
  - Fidelity: account history as CSV; Fidelity's FAQ states up to 24 months with 90 days as the default.
    [P, via search excerpt; the page itself redirected]
    https://www.fidelity.com/customer-service/faqs-exporting-account-information . A user reports 5 years
    in 90-day slices [S]. Positions CSV exists (UNVERIFIED columns).
  - Robinhood: custom **account activity reports** as CSV covering brokerage and retirement accounts, by
    date range; 2 to 24 hours to generate; **futures, crypto and spending activity are excluded**; crypto
    has its own CSV. [P] https://robinhood.com/us/en/support/articles/reports-and-statements/
  - Vanguard, E*TRADE, Webull, IBKR (Flex as a file), tastytrade: exports exist; exact columns and limits
    UNVERIFIED. Each needs a sample from the owner (kept outside the repo per ADR 0019).
- **Monthly statement PDFs**: positions and activity, but layout parsing per broker; roadmap already cut
  PDF profiles from Q1. Low value compared with CSV. Use only to reconcile an opening position.
- **Year-end tax forms.** Form 1099-B for a covered security reports date acquired (box 1b), cost basis
  (box 1e) and wash sale loss disallowed (box 1g). [P] https://www.irs.gov/instructions/i1099b . Robinhood's
  consolidated 1099 is a PDF with a CSV of the underlying transactions available, importable to tax
  software. [P] https://robinhood.com/us/en/support/articles/tax-documents-faq . TXF availability per
  broker UNVERIFIED.
- **What files omit.** Transaction CSVs: lots, wash sales, and usually corporate-action detail (a split
  appears as a share adjustment with no ratio). Realized G/L and 1099-B: closed lots only, nothing still
  open; 1099-B wash sales are computed per account for identical securities (CUSIP), so cross-account and
  IRA-triggered wash sales are not on the form ([S] https://www.taxbandits.com/1099-forms/form-1099-b-instructions/ ;
  consistent with the IRS instructions but not quoted from them). Noncovered lots have no reported basis.
  1099-DIV/INT are totals, not per-payment. Options: symbol formats differ per broker (OCC vs custom).

### 2.5 Market data for pricing

The cheapest correct answer first: **the brokerage feed already prices what you hold.** Plaid returns
`institution_price`, `institution_value` and a security `close_price` with every holdings pull [P]
https://plaid.com/docs/api/products/investments/ , and SnapTrade and position CSVs carry market value too.
Daily `holding` snapshots therefore build a price and net-worth history going forward with **no new
external service**. A market-data provider is needed only for (a) history before the first sync,
(b) days when no sync ran, (c) securities imported from files with no value column, (d) benchmarks.

| Source | Free limits | Terms note | Status |
|---|---|---|---|
| Twelve Data | Basic: 8 credits/min, 800/day | Basic is "Internal non-display usage"; EOD global data listed from the Grow plan up | [P] https://twelvedata.com/pricing.md . Sure defaults to it [P] https://github.com/we-promise/sure (`.env.example`) |
| Alpha Vantage | 25 requests/day | Free key does not grant redistribution rights (terms PDF not parsed) | [S] limits; terms UNVERIFIED https://www.alphavantage.co/terms_of_service/ |
| Finnhub | 60 calls/min | Redistribution terms not found | [S], UNVERIFIED |
| Tiingo | not retrieved | not retrieved (503) | UNVERIFIED https://www.tiingo.com/about/pricing |
| Massive (Polygon.io renamed 2025-10-30) | free tier reported as 5 calls/min, EOD, 2 years | Paid individual plans are marked "Individual use only"; free tier absent from the pricing page | [P] https://massive.com/blog/polygon-is-now-massive , https://massive.com/pricing ; free tier [S] UNVERIFIED |
| IEX Cloud | — | Retired 2024-08-31 | [S] https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/ |
| Stooq | undisclosed daily hit limit; now needs a captcha-issued key | No official API or terms page found; wrappers call it personal-use only | [S], UNVERIFIED https://pandas-datareader.readthedocs.io/en/latest/readers/stooq.html |
| Yahoo Finance (unofficial) | no key | Unofficial endpoint. Ghostfolio (`YAHOO` data source) and Sure (`yahoo_finance` provider) both use it; Yahoo's terms not read | UNVERIFIED; it is scraping an undocumented API, which Holdfast refuses elsewhere |
| FRED (rates, CPI, treasury yields) | key required; no numeric limit on the terms page | Must display: "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."; no "FRED" in hostnames; apps serving other users must link the terms; some series are third-party copyright, personal use only | [P] https://fred.stlouisfed.org/docs/api/terms_of_use.html |

Redistribution versus personal display: every free tier above is licensed to the key holder. A self-hosted
install where the household brings its own key and sees prices on its own screen is the personal case.
Holdfast Cloud showing vendor prices to customers is redistribution/display and needs a commercial data
licence; that is a Cloud-only cost and belongs in the ADR. Never bundle or commit price data from these
vendors in the repo or fixtures. **Hard rule 9: each provider adopted needs its own ADR** (or one ADR
naming each provider, its terms URL and the date read). Design a `PriceProvider` interface with the
broker feed as provider zero and keep `price_source` on every stored price (PLAN 18.3 already asks this).

### 2.6 Domain problems the tracker must get right

Tax-rule statements here are from IRS Publication 550's structure (headings confirmed at
https://www.irs.gov/publications/p550 [P]; body text was truncated, so specifics are from secondary
summaries and marked) and Form 1099-B instructions [P] https://www.irs.gov/instructions/i1099b .
None of these numbers or rules may be hard-coded outside `backend/app/tax/config` (hard rule 2).

1. **Broker's numbers are authoritative; Holdfast's are a check.** For covered securities the broker
   reports basis and wash sales to the IRS. Store broker-reported lots/basis/realized gains verbatim with
   their source, compute Holdfast's own only where the broker gives none, and show a mismatch, never
   silently overwrite. This mirrors PLAN 18.6's 1099-DA reconciliation.
2. **Cost-basis methods and lots.** FIFO default, specific identification, average basis for mutual fund
   and DRIP shares are all Pub. 550 topics. The method is per account (often per security class) and is
   set at the broker. Model: `lot` (account, security, acquired_at, quantity, remaining, basis, source,
   covered flag) and `lot_disposal` (proceeds, basis, term, wash adjustment). **This is the same engine as
   crypto `crypto_lot` / `crypto_disposal` (roadmap K2): build one, parameterized by asset kind.**
3. **Wash sales.** Loss disallowed when substantially identical securities are acquired within 30 days
   before or after; includes options/contracts, purchases in an IRA or Roth IRA (Rev. Rul. 2008-5), and a
   spouse's account; the disallowed loss moves into the replacement lot's basis and holding period, except
   in an IRA where it is lost. [S] https://www.investor.gov/introduction-investing/investing-basics/glossary/wash-sales ,
   https://www.kitces.com/blog/irs-shuts-down-wash-sale-evasion-technique/ . Brokers report only the
   same-account identical-security case, so a household-wide detector is real value, but it must be labelled
   an estimate. For a day trader this is the hardest computation in the phase (chains of adjustments).
4. **Corporate actions.** Splits and reverse splits (quantity and per-share basis change, total basis does
   not), spin-offs (basis allocated between parent and child), mergers (cash, stock or mixed; cash-in-lieu
   of fractions), symbol/CUSIP changes, return of capital (reduces basis). Feeds describe these
   inconsistently: Plaid as `transfer` subtypes `split`, `spin off`, `merger`; CSVs as share adjustments.
   Needs a `corporate_action` table and a manual-entry path; do not infer ratios from prices.
5. **Dividends and reinvestment.** Qualified vs non-qualified vs capital-gain distributions; each DRIP
   purchase is a new lot; foreign tax withheld; accrued interest on bonds.
6. **Options.** Contract identity (underlying, expiry, strike, right, multiplier 100), open/close side,
   expiry (premium becomes gain/loss), exercise and assignment (premium folds into the stock's basis or
   proceeds and creates a stock lot), multi-leg strategies, Section 1256 index options and futures (60/40,
   mark-to-market, Form 6781). Start with positions and cash effect only; defer strategy grouping and 1256.
7. **Margin and shorts.** Negative cash balance, margin interest as an expense (`margin expense` subtype),
   short positions as negative quantity (the `holding` docstring already allows it), buy-to-cover, payments
   in lieu of dividends. Net worth must subtract the margin debit.
8. **Cash and sweeps.** Money-market sweep funds appear as a security with price 1.00 and daily buy/sell
   noise; Plaid marks `is_cash_equivalent`. Treat as cash for allocation and suppress sweep churn in the
   activity view. Mutual funds price once a day, trade in fractional units and often use average basis.
9. **Multi-currency.** `security` and `investment_txn` have no currency column; Plaid returns
   `iso_currency_code` per holding and transaction. Add currency now, defer FX conversion (needs an FX
   source, another ADR).
10. **Account wrappers.** Taxable vs traditional IRA / Roth IRA / 401(k) / 403(b) / 457(b) / HSA / 529.
    Gains inside wrappers are not taxable events; contributions and distributions are the tax facts
    (PLAN 9.1 already reads them from `investment_txn`). The wrapper must be a first-class account attribute
    so realized-gain and wash-sale logic can branch on it; `account.subtype` may already carry it.
11. **Transfers between brokers (ACATS).** Lots move with their basis; the receiving broker's feed shows a
    `transfer` in with no basis until the delivering firm sends it. Same matching problem as crypto
    transfer pairing (PLAN 18.4); never treat as sell + buy.
12. **Day-trading volume.** An active trader produces hundreds to thousands of fills a day (partial fills
    each a row). Implications: `investment_txn` needs a `(account_id, date)` index and probably range
    partitioning by year later; ingest in batches through `IngestService`; keep raw fills but serve the UI
    from per-order and per-day rollups; virtualize the activity list (the Transactions page already budgets
    50k rows); `holding` daily snapshots stay small (accounts × positions × days); lot matching must be
    incremental, not recomputed from the start on every sync. Schwab's API caps a transactions call at one
    year and Plaid at 24 months, so deep history for a trader comes from files.
13. **Idempotency (hard rule 6).** Plaid gives `investment_transaction_id`, OFX gives FITID, IBKR gives
    trade ids; **broker CSVs usually give no id**, so the fingerprint-with-ordinal scheme from P2 is
    required, and file-versus-feed overlap needs the same "feed wins" dedupe as I4. Reconciliation rule for
    investments: opening quantity + Σ quantity changes = closing position per security, and cash likewise,
    else `needs_review`.

### 2.7 Legal and safety

- **No scraping, no brokerage passwords** (CLAUDE.md gotchas, security rule "no bank credentials"). Rules
  out the Robinhood private API, any headless-browser download helper, and OFX Direct Connect (which sends
  the username and password from the client). SnapTrade and Plaid hold credentials or OAuth grants
  themselves; Holdfast stores only a token or key, Fernet-encrypted, never logged (hard rule 3).
- **Read-only, never trade or move money.** Enforce by construction where the provider allows it:
  IBKR Flex token (reports only), tastytrade `read` scope, Coinbase `can_view` only (reject keys with
  trade/transfer, PLAN 18.0 rule 5). Where no read-only scope exists (Schwab Trader API, E*TRADE,
  SnapTrade read-and-trade brokers), the only control is that Holdfast's client has no order methods at
  all, plus an architecture test that fails on any order/transfer endpoint path. Say this plainly in the
  UI: "this connection could place trades; Holdfast has no code that does."
- **Agent rule 5 still applies:** advisor tools read `v_holdings_latest` and friends only; account numbers
  redacted before every provider.
- **Not investment advice.** How comparable projects word it and ingest data:

| Project | Licence | Brokerage ingestion | Advice wording found |
|---|---|---|---|
| Ghostfolio | AGPL-3.0 | Manual entry, CSV/JSON import of activities, REST import API, community broker-export converters; prices from Yahoo/CoinGecko | "designed for personal use"; no advice disclaimer found in README (site terms not retrievable) https://github.com/ghostfolio/ghostfolio |
| Portfolio Performance | EPL-1.0 | Per-broker PDF importers, CSV wizard, IBKR Flex Query importer; desktop, no aggregator | not found in README https://github.com/portfolio-performance/portfolio |
| Wealthfolio | AGPL-3.0 (trademarks excluded, CLA) | Free: manual + CSV. Paid "Connect": SnapTrade read-only sync | "Information and analysis, not investment advice." https://wealthfolio.app/connect , https://github.com/afadil/wealthfolio |
| Sure (Maybe fork) | AGPL-3.0 | Plaid (BYO keys), SimpleFIN, **SnapTrade (BYO Personal key)**, Coinbase; prices from Twelve Data or Yahoo | Chat: "AI responses are informational only. Not financial advice!" https://github.com/we-promise/sure |
| Actual Budget | MIT | Budgeting only; no investment tracking found in README | — https://github.com/actualbudget/actual |
| Firefly III | AGPL-3.0 | Budgeting/ledger; no investment tracking found in README | — https://github.com/firefly-iii/firefly-iii |

  Suggested Holdfast line (self-host voice): "Holdfast shows what you own and what it cost. It doesn't tell
  you what to buy or sell, and it isn't investment, tax or legal advice. Your broker's records and tax
  forms are the authority." Put it on the Investments page footer, on any realized-gain or wash-sale view
  ("estimate; your 1099-B governs"), and in the advisor system prompt. Never "CPA", never a buy/sell
  recommendation, never a performance promise. All three AGPL peers confirm AGPL plus BYO-key aggregators
  is an accepted pattern; EPL (Portfolio Performance) code must not be copied into an AGPL tree without a
  licence check, though its importer list is a useful map of formats.

---

## 3. Recommendation

### 3.1 Build order (fastest real value for one owner with accounts at a few brokers)

1. **Schema and domain foundation (one migration, one ADR).** Generalize `security` identity (per-source
   ids, FIGI/ISIN, currency, option contract columns); add `lot`, `lot_disposal`, `corporate_action`,
   `price_history` for securities; add currency, settlement date and source to `investment_txn`; extend
   the type/subtype vocabulary to Plaid's 44 subtypes as the canonical set. Build the lot engine once and
   share it with crypto (roadmap K2).
2. **File importers for the owner's actual brokers** on the Phase 3 pipeline: positions CSV (gives a
   reconciled opening position on day one), transactions CSV, realized gain/loss CSV (lots and wash sales
   for free), investment OFX/QFX. Schwab and Robinhood first if those are the owner's. No third party, no
   slot spent, deepest data, and it is the only route that reaches history older than 24 months.
3. **Plaid Investments (roadmap P6, already planned)** for daily holdings, balances and new activity at the
   brokers that fit in the slot budget, now also persisting `tax_lots` and `institution_price`.
   Fixture-first on Sandbox; depth per broker recorded as *unverified* until one real link is inspected.
4. **Investments page v1**: holdings by account and wrapper, allocation, unrealized gain from
   broker-reported basis, activity list, contributions vs limits (limits from tax config only), a
   "data source and freshness" chip per account, and the disclaimer. Prices from the feed itself.
5. **One read-only direct connector where the owner has that broker**, in this preference order:
   IBKR Flex → tastytrade (`read` scope) → Public. These need no slot, no aggregator and are read-only by
   construction.
6. **SnapTrade Personal as an optional BYO-key connector** if the owner's brokers do not fit in Plaid's
   remaining slots or Plaid's depth at a broker is poor. It is free today, 20 connections, and is how the
   two closest open-source peers do it.
7. **Realized gains, household-wide wash-sale estimate, 1099-B reconciliation**: do with Phase 7 / 10b,
   behind `FEATURE_TAX`, since it shares the 8949 work.

### 3.2 Defer or decline

- **Decline:** Robinhood private API, any scraping, OFX Direct Connect, anything that trades or moves
  money, Yahoo's unofficial endpoint as a default.
- **Defer:** Schwab Trader API (weekly manual re-login and a trade-capable token for little gain over
  CSV + Plaid; revisit only for a heavy thinkorswim user), E*TRADE API, option strategy grouping and
  Section 1256, margin analytics, multi-currency FX, corporate-action automation beyond manual entry,
  statement PDF parsing, a historical price provider (only when backfilled charts are wanted), real-time
  quotes, Yodlee / MX / Finicity / Akoya / Vezgo (not obtainable by an individual), SnapTrade Commercial
  for Holdfast Cloud ($100/month plus $1 per user; a Cloud pricing question).

### 3.3 ADRs needed

1. **Brokerage data strategy and `BrokerageConnector` contract**: route order (files → Plaid → direct →
   SnapTrade), read-only-by-construction rule and the no-order-endpoints architecture test, relation to
   `BankConnector`.
2. **Investment schema and shared lot engine**: security identity without CUSIP, lots, disposals,
   corporate actions, "broker-reported is authoritative" policy, shared with crypto K2.
3. **SnapTrade as an optional external service** (hard rule 9): BYO Personal key, terms read and dated,
   credential-holding disclosure, self-host only until a commercial plan is decided.
4. **Direct broker APIs** (hard rule 9, one ADR with an annex per broker: IBKR Flex, tastytrade, Public),
   token storage under ADR 0015.
5. **Market data providers** (hard rule 9): feed-first pricing, `PriceProvider` interface, each adopted
   vendor with its terms URL, personal-versus-display position, and the Cloud licence gap; FRED notice text.
6. **Investment copy and advice boundary**: disclaimer wording, advisor guardrails, "estimate" labels.
7. **Amend PLAN 3.2 / ADR 0023**: slot plan including brokerages; Schwab's one-Item-per-user rule in the
   relink flow; async investments history.
8. If Schwab's API is ever adopted: a separate ADR, because it breaks the read-only-scope rule.

### 3.4 Questions only the owner can answer

1. Which brokers, exactly, and how many separate logins at each (per person)?
2. Which account types at each: taxable, traditional/Roth IRA, 401(k)/403(b)/457(b), HSA, 529, custodial?
3. Do you trade options? Multi-leg? Index options or futures (Section 1256)? Robinhood futures, event
   contracts or crypto (all excluded from Robinhood's activity CSV)?
4. Margin or short positions?
5. Roughly how many trades per day/year (decides partitioning and whether rollups are v1)?
6. How many Plaid Items are already spent or promised, how many may brokerages take, and was the Plaid team
   created on or after 2026-04-15 (Trial eligibility)? Would you pay Plaid per Item if 10 is not enough?
7. Is a second vendor (SnapTrade) holding brokerage connections acceptable, including storing the broker
   password at brokers without OAuth?
8. How far back should history go (trade history from account opening, or positions from today forward)?
9. Is tax-grade output wanted (realized gains, wash-sale estimate, 1099-B reconciliation) or tracking only?
10. Any non-USD holdings or foreign-listed securities?
11. Will you supply sample exports from each broker to the private directory (ADR 0019) so profiles can be
    built against real layouts?
12. Should Holdfast Cloud ever offer brokerage sync (it changes the SnapTrade and market-data licences)?

### 3.5 Where the phase sits

- Do not move the Q1 finish line (Phase 5 on the critical path). Brokerage work is a preemptible lane.
- **Phase 11a "Investments: track"** = steps 1–5 above. Its hard dependencies are I1/I2 (import pipeline),
  P2 (`IngestService`) and P6 (Plaid investments), all inside Phases 2a–3, so step 1 (schema + lot engine)
  can start as a stretch lane right after Phase 3 and **should land before crypto 10a**, because 10a's
  `price_history`, lot engine (K2) and transfer matching are the same components. Building them for crypto
  first and retrofitting securities would mean two engines.
- **Phase 11b "Investments: connectors"** = step 5–6 (direct APIs, SnapTrade), after 11a and after the
  ADRs; independent of everything else.
- **Phase 11c "Investments: tax"** = step 7, scheduled with Phase 7 and 10b under `FEATURE_TAX`.
- The existing nav entry labels Investments as phase "2"; relabel when the phase is named.

---

## 4. What I could not confirm (summary)

- Plaid: whether Trial includes Investments (two Plaid support pages conflict; billing docs say yes);
  whether a Trial team gets Fidelity; per-broker depth of `cost_basis`, `tax_lots`, options and history.
- Schwab Trader API: official terms, any read-only scope, lot/cost-basis fields (portal returned 403).
- SnapTrade: developer terms on distributed open-source use; whether Personal keys need per-broker
  approvals; which brokers return tax lots; how long Personal stays free.
- Robinhood: exact contractual language on automated access; scope of the 2026 agentic-trading beta.
- E*TRADE midnight expiry and history depth; Public and Webull read-only scoping; Alpaca read-only keys;
  Kraken permission names; IBKR 365-day query limit and Client Portal session rules.
- Per-broker CSV/OFX columns, limits and whether lot-level exports exist (needs the owner's samples).
- Market-data terms for Alpha Vantage, Finnhub, Tiingo, Massive's free tier, Stooq and Yahoo.
- IRS Pub. 550 body text (page truncated); wash-sale specifics above are from secondary summaries.
