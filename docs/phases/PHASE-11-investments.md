# Phase 11 — Investments: brokerage accounts

Source: [brokerage connectivity survey, 2026-11-21](../research/brokerage-connectivity-2026-11-21.md). [PLAN §20](../../PLAN.md#20-market-driven-amendments-revision-11) wins on conflicts with earlier PLAN sections.

## 1. Goal and non-goals

Track holdings, trades, dividends, fees, cost basis and lots across brokerage accounts. All brokerage access is read-only.

- **11a Track.** Extend the schema. Build one lot engine for brokerage and crypto. Add file imports, extend Plaid Investments, and ship Investments v1.
- **11b Connectors.** Build at most one direct read-only connector if the owner has an eligible broker. Then consider an optional aggregator.
- **11c Tax.** Add realized gains, a wash-sale estimate and 1099-B reconciliation with Phase 7 and 10b, behind `FEATURE_TAX`.

Non-goals are trading, money movement, scraping, storing a brokerage password, using unofficial or private broker APIs, and giving investment advice. Options start with positions and cash effects. Defer strategy grouping, special-treatment derivatives, margin analytics, FX conversion, real-time quotes, retirement projections, and automated corporate-action interpretation.

## 2. What exists today

- `Security`, `Holding` and `InvestmentTxn` already exist. `Security` is shared reference data with a nullable unique Plaid ID. A holding is one account, security and date with quantity, total basis and value. An investment transaction has six types, a free-text subtype, an external ID, quantity, price, signed cash amount and fees. It has no source field, currency or settlement date. There are no brokerage lots, disposals, corporate actions or security price history. [Investment models](../../backend/app/models/investments.py), [model types](../../backend/app/models/_common.py), [core migration](../../backend/migrations/versions/0002_core_schema.py).
- Holdings and activity already inherit household and person visibility from the account. Other household members can see a `summary_only` account's balance without its detail. The account already carries `owner_person_id`, `subtype`, `tax_treatment` and `currency`. Its current tax-treatment values are coarse. [Account models](../../backend/app/models/accounts.py), [RLS migration](../../backend/migrations/versions/0002_core_schema.py).
- Phase 3 has CSV and OFX parsing primitives and a statement import service. The survey's proposed brokerage parsers and normalization are future work. [CSV parser](../../backend/app/domain/csv_parse.py), [OFX parser](../../backend/app/domain/ofx_parse.py), [statement import service](../../backend/app/services/statement_import.py).
- The Plaid client and setup probe exist. There is no dedicated Investments API or sync service. PLAN §7.2 and P6 already schedule the baseline Plaid investment reads. [`PlaidClient`](../../backend/app/integrations/plaid/client.py), [setup service](../../backend/app/services/plaid_setup.py), [PLAN](../../PLAN.md), [roadmap](../planning/ROADMAP.md).
- `/investments` is labeled Phase 2 in navigation and renders `PlaceholderPage`. The navigation summary promises a retirement projection, which the survey leaves out of v1. [Navigation](../../web/src/app/nav.ts), [routes](../../web/src/app/routes.tsx).

## 3. Sources

Coverage means availability reported by the survey. `UNVERIFIED` keeps the survey's evidence limit. Build entries can still have gates. Broker names are examples from the survey.

| Route | Coverage | Data depth | Cost | Credentials | Status and reason |
|---|---|---|---|---|---|
| CSV positions, activity and realized G/L | Broker exports, one profile per layout | Positions and fills. Realized G/L can include broker-adjusted basis and wash-sale data. History and columns vary. Schwab lot-detail CSV, Fidelity position columns and several other brokers' columns and limits are UNVERIFIED. | Free | Manual download | **Build** in 11a, profiles driven by samples |
| Manual investment OFX/QFX (Web Connect) | Brokers that offer downloads. Availability and history per broker are UNVERIFIED. | Positions, security list, activity and FITIDs in the format. The survey did not re-read the spec. | Free | Manual download | **Build** in 11a, stable transaction IDs |
| IBKR Flex XML export | IBKR only | Flex report sections, depending on query. Export profile details are UNVERIFIED. | Free with account | Manual export | **Defer** until a profile is needed |
| Monthly statement PDF | Broker statements | Positions and activity, layouts vary | Free export | Manual download | **Defer** parsing. Use for opening reconciliation. |
| 1099-B and supporting export | Brokers that issue the form. TXF availability per broker is UNVERIFIED. | Reported disposals, covered basis and reportable adjustments. Open lots are outside the form. | Free export | Manual download | **Build** in 11c, tax reconciliation |
| OFX Direct Connect | Retail availability reported in secondary sources. Broker notices were unavailable. | Remote statement download | Unclear | Broker username and password | **Decline**, password-based access |
| Plaid Investments | 3,358 institutions flagged in the surveyed CSV. Fidelity access on Trial and thinkorswim parity under Schwab are UNVERIFIED. | Holdings, prices, basis, optional lots and option fields, and up to 24 months of activity. Per-broker depth is UNVERIFIED. | Billing docs say free on eligible Trial with 10 lifetime Items. Trial Investments entitlement is UNVERIFIED due to conflicting support excerpts. Paid Holdings and Transactions are separate monthly Item subscriptions with unpublished rates. | BYO Plaid keys. Plaid handles login or OAuth. Holdfast seals the access token. | **Build** on P6, probe entitlement |
| SnapTrade Personal | Broad retail broker list. Personal-key approvals by broker are UNVERIFIED. | Positions, balances, activity and orders. Broker-level lot coverage is UNVERIFIED. | Currently free, up to 20 connections. Future free pricing is UNVERIFIED. | BYO client ID and consumer key. SnapTrade can hold the password where OAuth is unavailable. Some broker connections can trade. | **Build** only if needed in 11b, after terms and consent checks. Distributed BYO use is UNVERIFIED. |
| SnapTrade Commercial | Commercial access subject to approval | Daily read-only or real-time with trading. Tax lots are an add-on. | Launch starts at $100/month plus per-user fees. | Commercial key. Broker OAuth or SnapTrade-held credentials. | **Defer**, Cloud pricing and terms |
| IBKR Flex Web Service | IBKR only | Batch trades, closed lots, wash-sale flags, corporate actions and cash. The 365-day query limit is UNVERIFIED. | Free with account | Report-only token | **Build** candidate 1 for 11b |
| IBKR Client Portal / Web API | IBKR only | Session and gateway requirements are UNVERIFIED. | Unclear | Live session and local gateway reported, UNVERIFIED | **Defer**, prefer Flex reports |
| tastytrade Open API | tastytrade only. A personal app covers the owner's own account. | Positions, balances and activity | Free with account | Personal OAuth with separate `read` and `trade` scopes | **Build** candidate 2 with `read` only |
| Public.com Individual API | Public only | Portfolio and history. The survey lists tax-lot selling endpoints. Readable lot details are unclear. | Free for personal non-commercial use | Secret exchanged for a short-lived token. Read-only scope is UNVERIFIED. | **Defer**, candidate 3 after scope verification |
| Schwab Trader API (Individual) | Schwab, including thinkorswim | Positions, balances, activity and orders. Lot and basis fields are UNVERIFIED. The one-year transaction window is from secondary docs. | Free with account | Trade-capable OAuth. Weekly login from secondary docs. Official terms and a read-only scope are UNVERIFIED. | **Defer**, credential scope and relink burden |
| E\*TRADE API | E\*TRADE only | Accounts, balances, portfolio and activity. History depth is UNVERIFIED. | Free with account | OAuth 1.0a. Trade-capable token, inactive after idle. Midnight expiry is UNVERIFIED. | **Defer**, credential scope and renewal burden |
| Alpaca | Alpaca only | Fills and dividend activity | Unclear | Read-only keys are UNVERIFIED. | **Defer**, credential scope |
| Tradier | Tradier only | Position basis and closed position gain/loss | Unclear | Token from the API page. Read-only enforcement needs verification. | **Defer**, outside first connector shortlist |
| Webull Individual OpenAPI | Webull only | Account positions | Unclear | Signed app key and secret. Read-only scope is UNVERIFIED. | **Defer**, credential scope |
| Coinbase / Kraken via CCXT | Crypto exchanges | Balances, fills and ledger | Free | Read-only exchange keys. Coinbase permissions are checkable. Kraken permission names are UNVERIFIED. | **Defer** here to 10a |
| Robinhood official Crypto API | Crypto only. No official individual equities API found in the survey. Beta scope is UNVERIFIED. | Crypto account reads | Unclear | API key with selectable permissions | **Defer** here to crypto scope |
| Robinhood private API | Robinhood | Private interface | Unclear | Brokerage password and MFA. Contractual restrictions are UNVERIFIED. | **Decline**, private API and password |
| Fidelity / Vanguard direct | No individual developer API found, an UNVERIFIED negative | Unknown | Unknown | Unknown | **Defer**, use files or supported feeds |
| Yodlee, MX, Finicity, Akoya | Broad financial networks. MX and Finicity individual access is UNVERIFIED. | Varies | Quoted or unpublished. Yodlee starter terms are UNVERIFIED. | Vendor onboarding and agreements | **Defer**, individual access unclear or unavailable |
| Vezgo | Crypto exchanges, chains and wallets | Crypto data | Quote-only Business or Enterprise | Business onboarding | **Defer**, crypto scope already has CCXT |

| Pricing route | Coverage | Data depth | Cost | Credentials or terms | Status and reason |
|---|---|---|---|---|---|
| Broker or positions file | Securities held in connected or imported accounts | As-of price and value when supplied | Included in feed or file | Existing connector or manual file | **Build**, current value and forward history |
| Twelve Data | Market data | Basic internal non-display use. Global closing prices listed from Grow. | Limited free Basic, paid Grow | BYO vendor key, display rights matter | **Defer**, history only if needed |
| Alpha Vantage | Market data | Historical pricing | Limited free tier reported by secondary source | Vendor key. Terms are UNVERIFIED. | **Defer**, terms and backfill need |
| Finnhub | Market data | Historical pricing | Free limits reported, UNVERIFIED | Vendor key. Redistribution terms are UNVERIFIED. | **Defer**, terms and backfill need |
| Tiingo | Market data | UNVERIFIED | UNVERIFIED | Terms and access are UNVERIFIED. | **Defer**, pricing and terms unavailable |
| Massive (formerly Polygon.io) | Market data | Free end-of-day history is UNVERIFIED. | Free tier is UNVERIFIED. Paid individual plans are listed. | Vendor key. Paid individual plans are for individual use. | **Defer**, terms and backfill need |
| IEX Cloud | Former market-data service | Retired, per secondary reporting | — | — | **Decline**, retired |
| Stooq | Market data | Historical prices reported | Limits unclear | CAPTCHA-issued key reported. Official API and terms are UNVERIFIED. | **Defer**, unclear access and terms |
| Yahoo Finance unofficial | Market data | Undocumented feed | No key reported | Unofficial endpoint. Terms are UNVERIFIED. | **Decline**, scraping |
| FRED | Rates, CPI and Treasury yields | Macro series | Key required. Survey found no numeric limit in the terms. | BYO key, notice and series-specific rights | **Defer**, outside holdings valuation |

## 4. Design decisions

**D1 — Shared lot engine.** Put one asset-parameterized engine in `backend/app/domain/lots.py` with storage and feed adapters outside it. Brokerage scopes are accounts. K2 adds wallet and exchange scopes over the same engine. Match incrementally and preserve basis on matched in-kind transfers. An unmatched transfer awaits basis evidence. Keep broker-reported lot IDs, coverage, basis and adjustments verbatim, with source and date. Derive missing facts from supported evidence and label them as computed. Use the account or security's documented basis method. Support FIFO as the fallback for computed lots with complete inputs. Record specific IDs and broker-supplied average basis when given. Flag missing history, unknown basis, ambiguous matching and unsupported elections for review. Each reinvested purchase opens a new lot.

**D2 — Numeric types.** Use Python `Decimal` from parse through calculation. Use `NUMERIC(14,2)` for money, `NUMERIC(28,10)` for quantity and `NUMERIC(18,8)` for unit prices. Use `MoneyStr`, `QuantityStr` and `PriceStr` for JSON and generated TypeScript. Keep decimal arithmetic in the browser. Preserve high precision through valuation and round only at a money boundary. Cash out is negative and cash in is positive. Normalize Plaid signs once. Record the currency and keep value totals by currency. [Model types](../../backend/app/models/_common.py), [API types](../../backend/app/schemas/common.py).

**D3 — Schema and identity.** Reuse `security`, `holding` and `investment_txn`. Add source-qualified security IDs, optional FIGI/ISIN, currency and option metadata. Keep CUSIP optional and alias changes explicit. Add transaction source, currency, settlement date and an account/date index. Keep the six transaction types and map the surveyed Plaid subtype vocabulary while retaining raw labels. Record wrappers using the existing account subtype and tax treatment where they fit. Store household-scoped lots, disposals, corporate actions and priced observations. Handle supported corporate actions from explicit terms or a manual entry. Missing ratios or allocations go to review. Keep shorts signed, margin debits in cash and cash equivalents in cash allocation. Preserve raw fills and use incremental ingest.

**D4 — Read-only connector.** `BrokerageConnector` works with Phase 2's `BankConnector` lifecycle and exposes account, balance, position, activity and report reads plus link, relink and health. Allowlist exact vendor hosts, HTTP methods and data or auth paths. Reject unknown routes before sending. No generic outbound method may bypass the allowlist. An architecture test must fail if any brokerage adapter declares or calls an order or transfer endpoint, including read endpoints in those families. Ledger records of past trades and transfers come through activity or reports. Require provider-enforced read-only credentials for the direct connector. For any eligible trade-capable SnapTrade connection, require Bulwark approval of the boundary and disclose the credential's capability before linking.

**D5 — Connector secrets.** Use `SecretToken` and the existing secret resolver and rotation design. Seal stored grants, tokens and keys with the context-bound connector envelope required by ADR 0015. Keep secrets out of logs, response payloads, audit data, URLs shown to users and fixtures. Holdfast never receives a brokerage password. For SnapTrade, disclose when the vendor holds the password and get the owner's decision before enabling it. Each new external service needs an accepted ADR before use. [Secret handling ADR](../adr/0015-secret-sources-and-rotation.md).

**D6 — Feed-first prices.** Use the as-of position price and broker value, with the feed's close as a labeled price fallback. Save the date, currency and `price_source` for each observation. The shared `PriceProvider` contract starts with the broker feed and files. Build forward history and show stale or missing prices with their dates. An unpriced position stays unpriced. Current value needs no new external service. A separate service for backfill, gaps or benchmarks needs a provider ADR and verified display terms. K2 reuses price history storage and the provider boundary. Crypto sources can supply their own prices.

**D7 — Plaid Item budget and history.** Apply the Phase 2 production guard to all brokerage links. Each separate institution login consumes one Item per Plaid client. Two people with separate logins use two Items. Accounts on an existing Item can share it when Plaid permits. Removed Items still count, typed confirmation starts at eight, and creation of an eleventh Item is blocked. Relink through update mode. A second Schwab Item for the same user and app invalidates the first. Retain the PLAN §3.2 reserve of at least two slots. If the budget is full, use files. Keep `PLAID_ENV=sandbox` by default. Probe Investments entitlement through ADR 0023 without creating an Item. Keep tracking available if the product is unavailable. Poll for late-arriving history and replay safely after a short first response. Provider depth remains UNVERIFIED until checked on a permitted real link. [PLAN §3.2](../../PLAN.md#32-the-10-item-budget-decide-before-linking), [Plaid setup ADR](../adr/0023-plaid-guided-setup.md).

**D8 — Household and person visibility.** Give every new table a `household_id` and forced RLS. Use account visibility for lots, transactions, source IDs, private prices, review records and other detail. `private` stays within the owner scope. Shared details are readable by the household. The other person sees only allowed summaries on a `summary_only` account. Enforce the same policy on reads, aggregates and writes as `holdfast_app`. Use Alembic as `holdfast_owner` with one migration token. The existing shared `security` reference can hold public reference fields only. Any future advisor access uses whitelisted read-only views with pre-provider redaction. [Visibility ADR](../adr/0008-rls-and-per-person-visibility.md).

**D9 — File reconciliation and replay.** Use Phase 3 staging and review with P2 ingest. For each account, date and currency, compare positions × unit price, with explicit multipliers and cash or margin adjustments as applicable, against the stated account value. Compare opening quantity plus activity against closing positions, and opening cash plus signed cash activity against stated closing cash. Apply the existing statement money tolerance and the declared source precision for quantities. Every file must reconcile its applicable checks against stated evidence, either on its own or in a matched statement set. Missing inputs and failed checks produce `needs_review` with a reason. Review rows stay out of verified totals. Realized G/L and tax files also reconcile reported closing-lot totals against matched disposal records. Use file hashes, Plaid transaction IDs and OFX FITIDs, and a stable fingerprint with a same-day ordinal for CSVs without IDs. On a proven file/feed overlap, retain one effective row with feed precedence and preserve the file and broker-reported basis evidence. Ambiguous overlap goes to review.

**D10 — Tax boundary.** Tracking can store source-reported basis and disposal evidence. Keep computed realized gains, wash-sale estimates and 1099-B reconciliation in `backend/app/tax/`, with UI, API and any tax tools behind `FEATURE_TAX`. Validate year-specific rules from tax config. Use broker-reported values as the reconciliation authority and show differences separately. Distinguish missing and noncovered basis from zero. Label household wash-sale output as an estimate. Check linked household accounts, including spouse and retirement accounts where the verified config calls for it. Identify gaps in account coverage, wrappers or security identity. Follow Phase 7 and 10b's shared export conventions.

## 5. Story slices

One PR per slice. Sizes follow the [roadmap](../planning/ROADMAP.md#units-agent-time-not-human-days). Keel assigns the migration token. A named review gate is separate from the author.

| ID | Scope | Owner | Reviewers | Size | Migration token | Depends on | Acceptance evidence |
|---|---|---|---|---|---|---|---|
| **V1** | Schema, security identity, source and wrapper fields, lots, disposals, corporate actions and price history | Lattice | Ledger, Bulwark | M | Yes | Phase 3 PASS, F4, schema ADR | One Alembic head upgrades and downgrades with legacy fixtures. Forced RLS tests cover every new table. |
| **V2** | Shared lot engine and feed-first price contract. Basis provenance and in-kind transfer matching. | Ledger | Lattice, Ledger money gate, Cipher | M | No | V1, schema and pricing ADRs | Brokerage and wallet fixture adapters pass the same fractional buy, partial disposal, reinvestment and basis-preserving transfer tests. Missing inputs stay unresolved. |
| **V3** | Brokerage positions and activity CSV on Phase 3 imports | Tide | Ledger, Bulwark | M | No | V2, I1, I2, P2, brokerage strategy ADR | Matching positions and cash reconcile. An altered balance or missing data reaches `needs_review`. Same-day fills survive re-upload exactly once each. |
| **V4** | Investment OFX/QFX and overlap handling | Tide | Ledger, Bulwark | M | No | V3, I4 | Replay of a FITID and an overlapping feed fixture creates one effective transaction. An ambiguous match reaches `needs_review`. |
| **V5** | Realized G/L CSV evidence, including broker adjustments | Tide | Ledger, Tally, Bulwark | M | No | V3 | A synthetic export preserves broker basis, adjusted values and missing basis. An unmatched disposal flags review. Tax calculations stay gated. |
| **V6** | Manual, audited corporate-action corrections with explicit terms | Ledger | Ledger money gate, Bulwark | M | No | V2 | A split preserves aggregate basis. Incomplete terms reach review. An unauthorized correction fails. |
| **V7** | Extend P6 Plaid sync to brokerage lots, prices, and delayed history | Tide | Ledger, Bulwark | M | No | V2, V4, P1–P7, Plaid amendment | Synthetic Plaid pages cover empty lots, late history, one sign conversion and idempotent replay. An unavailable product leaves file tracking usable. |
| **V8** | Read API and Investments v1. Holdings by account and wrapper, allocation, unrealized gain from broker basis, virtualized activity, contributions and source freshness. | Prism | Prism brand gate, Ledger, Bulwark, Tally, Sounding | M | No | V5, V6, V7, F3, copy ADR | A 375px demo covers fresh, stale, unpriced and restricted accounts. Contributions show totals and use validated tax config for any enabled limit comparison. With `FEATURE_TAX` off, tracking still works. |
| **V9** | 11a acceptance and demo | Sounding | Keel | S | No | V1–V8 | All §7 checks pass on synthetic fixtures and Sandbox. Real-broker depth remains UNVERIFIED until checked. |
| **VB1** | One eligible direct read-only connector and shared connection storage | Tide | Bulwark, Ledger, Prism | M | Yes | V9, direct API and brokerage strategy ADRs, eligible broker | The chosen token can only read. Fixture replay is idempotent. Order and transfer paths fail before dispatch. Secret canary stays out of output. |
| **VB2** | Optional self-host SnapTrade Personal BYO connector | Tide | Bulwark, Ledger, Prism | M | If VB1 is skipped | VB1 or explicit VB1 skip, SnapTrade ADR, verified terms and owner decision | Fixtures cover disclosure before linking, unsupported access, replay, no order or transfer calls, and no secret leakage. |
| **VT1** | Realized gains and lot disposal reporting, with wrapper treatment | Tally | Ledger, Tally tax gate, Prism, Bulwark | M | No | V9, Phase 7 tax foundation, 10b export contract, copy ADR | Synthetic sales reconcile to lot evidence. Wrapper and unknown-basis cases are visible. Tax routes and UI are disabled with the flag off. |
| **VT2** | Household wash-sale estimate from verified year config | Tally | Ledger, Tally tax gate, Prism, Bulwark | M | No | VT1 | A synthetic cross-account chain follows verified config and shows unresolved coverage and estimate labels without leaking private detail. |
| **VT3** | 1099-B reconciliation through Phase 7's document path and shared export | Tally | Ledger, Tally tax gate, Prism, Bulwark | M | No | VT1, VT2, Phase 7 document verification | Synthetic covered and noncovered rows reconcile or show source-linked mismatches. Re-import adds zero duplicates and preserves the broker's original values. |

VB1 selects the first eligible broker in this order: IBKR Flex, tastytrade with `read`, Public after read-only access is verified. Skip VB1 if no candidate qualifies. VB2 is optional when Plaid slots or data depth are insufficient. If VB1 is skipped, VB2 supplies the connector storage migration. Split any slice that grows beyond M before scheduling it. If validated profiles or tax config are unavailable at a slice's gate, record that gap in its evidence.

## 6. ADRs needed

1. Brokerage data strategy and `BrokerageConnector` contract, Phase 2 reuse, ordering of sources and read-only tests.
2. Investment schema and shared lot engine, supported basis evidence, security identity and crypto K2 reuse.
3. SnapTrade Personal, dated distributed BYO terms, password custody and self-host boundary.
4. Direct broker APIs, with annexes for verified scopes and ADR 0015 token handling for each candidate.
5. Feed-first pricing and shared `PriceProvider` contract. Each adopted vendor needs dated terms, display rights and Cloud licensing. Include the FRED notice if adopted.
6. Investment copy and advice boundary, estimate labels and advisor guardrails.
7. PLAN §3.2 and ADR 0023 amendment for brokerage slots, update-mode relink and asynchronous Investments history.
8. Separate Schwab Trader API ADR if ever reconsidered, with an explicit decision on its trade-capable credentials.

## 7. Acceptance criteria for 11a

1. **Accept:** A single Alembic head upgrades from the current schema and downgrades in CI. Existing holdings and activity survive the upgrade. Runtime-role tests prove cross-household isolation, `private` account isolation and balance-only access for another person on a `summary_only` account. Each new table is covered.
2. **Accept:** The same lot-engine fixture suite passes for brokerage and crypto adapters. Fractional lots round-trip at the declared precision. A matched transfer carries basis with zero disposals. An incomplete history produces unknown basis. A split with stated terms preserves total basis.
3. **Accept:** The CSV and investment OFX/QFX demo reconciles position value, position movements and cash. Each intentionally mismatched or incomplete file is `needs_review` with a reason. Exact replay adds zero duplicates, and a proven feed overlap has exactly one effective row. The realized G/L import preserves the source's basis and adjustments.
4. **Accept:** A synthetic Plaid Investments run stores holding prices, cash effects and supplied lots. An empty lot array leaves basis evidence as supplied. Initial and delayed pages converge on the same final state when replayed. Simulated `INVALID_PRODUCT` leaves file import available. Sandbox and synthetic results are recorded separately from UNVERIFIED provider coverage.
5. **Accept:** Link tests confirm that the production guard counts removed Items, requires typed confirmation starting at eight, blocks an eleventh Item and relinks without spending another slot. Endpoint and transport tests reject any order or transfer route. A synthetic credential canary is absent from logs, audits and API responses.
6. **Accept:** At 375px and desktop, `/investments` shows holdings grouped by account and wrapper, allocation, unrealized gain where broker basis is available, activity, contribution totals, source, freshness and the footer line. A stale or missing price is labeled. Two currencies remain separate, margin cash reduces value and cash equivalents appear in cash allocation. The fixture run uses zero external price requests.
7. **Accept:** Tracking works with `FEATURE_TAX` off, and tax routes, tools and views stay unavailable. Any enabled contribution limit comparison uses the validated year config. A missing rule shows an unavailable comparison.

## 8. Placement

Phase 5 PASS on Sandbox and synthetic data stays the Q1 finish line. 11a is a preemptible lane after Phase 3 and before crypto 10a. Start with V1 and V2 so K2 can use the same lot engine, transfer rules and price history. Keep 10a's existing placement after Phase 5. 11b follows 11a and the connector ADRs. Schedule 11c with Phase 7 and 10b. [Roadmap](../planning/ROADMAP.md), [PLAN §18](../../PLAN.md#18-crypto--self-custody-module).

From Phase 2, 11a uses P1's connector protocol and P2's `IngestService`. The Plaid slice also needs the client, slot guard, baseline transaction sync, P6 investment and balance reads, and polling and relink. It does not require SimpleFIN, paid or live Plaid, Plaid webhooks, or a new vendor. Phase 3 provides staging, parser wiring, reconciliation, review and dedupe. The schema and engine can start when Phase 3 passes, independently of the Plaid extension.

The survey §3.5 assigns step 5 to both 11a and 11b. This plan puts all direct connectors in 11b. It also describes P6 as both part of 11a and a dependency. Keep the existing Phase 2 P6 baseline in place and use V7 for the extension. PLAN §18.5 sketches crypto-only storage and the roadmap names K2 as per-wallet. This plan uses shared logic and price history with account and wallet scopes. The schema ADR records how both proposals fit. Section 20.3 still governs connector and visibility requirements.

The survey's build order calls Public read-only by construction, while the source details mark its scope UNVERIFIED. Public requires verification before selection. The survey and ADR 0023 cite Plaid billing docs that include Investments in Trial. The survey also found conflicting support excerpts, so the setup probe decides availability for each Plaid client.

## 9. Open questions for the owner

These answers change profile selection, live validation or later scope. Synthetic schema and engine work can proceed independently.

1. Which brokers and how many separate logins per person? This sets the import profiles, Item count and eligible direct connector.
2. Which account wrappers are present? This affects classification, contribution display and tax treatment.
3. Are there options, multi-leg trades, index options, futures, event contracts or crypto activity? This changes required import fields, report coverage and deferred derivative work.
4. Are there margin or short positions? This sets reconciliation cases and UI priorities for signed quantities and cash.
5. Roughly how many fills are there per day and year? This determines whether v1 needs rollups or partitioning.
6. How many Plaid Items are spent or reserved, when and where was the Plaid team created, and is paid Plaid an option? This sets Trial eligibility checks, the budget and file fallback.
7. Is SnapTrade acceptable, including vendor custody of a password where OAuth is unavailable? This decides whether VB2 is viable.
8. How far back should trade and price history go? This determines the import backfill and whether to consider a historical price provider.
9. Is tracking enough, or should 11c reconcile tax-grade outputs? This sets the priority of the tax lane.
10. Are there non-USD or foreign-listed holdings? This changes identity and currency fixtures and the priority of FX support.
11. Can the owner supply representative exports for private validation on their own machine? This establishes which profiles can be verified. Repository tests use invented data. [Real-data ADR](../adr/0019-owner-real-data-stays-local.md).
12. Should Holdfast Cloud ever offer brokerage sync? This changes aggregator plan and price-display licensing decisions.

## 10. Copy rule

The product never calls itself an adviser. It makes no buy or sell recommendations or performance promises. Use the ownership voice from the [brand guide](../brand/BRAND.md#voice). Label derived tax results as estimates and point to the source records on the tax surface.

> Your holdings. Your records. Not investment advice.
