# Holdfast — Personal Finance Platform + CPA Agent

**Build plan for Claude Code**
Version 1.1 · Drafted 2026-11-18 · Revised 2026-11-18

> **Revision 1.1 (2026-11-18):**
> - The product is named **Holdfast** (working name was "Ledgerline"). See `docs/adr/0002-product-name-holdfast.md`.
> - **Section 20** adds the changes from the market research (`docs/research/`). Where Section 20 conflicts with an earlier section, Section 20 wins.
> - `CLAUDE.md` in the repo root is the live version of Section 14. Edit that file, not Section 14.
> - Brand and visual system: `docs/brand/BRAND.md` (Direction 01, chosen in ADR 0003).
>
> Each phase has acceptance criteria. Don't start a phase until the one before it passes. Progress is tracked in `docs/STATUS.md`.

---

## 0. Reality check (read first, it shapes the design)

These constraints are real and the architecture is built around them:

| Area | Constraint | Design response |
|---|---|---|
| **Plaid Trial plan** | Free, real production data, **capped at 10 Items** (an Item = one institution login). **Deleting an Item does NOT free a slot.** | Plan the 10 slots before linking anything (Section 3.2). Do all development in **Sandbox** (unlimited, fake data). Only link real accounts once Phase 2 is stable. |
| **Plaid Trial products** | Plaid support lists an 8-product Trial bundle: Auth, Transactions, Balance, Identity, Assets, Liabilities, Investments, Statements. (Another Plaid page lists fewer, so confirm in Dashboard → Products before relying on Investments, Liabilities or Statements.) | Build each product integration behind a feature flag so a missing product degrades gracefully. |
| **Transaction history depth** | Plaid Transactions returns about **24 months** at most, and some institutions return less. | 2020 to ~2024 comes from **statement PDF/CSV import** (Phase 3). Plaid covers recent history and ongoing sync. |
| **Credit bureaus** | There's **no consumer API** for your own Equifax/Experian/TransUnion report. Plaid's Consumer Report (Plaid Check) isn't on the Trial plan and is built for lenders with a permissible purpose. | **Manual pull + import:** download reports from AnnualCreditReport.com (free weekly from all 3 bureaus), save as PDF, and have the app parse them (Phase 6). No scraping of bureau sites. |
| **Filing** | You self-file. The app itself doesn't transmit to the IRS, because that requires being an authorized e-file provider. | The agent does the full CPA-style review and produces a filing-ready package. **You file it yourself** (Section 9.7). |
| **CPA agent mandate** | Tax law changes yearly (for example, OBBBA added several new deductions starting with tax year 2025). Stale numbers cost money. | The agent's job is to **exhaustively find every legal deduction, credit, exclusion, deferral and planning strategy** that applies, then rank them by dollars saved. It uses legal "loopholes" (provisions Congress wrote in on purpose) fully. The one hard line is **evasion**: hiding income, fabricating expenses or misclassifying personal spending. Every number comes from versioned yearly config with IRS source URLs. Each strategy is labeled with an audit-risk rating so you decide how aggressive to be. |
| **Hosting** | This is the most sensitive data you own. | Run it on a **personal** machine or personal VPS, **not employer-owned hardware and not over an employer VPN**. Access it remotely through Tailscale, never through an open port. |

---

## 1. Goals

1. **Aggregate** every account: checking/savings (Current, Navy Federal, Wells Fargo), credit cards, loans, 401(k)s, HSA, IRAs/brokerage (Empower, etc.).
2. **Backfill** history to 2020-01-01 by importing statements.
3. **Normalize and categorize** all transactions, with rules, ML assist and manual overrides.
4. **Real-time budgets**: envelope or category budgets that update on every sync, with alerts.
5. **Metrics and forecasts**: cash flow, savings rate, net worth, burn rate, recurring bills, 30/90/365-day cash projections, retirement trajectory.
6. **Credit monitoring**: parse the 3-bureau reports, track accounts, utilization, inquiries and changes, and flag discrepancies.
7. **CPA agent**: a year-round tax advisor that tracks deductible items, estimates liability, models strategies (401k/HSA/IRA contributions, timing, harvesting), assembles the year-end tax package and walks you through filing.
8. **Household support**: multiple people (for example, you plus a spouse) with per-person and joint views.

**Non-goals:** moving money, e-filing directly, scraping websites that forbid it, multi-tenant SaaS.

---

## 2. Architecture

```
        ┌──── Tailscale (private) ── https://money.yourdomain.com ── Caddy ────┐
                │                                                                      │
  Browser ──▶  web (React+TS+Tailwind+shadcn, PWA)  ──▶  api (FastAPI)  ──▶  Postgres 16 (encrypted volume)
  / phone          Plaid Link SDK              │   ▲                ▲
                                               │   │                │
                                               ▼   │                │
                                        worker (APScheduler/arq) ───┘
                                          • Plaid sync jobs
                                          • import parsers (PDF/CSV/OFX)
                                          • metrics + forecasts
                                          • alerting
                                               │
                                               ▼
                                        agent service (Anthropic SDK, tool use)
                                          • read-only SQL views
                                          • tax engine
                                          • document store
                                               │
                                        Redis (job queue + cache)
                                        MinIO or local encrypted dir (documents)
```

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2** | The finance/tax/ML ecosystem is in Python, and there's an official `plaid-python` SDK |
| DB | **PostgreSQL 16** | Numeric precision, window functions, materialized views |
| Queue | **Redis + arq** (or APScheduler to start) | Simple async jobs and scheduled syncs |
| Frontend | **React + Vite + TypeScript + Tailwind CSS + shadcn/ui** (core components) + **Magic UI / Aceternity UI** (accents only) + TanStack Query + Recharts (via shadcn charts). Installable **PWA** for phone | See Section 11.1 |
| Plaid client | `react-plaid-link` + `plaid-python` | Official SDKs |
| PDF parsing | `pdfplumber`, `camelot` (tables), `pytesseract` fallback for scanned docs | Statement backfill |
| Forecasting | `pandas`, `statsmodels` (ETS/SARIMA), optionally `prophet` | Cash-flow projections |
| Categorization | Rules engine + `scikit-learn` (TF-IDF + logistic regression) trained on your own labels | Learns your patterns |
| Tax estimate | Own engine driven by yearly config; optionally cross-check against **PSL Tax-Calculator** (`taxcalc`) | Transparent, auditable |
| Agent | **Anthropic Python SDK**, Claude with tool use (`claude-opus-5-5` for tax review, `claude-sonnet-5-5` for chat and categorization help) | Strong reasoning and tool use |
| Auth | Local accounts + **WebAuthn passkeys** (or TOTP) via `py_webauthn` | Single household, strong auth |
| Secrets | `.env` (never committed) + **libsodium/`cryptography` Fernet** for Plaid access tokens at rest | Tokens are bearer credentials |
| Deploy | **Docker Compose** on a personal Ubuntu box or VPS; **Tailscale** for remote access; Caddy for local HTTPS | Private by default |
| Dev OS | Windows (pwsh 7) and Ubuntu | Scripts provided for both |

---

## 3. Data sources

### 3.1 Integration matrix

| Source | Method | Products | Notes |
|---|---|---|---|
| Current | Plaid | Transactions, Balance, Auth | Older history from in-app monthly statement PDFs |
| Navy Federal CU | Plaid | Transactions, Balance, Auth, Investments, Liabilities | Plaid lists NFCU support for Assets, Auth, Balance, Transactions, Investments. If Link asks for a "Personal Access Code", enter your online banking password |
| Wells Fargo | Plaid (OAuth) | Transactions, Balance, Liabilities | OAuth institution, so it needs a registered redirect URI (Section 7.3). Wells Fargo is included on the Trial plan |
| Empower (401k) | Plaid | Investments | Holdings, investment transactions, contributions |
| HSA | Plaid | Investments / Transactions | If the HSA is administered through Empower, it may need to be linked under **Optum Bank**. Confirm the HSA custodian |
| Other 401k/IRA | Plaid | Investments | One Item per institution login |
| Credit reports (EQ/EX/TU) | **Manual PDF import** | — | AnnualCreditReport.com, free weekly |
| Credit score | Manual entry or import | — | Scores aren't included on free reports. Card issuer or bureau apps can show one; enter it monthly |
| Tax documents (W-2, 1099-INT/DIV/B/R, 1098, 5498-SA, 1099-SA, 1095) | Upload PDF → parse | — | Phase 7 |
| Prior-year returns | Upload PDF → parse key lines | — | Carryforwards, baselines |
| Historical statements 2020+ | Upload PDF/CSV/OFX/QFX | — | Phase 3 |

### 3.2 The 10-Item budget (decide before linking)

Fill this in before Phase 2 goes live. Keep at least 2 slots spare for relinks or a new account.

| # | Institution | Accounts under this login | Owner |
|---|---|---|---|
| 1 | Current | Checking, savings pods | |
| 2 | Navy Federal | Checking, savings, cards, loans | |
| 3 | Wells Fargo | | |
| 4 | Empower | 401(k) | |
| 5 | HSA custodian | HSA | |
| 6 | (other 401k/IRA) | | |
| 7 | (spouse's bank, if any) | | |
| 8 | spare | | |
| 9 | spare | | |
| 10 | spare | | |

If you exceed 10, either upgrade to paid Pay-as-you-go (you can't return to Trial afterward) or move low-activity accounts to monthly statement import.

---

## 4. Repository layout

```
holdfast/
├── PLAN.md                         # this file
├── CLAUDE.md                       # rules for Claude Code (Section 14)
├── README.md
├── .env.example                    # every variable, no values
├── .gitignore                      # .env, data/, *.pdf, secrets/
├── docker-compose.yml              # postgres, redis, api, worker, web, caddy
├── docker-compose.dev.yml
├── Makefile                        # make up / test / migrate / seed (Linux)
├── scripts/
│   ├── dev.ps1                     # pwsh 7 equivalents of Makefile targets
│   ├── backup.ps1 / backup.sh      # encrypted pg_dump + documents → restic
│   ├── restore.sh
│   ├── gen-keys.py                 # generates FERNET_KEY, SESSION_SECRET
│   └── seed_sandbox.py             # creates Plaid sandbox Items with test data
│
├── backend/
│   ├── pyproject.toml              # uv or poetry; ruff, mypy, pytest config
│   ├── alembic.ini
│   ├── migrations/versions/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory, routers, middleware
│   │   ├── config.py               # pydantic-settings; loads .env
│   │   ├── db.py                   # engine, session, Base
│   │   ├── security/
│   │   │   ├── crypto.py           # encrypt/decrypt Plaid tokens (Fernet)
│   │   │   ├── auth.py             # sessions, WebAuthn/TOTP
│   │   │   └── audit.py            # audit log writer
│   │   ├── models/                 # SQLAlchemy ORM (Section 5)
│   │   │   ├── household.py        # Household, Person, User
│   │   │   ├── institution.py      # Institution, PlaidItem, Account
│   │   │   ├── transaction.py      # Transaction, Split, Transfer link
│   │   │   ├── category.py         # Category, Rule
│   │   │   ├── investment.py       # Security, Holding, InvestmentTxn
│   │   │   ├── liability.py        # Liability (loans, cards)
│   │   │   ├── budget.py           # Budget, BudgetLine, BudgetPeriod
│   │   │   ├── balance.py          # BalanceSnapshot (daily)
│   │   │   ├── credit.py           # CreditReport, Tradeline, Inquiry, ScoreEntry
│   │   │   ├── document.py         # Document, ParsedField
│   │   │   ├── tax.py              # TaxYear, TaxItem, TaxScenario, Estimate
│   │   │   ├── agent.py            # Conversation, Message, Finding
│   │   │   └── audit.py
│   │   ├── schemas/                # Pydantic request/response models
│   │   ├── api/
│   │   │   ├── routes_auth.py
│   │   │   ├── routes_plaid.py     # link token, exchange, webhooks
│   │   │   ├── routes_accounts.py
│   │   │   ├── routes_transactions.py
│   │   │   ├── routes_budgets.py
│   │   │   ├── routes_metrics.py
│   │   │   ├── routes_forecast.py
│   │   │   ├── routes_credit.py
│   │   │   ├── routes_documents.py
│   │   │   ├── routes_tax.py
│   │   │   └── routes_agent.py     # streaming chat (SSE)
│   │   ├── integrations/
│   │   │   └── plaid/
│   │   │       ├── client.py       # configured PlaidApi per env
│   │   │       ├── link.py         # create link token, exchange public token
│   │   │       ├── sync_transactions.py  # /transactions/sync cursor loop
│   │   │       ├── sync_balances.py
│   │   │       ├── sync_investments.py
│   │   │       ├── sync_liabilities.py
│   │   │       ├── statements.py   # Statements product (if enabled)
│   │   │       ├── webhooks.py     # verify JWT, dispatch jobs
│   │   │       └── errors.py       # ITEM_LOGIN_REQUIRED → relink flow
│   │   ├── importers/
│   │   │   ├── base.py             # Importer protocol → NormalizedTxn
│   │   │   ├── csv_generic.py      # column-mapping profiles
│   │   │   ├── ofx.py              # OFX/QFX via ofxparse
│   │   │   ├── pdf_statement.py    # pdfplumber + per-bank profiles
│   │   │   ├── profiles/
│   │   │   │   ├── current.yaml
│   │   │   │   ├── navy_federal.yaml
│   │   │   │   └── wells_fargo.yaml
│   │   │   └── dedupe.py           # merge imported vs Plaid txns
│   │   ├── domain/
│   │   │   ├── categorize/
│   │   │   │   ├── rules.py        # user rules (merchant regex, amount, account)
│   │   │   │   ├── ml.py           # train/predict on user labels
│   │   │   │   └── pipeline.py     # rules → plaid PFC → ML → "Uncategorized"
│   │   │   ├── transfers.py        # detect internal transfers & card payments
│   │   │   ├── recurring.py        # subscriptions/bills detection
│   │   │   ├── budgets.py          # real-time budget computation
│   │   │   ├── metrics.py          # cash flow, savings rate, net worth, etc.
│   │   │   ├── forecast.py         # cash-flow & balance projection
│   │   │   ├── retirement.py       # contribution tracking, projection
│   │   │   ├── credit.py           # report diffing, utilization
│   │   │   └── alerts.py           # rule-based notifications
│   │   ├── tax/
│   │   │   ├── config/             # one YAML per tax year (Section 9.2)
│   │   │   │   ├── 2025.yaml
│   │   │   │   └── 2026.yaml
│   │   │   ├── state/
│   │   │   │   └── sc.py           # state module (South Carolina, if applicable)
│   │   │   ├── engine.py           # federal liability estimate
│   │   │   ├── items.py            # deductible/credit item tagging
│   │   │   ├── doc_parsers/        # W-2, 1099-*, 1098, 5498-SA parsers
│   │   │   ├── relief_catalog.yaml # every deduction/credit/strategy per year (Section 9.5)
│   │   │   ├── relief_scanner.py   # evaluates the catalog against household data
│   │   │   ├── strategies.py       # scenario modeling
│   │   │   ├── amend.py            # prior-year look-back + 1040-X worksheets
│   │   │   ├── fill_forms.py       # fills IRS fillable PDFs for paper/Fillable Forms
│   │   │   ├── package.py          # year-end workpaper builder (PDF + XLSX)
│   │   │   └── crosscheck.py       # optional taxcalc comparison
│   │   ├── agent/
│   │   │   ├── client.py           # Anthropic SDK wrapper, model routing
│   │   │   ├── prompts/
│   │   │   │   ├── system_cpa.md
│   │   │   │   ├── system_assistant.md
│   │   │   │   └── review_checklist.md
│   │   │   ├── tools.py            # tool schemas + handlers (Section 10.3)
│   │   │   ├── guardrails.py       # redaction, read-only enforcement
│   │   │   └── runs.py             # scheduled reviews (monthly, quarterly, year-end)
│   │   ├── worker/
│   │   │   ├── main.py             # arq worker settings
│   │   │   ├── jobs.py             # sync_item, rebuild_metrics, run_forecast...
│   │   │   └── schedule.py         # cron definitions
│   │   └── notify/
│   │       ├── email.py            # SMTP
│   │       └── push.py             # ntfy.sh self-hosted or Pushover
│   └── tests/
│       ├── conftest.py             # test DB, factories
│       ├── fixtures/               # sanitized sample statements, Plaid JSON
│       ├── test_plaid_sync.py
│       ├── test_importers.py
│       ├── test_dedupe.py
│       ├── test_categorize.py
│       ├── test_transfers.py
│       ├── test_recurring.py
│       ├── test_budgets.py
│       ├── test_forecast.py
│       ├── test_tax_engine.py      # golden tests vs IRS worked examples
│       └── test_agent_tools.py
│
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── src/
│       ├── main.tsx
│       ├── api/                    # typed client (generated from OpenAPI)
│       ├── components/
│       │   ├── PlaidLinkButton.tsx
│       │   ├── TransactionTable.tsx
│       │   ├── CategoryPicker.tsx
│       │   ├── BudgetBar.tsx
│       │   ├── charts/             # CashFlow, NetWorth, Forecast, Spending
│       │   └── AgentChat.tsx
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── Accounts.tsx
│       │   ├── Transactions.tsx
│       │   ├── Budgets.tsx
│       │   ├── Forecast.tsx
│       │   ├── Investments.tsx
│       │   ├── Credit.tsx
│       │   ├── Documents.tsx
│       │   ├── Taxes.tsx
│       │   ├── Advisor.tsx         # agent chat
│       │   └── Settings.tsx        # items, rules, household, security
│       └── lib/
│
├── data/                           # gitignored; mounted volume
│   ├── documents/                  # encrypted uploads
│   └── exports/
└── docs/
    ├── adr/                        # architecture decision records
    ├── runbook.md                  # relink, backup/restore, key rotation
    └── tax-sources.md              # IRS pubs referenced per year
```

---

## 5. Data model (Postgres)

All money is stored as `NUMERIC(14,2)`, never floats. Signs follow one convention everywhere: **outflow negative, inflow positive** (Plaid uses the opposite, so convert on ingest).

```sql
-- Household & identity
household(id, name, created_at)
person(id, household_id, display_name, filing_role)          -- 'primary','spouse','dependent'
app_user(id, person_id, email, password_hash, webauthn_creds jsonb, totp_secret_enc, role)

-- Institutions & accounts
institution(id, plaid_institution_id, name, logo_url)
plaid_item(id, household_id, institution_id, plaid_item_id, access_token_enc bytea,
           env, products text[], status, error_code, consent_expires_at,
           tx_cursor text, last_synced_at, created_at)
account(id, plaid_item_id NULL, household_id, owner_person_id NULL,  -- NULL owner = joint
        plaid_account_id NULL, source,                               -- 'plaid','import','manual'
        name, official_name, mask, type, subtype,                    -- depository/credit/loan/investment
        tax_treatment,                                               -- 'taxable','traditional','roth','hsa'
        currency, is_hidden, opened_at, closed_at)

-- Transactions
transaction(id, account_id, plaid_transaction_id UNIQUE NULL, import_fingerprint UNIQUE NULL,
            posted_date, authorized_date, amount, description_raw, merchant_name,
            plaid_pfc_primary, plaid_pfc_detailed,
            category_id, category_source,       -- 'rule','plaid','ml','user'
            is_pending, is_transfer, transfer_group_id NULL,
            is_tax_relevant bool, tax_tag NULL,  -- 'charity','medical','business','hsa_qualified'...
            notes, receipt_document_id NULL,
            source, created_at, updated_at)
transaction_split(id, transaction_id, category_id, amount, tax_tag, note)

category(id, household_id, parent_id NULL, name, kind,  -- 'income','expense','transfer','savings'
         is_essential bool, default_tax_tag NULL)
rule(id, household_id, priority, match jsonb,            -- {merchant_regex, desc_regex, account_id, amount_min/max}
     set jsonb,                                          -- {category_id, tax_tag, is_transfer, rename}
     created_by, hit_count)

-- Balances & investments
balance_snapshot(account_id, as_of_date, current, available, limit_amt, PRIMARY KEY(account_id, as_of_date))
security(id, plaid_security_id, ticker, name, type, cusip)
holding(account_id, security_id, as_of_date, quantity, cost_basis, value)
investment_txn(id, account_id, plaid_inv_txn_id, date, type, subtype, security_id,
               quantity, price, amount, fees)             -- contributions, dividends, buys/sells
liability(account_id, kind, apr, min_payment, next_due_date, statement_balance, original_principal, payoff_date)

-- Budgets
budget(id, household_id, name, period,                   -- 'monthly','biweekly','annual'
       method,                                           -- 'category','envelope','zero_based'
       rollover bool, start_date, active)
budget_line(id, budget_id, category_id, amount, alert_pct default 0.8)
budget_period_actual  -- MATERIALIZED VIEW: budget_line × period → spent, remaining, pace

-- Credit
credit_report(id, person_id, bureau, pulled_on, document_id, parsed_ok)
tradeline(id, credit_report_id, creditor, account_type, opened, status, credit_limit,
          balance, high_balance, payment_history jsonb, account_number_masked)
credit_inquiry(id, credit_report_id, creditor, date, kind)  -- hard/soft
credit_score_entry(id, person_id, model, bureau, score, as_of, source)

-- Documents
document(id, household_id, person_id NULL, kind,         -- 'statement','w2','1099-int','credit_report','receipt',...
         tax_year NULL, filename, sha256, storage_path, mime, uploaded_at, parse_status)
parsed_field(id, document_id, key, value, confidence, page, bbox jsonb, verified_by_user bool)

-- Tax
tax_year(id, household_id, year, filing_status, state, dependents jsonb, status)  -- 'open','prepared','filed'
tax_item(id, tax_year_id, person_id, form, line_key, amount, source_type,           -- 'document','transaction','manual','agent'
         source_id, confidence, needs_review bool, note)
tax_scenario(id, tax_year_id, name, assumptions jsonb, result jsonb, created_by)
estimate_snapshot(id, tax_year_id, as_of, federal_liability, state_liability, withheld, projected_due)

-- Agent
agent_conversation(id, household_id, kind, title, created_at)   -- 'chat','monthly_review','year_end'
agent_message(id, conversation_id, role, content jsonb, tool_calls jsonb, tokens_in, tokens_out, created_at)
agent_finding(id, household_id, tax_year NULL, severity, category, title, body_md,
              est_savings NULL, status, source_refs jsonb)       -- 'open','accepted','dismissed','done'

audit_log(id, user_id, action, entity, entity_id, detail jsonb, ip, at)
```

**Key indexes:** `transaction(account_id, posted_date)`, `transaction(category_id, posted_date)`, `transaction(merchant_name)`, trigram index on `description_raw`, `balance_snapshot(as_of_date)`.

**Read-only reporting views** (the agent uses only these): `v_transactions`, `v_monthly_cashflow`, `v_category_spend`, `v_net_worth_daily`, `v_recurring`, `v_budget_status`, `v_holdings_latest`, `v_contributions_ytd`, `v_tax_items`.

---

## 6. Environment variables (`.env.example`)

```
# Core
APP_ENV=dev                         # dev | prod
APP_BASE_URL=https://money.yourdomain.com
TAILSCALE_BIND_IP=100.x.y.z
CF_API_TOKEN=                       # DNS-01 cert only (Zone:DNS:Edit scope)
SESSION_SECRET=
FERNET_KEY=                         # scripts/gen-keys.py
DATABASE_URL=postgresql+psycopg://holdfast:***@db:5432/holdfast
REDIS_URL=redis://redis:6379/0
DOCUMENT_STORE=/data/documents

# Plaid
PLAID_ENV=sandbox                   # sandbox | production
PLAID_CLIENT_ID=
PLAID_SECRET_SANDBOX=
PLAID_SECRET_PRODUCTION=
PLAID_PRODUCTS=transactions,auth,balance,investments,liabilities,statements
PLAID_COUNTRY_CODES=US
PLAID_REDIRECT_URI=https://money.yourdomain.com/plaid/oauth-return
PLAID_WEBHOOK_URL=                  # optional; see 7.4
PLAID_TX_DAYS_REQUESTED=730

# Anthropic
ANTHROPIC_API_KEY=
AGENT_MODEL_REVIEW=claude-opus-5-5
AGENT_MODEL_CHAT=claude-sonnet-5-5
AGENT_MONTHLY_TOKEN_BUDGET=2000000

# Notifications
SMTP_HOST= SMTP_USER= SMTP_PASS= ALERT_EMAIL=
NTFY_URL=  NTFY_TOPIC=

# Household
HOUSEHOLD_STATE=SC
HISTORY_START=2020-01-01
```

---

## 7. Plaid integration details

### 7.1 Link flow
1. `POST /api/plaid/link-token` → server calls `/link/token/create` with `user.client_user_id = person.id`, `products`, `transactions.days_requested = 730`, `redirect_uri`, and optional `webhook`.
2. The frontend opens Plaid Link with the token, and the user authenticates with the bank.
3. `onSuccess(public_token, metadata)` → `POST /api/plaid/exchange` → `/item/public_token/exchange` → **encrypt `access_token` with Fernet** → store `plaid_item` and `account` rows from metadata plus `/accounts/get`.
4. Enqueue `initial_sync(item_id)`.
5. **Guard:** before creating a link token in production, check the Item count. If 8 or more, require typing a confirmation phrase in the UI. Never allow an 11th.

### 7.2 Sync
- **Transactions:** a `/transactions/sync` cursor loop. Apply `added`/`modified`/`removed`, persist the cursor **only after the DB commit**, handle `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION` by restarting from the last saved cursor, and map pending→posted using `pending_transaction_id`.
- **Balances:** `/accounts/balance/get` once daily (it's the expensive call), writing a `balance_snapshot`. Use cached `/accounts/get` balances for intraday views.
- **Investments:** `/investments/holdings/get` daily and `/investments/transactions/get` (24-month window) daily.
- **Liabilities:** `/liabilities/get` weekly.
- **Statements** (if enabled): list and download available statement PDFs into the `document` table, then run them through the same PDF importer for **verification** (reconcile Plaid transactions against the statement totals).

### 7.3 OAuth institutions (Wells Fargo, etc.)
- Register `PLAID_REDIRECT_URI` in Plaid Dashboard → Developers → API → Allowed redirect URIs.
- Production redirect URIs must be HTTPS. Use your own domain with Caddy's DNS-01 cert (Section 12.1), e.g. `https://money.yourdomain.com/plaid/oauth-return`.
- The frontend route `/plaid/oauth-return` re-initializes Link with `receivedRedirectUri = window.location.href`.

### 7.4 Webhooks (optional)
Plaid webhooks require a publicly reachable URL. Options: (a) **skip webhooks** and poll every 4–6 hours via the scheduler (fine for personal use, and the default); (b) Tailscale Funnel exposing **only** `/api/plaid/webhook`, with Plaid JWT verification (`/webhook_verification_key/get`) enforced.

### 7.5 Errors
- `ITEM_LOGIN_REQUIRED` / `PENDING_EXPIRATION` → mark the Item `needs_relink`, notify, and offer **update-mode Link** (reuses the same Item and **doesn't consume a new slot**).
- Rate limit / `PRODUCT_NOT_READY` → exponential backoff in the job.
- Log `request_id` from every Plaid error.

---

## 8. Domain algorithms

### 8.1 Historical import (2020 → Plaid coverage start)
1. Upload statements (bulk drag-and-drop). Hash each with SHA-256 to skip duplicate files.
2. Detect the bank profile from text fingerprints (YAML `profiles/*.yaml` define: header regexes, transaction table region, date format, sign convention, opening/closing balance regex).
3. Extract transactions → `NormalizedTxn`.
4. **Reconcile per statement:** `opening + Σ(txns) == closing` within $0.01. If it fails, mark the document `needs_review` and show the diff in the UI. **Never silently accept an unreconciled statement.**
5. **Dedupe against Plaid** (`dedupe.py`): match on account, amount, and date within ±3 days, plus normalized description similarity (rapidfuzz ≥ 85). Plaid wins, and the imported row is linked as `superseded_by`.
6. Fingerprint = `sha256(account_id|date|amount|normalized_desc|seq)` so re-imports are idempotent.

### 8.2 Categorization pipeline
Order (first hit wins, `category_source` recorded):
1. **User rules** by priority.
2. **Transfer detection** (8.3).
3. **Plaid Personal Finance Category** mapped to your category tree through a mapping table.
4. **ML model:** TF-IDF on `merchant + description` plus amount-bucket and account-type features, using logistic regression trained on user-confirmed labels. Accept only if probability ≥ 0.80. Retrain nightly once there are 200+ labels.
5. Otherwise, **Uncategorized** goes into a review queue.

When a user recategorizes, offer to "create a rule for all <merchant>?" and apply it retroactively with a preview.

### 8.3 Transfer and card-payment detection
Pair an outflow in account A with an inflow of the same absolute amount in account B (same household) within 0–5 days, with description hints (`TRANSFER`, `PAYMENT THANK YOU`, `ONLINE PMT`, institution names). Mark both `is_transfer` with a shared `transfer_group_id`. Transfers are excluded from income/spend metrics. A credit card payment from checking is a transfer, and the card purchases are the expenses.

### 8.4 Recurring detection
Group by normalized merchant. For each group with 3 or more occurrences, compute the interval series and test for weekly (7±2), biweekly (14±2), semi-monthly, monthly (28–33), quarterly or annual cadence. Amount stability = coefficient of variation < 0.15 (fixed) or < 0.35 (variable, like utilities). Output: merchant, cadence, typical amount, next expected date, last seen, and status (`active`, `late`, `possibly_cancelled` once 1.5 intervals pass with no charge). Paychecks are detected the same way on inflows, which feeds the forecast.

### 8.5 Real-time budgets
- Recompute on every sync commit (an event hook enqueues `rebuild_budget_status`).
- Per line: `spent`, `remaining`, `pct`, and **pace** = `spent / (days_elapsed / days_in_period)`, projected to period end.
- Alerts at `alert_pct` and at 100%, plus a "pace alert" when the projection exceeds 110% before mid-period.
- Envelope mode supports rollover. Zero-based mode requires income minus all lines = 0.

### 8.6 Metrics (dashboard)
| Metric | Definition |
|---|---|
| Net cash flow | Σ inflows − Σ outflows (excluding transfers), by month |
| Savings rate | (income − spending) / income, where retirement contributions count as savings (pulled from investment_txn and paystub data) |
| Net worth | Σ asset balances − Σ liability balances, daily from snapshots |
| Liquid runway | liquid cash / trailing-3-month average essential spend |
| Spend by category | trailing 12 months, MoM and YoY deltas, outlier flags (z-score > 2) |
| Fixed vs. variable | from recurring detection |
| Debt metrics | utilization per card and overall, weighted APR, payoff timeline (avalanche vs. snowball) |
| Retirement | YTD contributions vs. annual limit (from tax config), employer match captured, projected balance at target age (Monte Carlo, 1,000 runs) |

### 8.7 Forecasting
- **Cash-flow forecast (30/90/365 days):** a hybrid model. Known recurring items (8.4) are scheduled deterministically. The residual discretionary spend is modeled by category with ETS on weekly aggregates. Output P10/P50/P90 bands for daily projected balance per account and in total.
- **Low-balance alert** if P10 drops below the user threshold within 30 days.
- **Backtest:** every month, record forecast vs. actual MAPE and show it on the Forecast page so you know how much to trust it.
- **What-if:** the UI lets you add a hypothetical income or expense (raise, car purchase, new subscription) and re-runs the projection.

### 8.8 Alerts
Configurable: large transaction (> $X), new merchant over $Y, duplicate charge (same merchant and amount within 48h), recurring price increase (> 10%), budget thresholds, low projected balance, Item needs relink, new hard inquiry or new tradeline on a credit report, unreconciled statement. Delivered by email and push (ntfy).

---

## 9. Tax module

### 9.1 Year-round tracking
- Transactions get a `tax_tag` via rules or the agent: `charity`, `medical`, `hsa_qualified`, `childcare`, `education`, `business_*`, `home_office`, `mortgage_interest`, `property_tax`, `state_tax_paid`, `estimated_tax_payment`.
- Contributions are pulled from investment_txn: 401(k) pre-tax/Roth, IRA, HSA (employee and employer).
- **Withholding tracker:** enter or parse paystubs (W-4 settings, YTD federal and state withholding), then project year-end withholding vs. liability. Alert in Q3 if a balance due or a large refund is projected.

### 9.2 Yearly config (no hardcoded tax numbers)
`backend/app/tax/config/2026.yaml`. **Every value carries a source**, and Claude Code must fill these from official IRS/state publications and leave `TODO_VERIFY` where unsure:

```yaml
year: 2026
sources:
  brackets: "IRS Rev. Proc. <number> — https://www.irs.gov/..."
federal:
  standard_deduction: { single: TODO_VERIFY, mfj: TODO_VERIFY, hoh: TODO_VERIFY }
  brackets:
    mfj: [ {upto: TODO, rate: 0.10}, ... ]
  ltcg_brackets: { ... }
  niit_threshold: { ... }
  limits:
    401k_elective: TODO_VERIFY
    401k_catchup_50: TODO_VERIFY
    ira: TODO_VERIFY
    hsa_self: TODO_VERIFY
    hsa_family: TODO_VERIFY
    hsa_catchup_55: TODO_VERIFY
  credits: { child_tax_credit: TODO_VERIFY, ... }
  salt_cap: TODO_VERIFY        # changed by OBBBA; verify
state:
  sc: { ... }                  # module in tax/state/sc.py
```

A test fails the build if any `TODO_VERIFY` remains in the config for the year being prepared.

### 9.3 Document ingestion
Parsers for W-2, 1099-INT, 1099-DIV, 1099-B (with cost basis), 1099-R, 1099-SA, 5498-SA, 1098, 1098-E, 1098-T, 1095-A/B/C and prior-year 1040. Each extracted field is saved as `parsed_field` with a confidence score. The **UI requires you to verify every field** before it becomes a `tax_item`. Cross-checks: W-2 box 1 vs. paystub YTD, 1099-INT vs. Plaid interest transactions, 5498-SA vs. HSA contributions seen in transactions.

### 9.4 Estimation engine
`engine.py` computes AGI → deductions (standard vs. itemized, picking the higher) → taxable income → ordinary and capital-gains tax → credits → other taxes → total liability → minus withholding and estimated payments → refund or balance due. The state module runs the same flow. It outputs a line-item trace (every number links back to its sources). Golden tests use IRS worked examples from the instructions for the relevant forms. Optionally cross-check against `taxcalc` and flag differences above $50.

### 9.5 Relief scanner + strategy engine

The agent doesn't wait to be asked. It runs a **relief scanner**: a checklist of every provision in `tax/relief_catalog.yaml`, evaluated against your data every quarter and at year-end. Each entry has an eligibility test, the data it needs, an estimated savings formula, an IRS source, a confidence level and an audit-risk rating. Anything eligible becomes a `tax_scenario` with a computed dollar delta. Anything *possibly* eligible but missing data becomes a "need info" question (for example, "Did you pay for childcare in 2026?").

The catalog is maintained per tax year. Claude Code builds the initial version and verifies every item and limit against current IRS sources. Starting categories:

**Retirement and health accounts**
- 401(k)/403(b)/457(b) pre-tax vs. Roth (marginal rate now vs. projected), catch-up contributions, **457(b) on top of a 401(k)** (common for government employees, so check your plan), and capturing the full employer match
- Traditional/Roth/backdoor Roth IRA (pro-rata check), spousal IRA
- HSA maxing, HSA as a long-term investment account (save receipts and reimburse yourself years later), family vs. self-only limits, last-month rule
- Roth conversions in low-income years, Saver's Credit eligibility
- Mega backdoor Roth if the plan allows after-tax contributions plus in-plan conversion

**New and recent law (verify each against the year's IRS guidance)**
- OBBBA deductions starting in tax year 2025: qualified tips, qualified overtime, interest on loans for qualifying new US-assembled vehicles, the additional senior deduction, and the higher SALT cap with its income phase-down
- Any expiring or ending credits (some energy credits ended or changed). The scanner must check effective dates, not assume

**Itemized deductions and timing**
- Itemize vs. standard every year, **bunching** (two years of charity or property tax in one year), donor-advised funds, appreciated stock donations instead of cash
- Medical expenses above the AGI floor (including mileage, insurance premiums paid post-tax, and dental/vision), mortgage interest and points, state and local taxes up to the cap
- Income timing: defer bonuses or accelerate deductions across year boundaries when brackets differ

**Investments**
- Tax-loss harvesting (wash-sale check across **all** linked accounts, including IRAs and the spouse's accounts), harvesting gains in the 0% LTCG bracket, specific-lot identification, asset location (bonds in tax-deferred, growth in Roth), municipal bonds when the after-tax yield wins, loss carryforward tracking

**Credits**
- Child Tax Credit, credit for other dependents, Child and Dependent Care Credit vs. dependent care FSA, AOTC/Lifetime Learning, student loan interest, adoption, EV/energy credits still in effect for the year, Premium Tax Credit reconciliation

**Side income / self-employment (if any 1099 or business income appears)**
- Schedule C expenses, the home office (simplified vs. actual), vehicle (standard mileage vs. actual), the QBI deduction, half of SE tax, self-employed health insurance, Solo 401(k) or SEP-IRA (often the single biggest lever), Section 179/bonus depreciation, hiring your children (with the payroll rules), the 280A(g) 14-day rental rule (flagged as high-scrutiny, requires fair-market documentation)

**Real estate (if applicable)**
- Rental depreciation, cost segregation, passive loss rules and the $25k allowance, the home-sale exclusion, 1031 exchanges

**Family and filing status**
- MFJ vs. MFS vs. HOH comparison, kiddie tax, 529 plans (check the state deduction for South Carolina's plan), gifts to family

**State (South Carolina module)**
- State-specific deductions, credits and retirement-income treatment, plus conformity differences from federal

**Withholding and penalties**
- W-4 tuning so you don't give the IRS an interest-free loan or owe a penalty, safe-harbor check, annualized-income method when income is lumpy

**Look-back: money left on the table**
- Review prior-year returns (upload the PDFs) for missed deductions or credits. You can generally amend with **Form 1040-X within 3 years of filing** (or 2 years of paying), so the agent flags any refund still claimable and drafts the amendment worksheet.

**Risk labels on every recommendation**
| Label | Meaning |
|---|---|
| 🟢 Standard | Mainstream, clearly allowed, low audit attention |
| 🟡 Documentation-heavy | Legal, but you need receipts or logs (home office, mileage, charity over $250) |
| 🟠 Scrutinized | Legal but a known audit target or fact-dependent (280A(g), large Schedule C losses, hobby vs. business). The agent states exactly what documentation makes it defensible |
| 🔴 Won't recommend | No legal basis or evasion: unreported income, fake or personal expenses as business, made-up numbers |

The agent presents everything green through orange with dollar impact and lets **you** choose. Red items are declined with the reason.

### 9.6 Year-end package (`package.py`)
Generates `TaxPackage_<year>.zip` containing:
1. `01_Summary.pdf`: projected federal and state result, key numbers, open questions
2. `02_Strategy_Memo.pdf`: each recommendation with estimated savings, rationale, IRS source citations and confidence
3. `03_Workpapers.xlsx`: tabs for income, adjustments, deductions, credits, payments, a source-document index and the transaction-level backup
4. `04_Entry_Guide.pdf`: form-by-form, line-by-line values to type into your filing software, each traced to source documents
5. `05_Source_Documents/`: all verified uploaded PDFs
6. `06_Review_Flags.pdf`: open questions, missing documents, and the 🟠 items with the documentation checklist for each
7. `07_Amendments/`: 1040-X worksheets for any prior-year refunds still claimable

### 9.7 Self-filing workflow
The agent prepares the return and you submit it. Supported paths:
| Path | How the package helps |
|---|---|
| **IRS Free File Fillable Forms** (free, you type in form lines) | The entry guide maps every value to a form and line number. This is the closest match to the app's output |
| **Commercial software** (FreeTaxUSA, TurboTax, etc.) | The entry guide is organized by the interview sections those programs use |
| **Paper filing** | The app fills IRS fillable PDFs (Form 1040 + schedules) from the computed return, for you to review, sign and mail |
| **State (SC)** | State e-file through the same software, or the state's own filing portal |

**Pre-file checklist (the agent runs it):** every source document verified, the engine result matches what your filing software shows (±$1, and the agent explains any difference), estimated payments and withholding match IRS records (pull your IRS account transcript to confirm), and bank info for the refund is double-checked.

---

## 10. CPA agent

### 10.1 Modes
| Mode | Trigger | Model | Output |
|---|---|---|---|
| Chat ("Advisor" page) | on demand | Sonnet | Answers questions about your finances using tools |
| Monthly review | 1st of month, scheduled | Opus | `agent_finding`s: spending anomalies, budget drift, subscription creep, fee detection, savings opportunities |
| Quarterly tax check | Apr/Jun/Sep/Jan | Opus | Withholding projection, estimated-payment need, contribution pacing |
| Year-end prep | Jan–Apr, on demand | Opus | Full review per `review_checklist.md`, populates the tax package |
| Doc assist | on upload | Sonnet | Proposes field values for low-confidence parses (the user still verifies) |

### 10.2 System prompt essentials (`prompts/system_cpa.md`)
- Role: the household's tax strategist and reviewer, acting like a sharp CPA whose goal is the **lowest legal total tax** for this year and over the long run.
- **Exhaustive by default:** runs the full relief catalog every review, proactively asks for missing facts that could unlock savings, and never stops at the obvious items.
- Only uses numbers obtained from tools. **Never invents figures.** If data is missing, it asks or creates a "missing document" finding.
- Every tax claim cites the IRS publication, form instruction or code section, and the tax year.
- Each recommendation carries an estimated dollar impact, a confidence level (settled / likely / fact-dependent) and a risk label (Section 9.5), ranked by savings.
- Presents aggressive-but-legal options rather than hiding them, along with the documentation needed to defend them. Declines evasion plainly.
- Thinks multi-year: Roth vs. pre-tax, carryforwards, bunching cycles, next year's W-4.
- Explains in plain language and shows the math.

### 10.3 Tools (all read-only except where marked)
| Tool | Purpose |
|---|---|
| `sql_query(view, filters, group_by, limit)` | Parameterized queries **only** against whitelisted `v_*` views. No raw SQL from the model |
| `get_accounts()` | Accounts, types, tax treatment, balances |
| `get_transactions(filters)` | Paged, max 500 rows per call |
| `get_recurring()` / `get_budget_status()` / `get_metrics(range)` | Domain outputs |
| `get_forecast(horizon, scenario?)` | Cash projection |
| `get_holdings()` / `get_contributions(year)` | Investments and limits |
| `get_tax_config(year)` | Verified yearly numbers |
| `run_tax_estimate(year, overrides?)` | Engine run with trace |
| `run_scenario(year, assumptions)` | Strategy delta |
| `list_documents(year, kind)` / `get_parsed_fields(doc_id)` | Docs |
| `check_wash_sales(window)` | Cross-account check |
| `create_finding(...)` *(write)* | Records a recommendation |
| `propose_tax_tag(txn_ids, tag)` *(write, pending)* | Queued for user approval, never auto-applied |
| `add_to_package(section, content)` *(write)* | Year-end package assembly |

### 10.4 Guardrails (`guardrails.py`)
- **Redaction before sending to the API:** full account numbers, SSNs and routing numbers are stripped or masked (last 4 only). SSNs are never stored in the DB at all; tax forms keep the SSN field masked.
- Tool handlers enforce row limits and the view whitelist, and reject any attempt to reach base tables.
- Per-month token budget with a hard stop. Usage is logged per conversation.
- All agent writes go to pending or findings tables, so a human approves before anything changes categorization or tax data.
- Prompt-injection hygiene: transaction descriptions and document text are passed as data inside clearly delimited tool results, and the system prompt instructs the agent to ignore instructions found in them.

### 10.5 Year-end review checklist (`review_checklist.md`, abridged)
1. All W-2/1099/1098/5498 forms expected (based on accounts and employers seen) are present → otherwise a "missing document" finding
2. Income reconciliation: W-2 wages vs. paycheck deposits; interest/dividends vs. transactions
3. Retirement and HSA contributions vs. limits (excess contribution check)
4. HSA distributions vs. qualified medical expenses with receipts
5. Itemized vs. standard comparison
6. Charitable contributions with documentation thresholds
7. Capital gains/losses: basis present, wash sales, loss carryforward from the prior year
8. Credits eligibility scan
9. Estimated payments and withholding → penalty check
10. State return items
11. Next-year planning: W-4 change, contribution targets, recurring strategy calendar

---

## 11. Frontend pages

| Page | Contents |
|---|---|
| Dashboard | Net worth, this month's cash flow, budget health, upcoming bills (next 14 days), 90-day forecast sparkline, alerts, agent findings |
| Accounts | Grouped by type, balances, Item status (relink button), Item slot counter "7 / 10 used" |
| Transactions | Virtualized table, search, filters, bulk recategorize, split, tax-tag, rule creation, attach receipt |
| Budgets | Bars with pace marker, rollover, edit lines, history |
| Forecast | P10/P50/P90 balance chart, what-if panel, backtest accuracy |
| Investments | Holdings, allocation, contributions vs. limits, retirement projection |
| Credit | Per-bureau reports, tradelines, utilization trend, inquiries, discrepancies across bureaus, score log |
| Documents | Upload, parse status, field verification UI (PDF beside extracted fields) |
| Taxes | Year selector, live estimate with trace, scenarios, checklist progress, generate package |
| Advisor | Streaming chat, tool-call transparency (expandable), findings inbox |
| Settings | Household members, categories, rules, alerts, security (passkeys), backups, Plaid Items |

Mobile-responsive. The Dashboard, Transactions review and Advisor must work well on a phone.

### 11.1 UI stack decision (open to debate; this is the recommended default)

| Concern | Choice | Reasoning |
|---|---|---|
| Framework | **React + TypeScript (strict) + Vite** | No SSR needed for a private single-household app. Vite builds a static bundle that Caddy serves. Next.js would add a Node server and complexity with no benefit here |
| Styling | **Tailwind CSS** (v4) | Utility-first, pairs with every library below |
| Core components | **shadcn/ui** (Radix primitives) | Tables, forms, dialogs, sheets, command palette, date pickers, charts. Code is copied into `web/src/components/ui/`, so you own it. Accessible and keyboard-friendly. This is the foundation of the UI |
| Data tables | **TanStack Table** + shadcn `DataTable` pattern, plus `@tanstack/react-virtual` | Tens of thousands of transactions need virtualization |
| Charts | **shadcn charts (Recharts)**; add **Tremor** or **visx** only if needed | Consistent theming with the components |
| Accent/motion | **Magic UI** and **Aceternity UI**, used *sparingly* | Animated number tickers for net worth, subtle backgrounds, card hover effects on the dashboard and login. **Don't** use them in dense data views (Transactions, Workpapers): heavy motion hurts readability and battery on a phone. Both are copy-in components on Tailwind + Framer Motion, so they fit the shadcn model |
| Animation | **Motion** (Framer Motion) | Required by Magic/Aceternity anyway |
| Forms | **react-hook-form + zod** | Zod schemas generated from the OpenAPI spec keep frontend and backend validation in sync |
| API client | `openapi-typescript` + `openapi-fetch`, wrapped in TanStack Query | Fully typed from FastAPI |
| State | TanStack Query for server state, **Zustand** for small UI state | No Redux needed |
| Icons | `lucide-react` | Default for shadcn |
| Theme | Light/dark via shadcn CSS variables, following the system setting | |

**Mobile + web:** one codebase, built as an installable **PWA** (`vite-plugin-pwa`): add to home screen on iOS/Android, full-screen, app icon, offline shell. Responsive rules:
- Bottom tab bar on mobile (Dashboard, Transactions, Budgets, Advisor, More) and sidebar on desktop
- shadcn `Sheet`/`Drawer` (vaul) instead of modals on small screens
- Transactions become swipeable cards on mobile (swipe to categorize or tag) and a table on desktop
- Touch targets ≥ 44px; test at 375px width
- **Don't cache financial API responses in the service worker.** Cache only the app shell, so data never sits unencrypted on the phone

**When to go native instead:** if you later want Face ID app lock, background push without a browser, or widgets, wrap the same React app in **Capacitor** (keeps one codebase). React Native would mean rewriting the UI, so avoid it unless there's a strong reason.

Folder additions:
```
web/src/components/ui/        # shadcn components (generated via `npx shadcn@latest add ...`)
web/src/components/magic/     # Magic UI / Aceternity copies, isolated
web/src/components/mobile/    # BottomNav, SwipeCard, MobileSheet
web/src/hooks/useBreakpoint.ts
web/components.json           # shadcn config
```

---

## 12. Security and operations

- **Encryption:** Plaid tokens with Fernet (key in `.env`, rotation procedure in the runbook). The Postgres data dir sits on a LUKS-encrypted volume. Documents are encrypted at rest (age or Fernet per file).
- **Network:** no public ports. See 12.1 for private access on your own domain.

### 12.1 Private access on your own domain (phone on Wi-Fi or LTE)

**Why not IP allowlisting / static routes:** on LTE your phone's public IP changes constantly and sits behind carrier NAT shared with many other customers, so an IP allowlist either locks you out or lets strangers in. Identity-based access through a private tunnel solves this.

**Recommended: Tailscale (WireGuard) + your domain + Caddy**

```
Phone (Wi-Fi or LTE) ──Tailscale/WireGuard──▶ home server (100.x.y.z)
        │                                         │
  https://money.yourdomain.com          Caddy :443 (bound to Tailscale IP only)
        │                                         │
  DNS: money.yourdomain.com → 100.x.y.z     web (static) + /api → FastAPI
```

1. Install Tailscale on the server, your phone and your laptop. In the Tailscale admin, enable **device approval** and set an **ACL** that lets only your devices (tagged `tag:owner`) reach `tag:holdfast` on port 443.
2. DNS at your registrar: `money.yourdomain.com` → **A record pointing to the server's Tailscale IP (100.x.y.z)**. It resolves publicly but is unreachable unless you're on the tailnet. (Alternatively, use Tailscale split DNS to keep the record off public DNS entirely.)
3. **Caddy** with a DNS-01 ACME challenge (e.g. `caddy-dns/cloudflare` or your registrar's plugin) gets a real Let's Encrypt cert without opening any port. Bind Caddy to the Tailscale interface only.
4. Server firewall (ufw): deny all inbound on the LAN/WAN interface; allow 443 only on `tailscale0`; SSH only over Tailscale.
5. The phone runs Tailscale "always on" (iOS On-Demand / Android Always-on VPN), so it works on LTE automatically. Install the PWA from `https://money.yourdomain.com`.
6. The app's own passkey login stays on as a second layer, so a lost phone that's still on the tailnet isn't enough.

**Caddyfile sketch**
```
{
  email you@yourdomain.com
}
money.yourdomain.com {
  bind 100.x.y.z
  tls {
    dns cloudflare {env.CF_API_TOKEN}
  }
  encode zstd gzip
  header {
    Strict-Transport-Security "max-age=31536000"
    X-Frame-Options DENY
    X-Content-Type-Options nosniff
    Referrer-Policy no-referrer
    Content-Security-Policy "default-src 'self'; script-src 'self' https://cdn.plaid.com; frame-src https://cdn.plaid.com; connect-src 'self' https://*.plaid.com"
  }
  handle /api/* {
    reverse_proxy api:8000
  }
  handle {
    root * /srv/web
    try_files {path} /index.html
    file_server
  }
}
```

**Alternatives considered**
| Option | Verdict |
|---|---|
| Self-hosted **WireGuard** (or Headscale) | Same model with no third-party coordination server, but more upkeep. Good if you don't want to rely on Tailscale |
| **Cloudflare Tunnel + Cloudflare Access** | Works on LTE with email/passkey login and no VPN app. The trade-off is that the hostname is public and Cloudflare terminates TLS, so your financial data passes through it in plaintext at their edge. Acceptable for many people, but less private |
| **mTLS client certificate** on the phone | Strong, but iOS cert management is painful. Can be layered on top of Tailscale if you want belt and suspenders |
| Port-forward + IP allowlist | Rejected (see above) |

**Plaid implications:** the OAuth redirect URI `https://money.yourdomain.com/plaid/oauth-return` works fine on a private hostname, because the redirect happens inside your browser. Webhooks need a public endpoint, so stay with scheduled polling (Section 7.4), or use Tailscale Funnel / a Cloudflare Tunnel exposing **only** `/api/plaid/webhook`.
- **Auth:** passkeys (WebAuthn) primary, TOTP fallback, 12-hour sessions, re-auth for sensitive actions (adding an Item, export, package generation).
- **Audit log:** every login, Plaid call type, export, agent run and settings change.
- **Backups:** nightly `pg_dump` plus documents → `restic` to an encrypted offsite target (B2/S3). **Test restores monthly** (`scripts/restore.sh` into a scratch container).
- **Dependencies:** Renovate/Dependabot, and `pip-audit` + `npm audit` in CI.
- **Secrets hygiene:** pre-commit with `gitleaks`, and `.env`, `data/` and `*.pdf` gitignored.
- **Data minimization:** don't store SSNs, full account numbers, or bank usernames/passwords (Plaid handles those).
- **Plaid policy:** keep the Plaid client_id and secret private, as Plaid's Developer Policy requires.

---

## 13. Phased build plan

Each phase ends with passing tests and a demo. Estimates assume evenings and weekends with Claude Code.

### Phase 0 — Scaffold (1–2 days)
- Repo layout per Section 4, Docker Compose (db, redis, api, worker, web, caddy), `.env.example`, `scripts/dev.ps1` + Makefile, ruff/mypy/pytest/eslint/vitest, pre-commit with gitleaks, GitHub Actions CI.
- FastAPI health endpoint, React shell with routing, Alembic baseline.
- **Accept:** `make up` / `./scripts/dev.ps1 up` brings everything up; CI is green; `/api/health` returns OK through Caddy HTTPS.

### Phase 1 — Auth, household, data model (2–3 days)
- All ORM models and migrations from Section 5. Household/person/user management. Passkey + TOTP login. Audit log.
- **Accept:** Can create a household with 2 people, log in with a passkey, and all tables exist with indexes.

### Phase 2 — Plaid (Sandbox first) (4–6 days)
- Link token, exchange, encrypted token storage, the Item slot counter and guard, transactions sync loop, balances, investments, liabilities, update-mode relink, error handling, scheduled polling.
- `scripts/seed_sandbox.py` creates sandbox Items (`user_good`/`pass_good`, plus `user_transactions_dynamic` for realistic flows).
- **Accept:** Sandbox Items sync fully; re-running sync is idempotent; simulated `ITEM_LOGIN_REQUIRED` triggers the relink flow; tests cover cursor restart.
- **Then:** apply for the Trial plan, fill in the Section 3.2 table, and link real accounts **one at a time**, verifying each.

### Phase 3 — Historical import (4–6 days)
- CSV/OFX/PDF importers, bank profiles for Current, NFCU and Wells Fargo, statement reconciliation, dedupe vs. Plaid, review UI for failures.
- **Accept:** Importing a full year of statements per bank reconciles 100%, and re-importing creates zero duplicates. The overlap months with Plaid data dedupe correctly (verified on fixtures).

### Phase 4 — Categorization, transfers, recurring (3–4 days)
- Category tree seed, Plaid PFC mapping, rules engine with retroactive preview, transfer pairing, recurring detection, ML model with nightly retrain.
- **Accept:** At least 90% of transactions auto-categorized after you label about 200; transfers are excluded from spend; recurring list matches your real bills.

### Phase 5 — Budgets, metrics, forecasts, alerts (5–7 days)
- Real-time budget computation, dashboard metrics, cash-flow forecast with bands and backtest, what-if, alerts over email and ntfy.
- **Accept:** A new transaction updates the budget within one sync cycle; forecast backtest MAPE is displayed; alerts fire in tests.

### Phase 6 — Credit (2–3 days)
- Upload AnnualCreditReport PDFs per bureau, parse tradelines and inquiries, cross-bureau discrepancy detection, report-to-report diffing, manual score log.
- **Accept:** Three real reports parse; a new inquiry between two pulls generates an alert.

### Phase 7 — Tax foundation (6–8 days)
- Yearly config with sources (no `TODO_VERIFY` left for the target year), document parsers + verification UI, tax tagging, withholding tracker, estimation engine + state module, golden tests, optional taxcalc cross-check.
- **Accept:** Engine matches the IRS worked examples; last year's actual return reproduces within $1 when fed last year's documents.

### Phase 8 — CPA agent (5–7 days)
- Anthropic client, model routing, tools + whitelist, guardrails/redaction, chat UI with streaming and visible tool calls, scheduled monthly/quarterly runs, findings inbox, scenario modeling, year-end package generation.
- **Accept:** The agent answers "what did we spend on dining in Q3 vs. last year?" correctly from tools; a monthly review produces findings with source references; a year-end dry run on last year's data produces a complete package; a redaction test proves no full account numbers or SSNs leave the server.

### Phase 8.5 — Mobile + private access (2–3 days)
- PWA manifest, icons and service worker (app shell only), bottom nav, swipe-to-categorize cards, sheet-based dialogs, Lighthouse PWA audit.
- Caddy on your domain with DNS-01, bound to the Tailscale IP; ufw rules; Tailscale ACLs; always-on VPN on the phone.
- **Accept:** The PWA installs on your phone and works on LTE through Tailscale; the domain times out from a device not on the tailnet; the Plaid OAuth redirect works on the domain.

### Phase 9 — Hardening (ongoing)
- Backups + restore drill, key rotation runbook, performance (materialized view refresh), mobile polish, docs.

---

## 14. CLAUDE.md (create this file in the repo root)

```markdown
# CLAUDE.md — Holdfast

## Project
Private, self-hosted personal finance platform + tax-prep agent for one household. See PLAN.md for full spec. Work phase by phase; stop at the end of each phase for review.

## Commands
- Linux: `make up | down | test | lint | migrate | seed`
- Windows (pwsh 7): `./scripts/dev.ps1 up|down|test|lint|migrate|seed`
- Backend tests: `cd backend && uv run pytest -q`
- Frontend: `cd web && npm run dev | test | lint`

## Hard rules
1. Money is Decimal / NUMERIC(14,2). Never float. Outflow negative, inflow positive.
2. Never hardcode tax figures. They live in backend/app/tax/config/<year>.yaml with a source URL per value. Unknown → TODO_VERIFY.
3. Never log or print Plaid access tokens, API keys, SSNs or full account numbers. Plaid tokens are Fernet-encrypted at rest.
4. PLAID_ENV defaults to sandbox. Never write code that creates production Items without the slot guard (max 10, confirm at ≥8).
5. Agent tools are read-only against whitelisted v_* views. Agent writes go only to pending or findings tables.
6. All imports must be idempotent. All statements must reconcile or be flagged.
7. Every new module gets tests. Fixtures must be synthetic or sanitized, and real statements never go in the repo.
8. Migrations through Alembic only, with no manual schema edits.
9. Don't add new external services without an ADR in docs/adr/.
10. Windows scripts target PowerShell 7 (pwsh), not Windows PowerShell 5.
11. Crypto: never accept, store, log or transmit seed phrases or private keys. Only xpubs, descriptors and addresses. Signing happens on the hardware wallet (client-side WebUSB/QR/file); the server only builds and broadcasts PSBTs. Spending stays behind FEATURE_CRYPTO_SPEND=false and is self-host only. Exchange API keys must be read-only.

## Style
Python: ruff + mypy strict, FastAPI routers thin, logic in app/domain. TS: strict mode, TanStack Query, no any.
UI: shadcn/ui is the base component set (add via `npx shadcn@latest add`). Magic UI / Aceternity only in web/src/components/magic/ and only on Dashboard/login, never in dense data views. Mobile-first: test at 375px. The service worker caches the app shell only, never API responses.
Networking: Caddy binds to the Tailscale IP only. Never add a public port or port-forward.
```

---

## 15. Kickoff prompts for Claude Code (copy one per phase)

- **Phase 0:** "Read PLAN.md and create CLAUDE.md from Section 14. Implement Phase 0 exactly as specified in Section 13. Use uv for Python. Make dev.ps1 work in pwsh 7. Stop when the acceptance criteria pass and show me the results."
- **Phase 2:** "Implement Phase 2 Plaid integration per Sections 7 and 13 in sandbox only. Include the 10-Item guard and update-mode relink. Write tests with recorded sandbox JSON fixtures."
- **Phase 3:** "Implement Phase 3 importers per Section 8.1. I'll drop sanitized sample statements in backend/tests/fixtures/statements/. Build bank profiles from them. Reconciliation failures must surface in the UI."
- **Phase 7:** "Implement the Phase 7 tax foundation. For config/<year>.yaml, look up each value from official IRS sources and cite the URL. Leave TODO_VERIFY for anything you can't confirm. Build golden tests from IRS form-instruction examples."
- **Phase 8:** "Implement the Phase 8 agent per Section 10. Start with the tool layer and guardrails plus their tests, then the chat UI, then scheduled reviews, then package generation."

---

## 16. Your to-do list (things only you can do)

1. Sign up for Plaid: https://dashboard.plaid.com/signup → verify email → apply for the **Trial plan** (identity verification).
2. In Dashboard → Products, confirm which products the Trial bundle includes for your team.
3. Fill in the **10-Item table** (Section 3.2). Confirm your HSA custodian and the 401(k) recordkeeper login.
4. Get an Anthropic API key at console.anthropic.com and set a monthly spend limit.
5. Pick the host: a personal Ubuntu machine or VPS (not employer equipment). Install Tailscale on it, your phone and your laptop; set ACLs; create the `money.yourdomain.com` A record pointing to the server's Tailscale IP; create a DNS API token for Caddy's DNS-01 cert (Section 12.1).
6. Gather historical statements from 2020 onward: Current (in-app Documents → Statements, or request via in-app chat), NFCU and Wells Fargo (online banking statement archives).
7. Pull all 3 credit reports from AnnualCreditReport.com and save them as PDFs.
8. Collect last year's tax return and forms (used for engine validation).
9. Decide on a backup target (Backblaze B2 or similar) and store the restic password and Fernet key in a password manager. **If you lose the Fernet key, every Plaid Item must be relinked.**

---

## 17. Future: open source + paid hosting (design for it now, launch later)

**Scope decision:** the product is the **personal finance + budgeting app**. The tax/CPA agent stays a **personal, self-host-only module**: it's off by default, ships in the open-source repo, and is **excluded from any hosted offering** unless separately reviewed later. Build it as an isolated module (`backend/app/tax/`, `agent/` tax tools, and the Taxes page behind a `FEATURE_TAX=true` flag) so it can be left out cleanly.

### 17.1 Two distribution models
| Model | Who pays Plaid/AI | Your cost | Your legal exposure |
|---|---|---|---|
| **Self-hosted open source** (like Actual Budget or Firefly III) | Each user brings their **own** Plaid keys (their own free Trial plan) and their own Anthropic key | ~$0 | Minimal. You publish code; you never touch their data |
| **Hosted SaaS** ("Holdfast Cloud") | You, through a paid Plaid Production plan plus your AI bill, passed on in the subscription | Plaid per-Item fees + AI tokens + hosting + compliance | Significant (17.3). You're now holding strangers' financial and tax data |

**Recommended path:** release open source + self-hosted first (it costs you nothing and builds users), then offer hosting as the paid tier for people who don't want to run servers. This open-core model is common.

### 17.2 License
- **AGPL-3.0** for the core: anyone can self-host and modify, but a competitor offering it as a hosted service must publish their changes. This protects your hosted business.
- Alternatives: a fair-source / BSL-style license if you want to forbid competing hosted offerings outright, at the cost of not being OSI "open source".
- Use a CLA or DCO for contributors so you keep the right to dual-license.

### 17.3 What changes when strangers' data is on your servers (get a lawyer before launch)
- **Plaid:** the Trial plan is for personal and hobby use. A commercial product needs a paid Production plan, Plaid's security questionnaire, and per-Item pricing (Transactions and Investments are monthly per-Item subscriptions). Model this before setting a price.
- **GLBA / FTC Safeguards Rule:** apps that hold consumers' financial data generally fall under it. It requires a written information-security program, a designated security owner, risk assessments, MFA, encryption, vendor oversight and breach notification.
- **Tax features:** don't market it as a "CPA". CPA is a licensed title regulated by states. Position it as **tax-prep and planning software that users self-file with** (the TurboTax model). Tax-return information has its own federal disclosure restrictions (IRC §7216) that likely apply to software providers. Verify this with counsel, and don't use or share tax data for anything except the user's own return.
- **AI processing:** disclose that data is sent to the AI provider, use an API tier with no training on your data and suitable retention terms, and keep the redaction layer (Section 10.4).
- **Expect to need:** a privacy policy, terms of service, SOC 2 Type I eventually (people will ask), cyber liability insurance, and an LLC so it isn't you personally on the hook.

### 17.4 Architecture choices to make now so this stays possible
- `household_id` on every row is already in the schema (Section 5). Keep it there and enforce it with **Postgres row-level security** so tenant isolation is guaranteed by the DB, not just app code.
- **Bring-your-own-keys config:** Plaid and Anthropic credentials can come from `.env` (self-host) or a per-household encrypted settings table (hosted). Build the abstraction in Phase 2.
- Keep the `relief_catalog.yaml` and tax config **data-driven and versioned**. These are valuable, and community contributions can keep them current each year.
- A feature-flag layer separates core (open) from hosted-only extras (managed backups, multi-device sync, priority AI models).
- No personal assumptions in code (your state, your institutions, your name). Those live in household settings.
- Telemetry is opt-in only, and none by default in self-hosted builds.

### 17.5 Pricing sketch (fill in with real numbers once you have usage data)
```
monthly cost per user ≈ (Plaid Items × per-Item monthly fee)
                       + (AI tokens per month × model price)
                       + (hosting ÷ users) + (compliance/insurance ÷ users)
price = cost × 2.5–3     # margin for support, refunds, growth
```
Measure the real AI token usage on your own household during year one (the app already logs tokens per conversation). Year-end tax review will be the expensive spike, so consider a separate "Tax season" add-on rather than baking it into the monthly price.

### 17.6 Milestones
1. Run it on your own finances for at least one full tax year (2026 → filed in 2027).
2. Clean the repo of anything personal, write docs, publish under AGPL, add a one-command self-host installer.
3. Gather self-hosters and issues; see whether people would pay for hosting.
4. Form an LLC, get legal review (Section 17.3), Plaid Production approval, a Safeguards program, then launch a hosted beta with a small waitlist.

---

## 18. Crypto + self-custody module

Goal: crypto sits alongside bank, card, 401(k) and HSA accounts in one net worth, one cash-flow view and one tax picture. On self-hosted installs you can also run your own Bitcoin wallet, with keys that never leave your hardware wallet.

### 18.0 Non-negotiable principles
1. **The server never holds private keys or seed phrases.** There is no field anywhere in the UI that accepts 12/24 words. The app stores only public data: xpubs, output descriptors and addresses.
2. **Watch-only by default.** Spending is a separate, off-by-default feature (`FEATURE_CRYPTO_SPEND=false`) available only on self-hosted installs.
3. **The hardware wallet signs; the app only builds and broadcasts.** The app builds an unsigned transaction (a PSBT for Bitcoin), the device shows the address and amount on its own screen, the user approves there, and the app broadcasts the signed result through your own node.
4. **A hosted version stays watch-only, permanently.** Moving or holding other people's crypto makes you a custodian/money transmitter, with licensing obligations. Out of scope.
5. **Exchange API keys must be read-only.** Where an exchange's API reports key permissions, reject keys with trade or withdraw rights. Otherwise show a blocking warning. Keys are Fernet-encrypted like Plaid tokens.

### 18.1 Scope in three tiers
| Tier | What | Install types | Risk |
|---|---|---|---|
| **1. Track** | Watch-only wallets (BTC xpub/descriptors, EVM and other chain addresses), exchange balances + full trade/deposit/withdrawal history, cost-basis lots, unrealized/realized gains, net worth, tax | Self-hosted + hosted | Low |
| **2. Bitcoin self-custody wallet** | Descriptor wallets (single-sig + multisig), coin control, UTXO labels (BIP-329 import/export), fee estimation, PSBT build → hardware sign → broadcast via your node | Self-hosted only | Medium |
| **3. EVM send** | Build and sign ETH/L2 transfers on Ledger/Trezor | Self-hosted only, later or never | High (blind signing, contract risk). For DeFi, keep using a dedicated wallet and let the app track it |

### 18.2 Talking to hardware wallets
The server is a headless box, while the hardware wallet is plugged into your laptop or held next to your phone. **Signing has to happen client-side**, not through a USB tool on the server.

| Path | Devices | Works on | How |
|---|---|---|---|
| **Browser USB (WebHID/WebUSB)** | Ledger, Trezor, BitBox02, Jade | Chrome/Edge on desktop and Android; **not iOS Safari** | Vendor JS SDKs (Trezor Connect, Ledger transport libraries, BitBox02 API, Jade serial) inside the React app |
| **Air-gapped animated QR** | Keystone, Jade, Foundation Passport, Coldcard Q, SeedSigner | **Every phone and laptop with a camera, including the iOS PWA** | Show the PSBT as an animated QR (UR / BBQr); scan the signed PSBT back with the camera |
| **MicroSD file** | Coldcard Mk4/Q, Passport, Keystone | Any browser | Download `.psbt`, sign on the device, upload the signed file |
| **Server-side USB (HWI/BHWI)** | Most devices | Only when the device is plugged into the server | **Don't build on this.** The HWI maintainer announced a wind-down in August 2026 (no new devices or features; Rust-based BHWI named as the likely successor). Keep it behind an adapter interface if used at all |

Design the signer layer as a `Signer` interface (`get_xpub`, `display_address`, `sign_psbt`) with implementations `webusb_trezor`, `webusb_ledger`, `webusb_bitbox`, `serial_jade`, `qr_ur`, `qr_bbqr`, `file_psbt`. QR and file signing are the universal fallback and get built first.

**Onboarding a hardware wallet (watch-only):** connect or scan the device's export QR → import the xpub / output descriptor (with fingerprint and derivation path) → **verify the first receive address on the device's screen** → save as a watch-only wallet. The private key is never seen.

### 18.3 Chain data (self-hosted first)
| Chain | Preferred source | Fallback |
|---|---|---|
| Bitcoin | Your own **Bitcoin Core + Electrum server (electrs or Fulcrum)** or a self-hosted **mempool** instance (many people already run one on Umbrel/Start9) | Public Esplora/mempool APIs (privacy warning: they learn your addresses) |
| Ethereum + L2s (Base, Arbitrum, Optimism, Polygon) | Your own node RPC | RPC providers; an indexer API for token/NFT transfer history |
| Solana, others | Plugin connectors | CSV import |

Prices: a market-data provider with daily historical closes cached locally (needed for basis on every acquisition, transfer and income event). Store the price source per lot for auditability.

### 18.4 Exchanges
- **CCXT** (open-source, 100+ exchanges, Python) for balances, trades, deposits and withdrawals with read-only keys. Use native SDKs where CCXT's history coverage is thin (for example Coinbase's own API).
- **CSV importers** for exchanges without full-history APIs, plus importers for Koinly/CoinTracker/CoinLedger exports so people can migrate.
- **Transfer matching:** pair an exchange withdrawal with the wallet deposit (amount minus fee, time window, tx hash when available). This is the single most important step for correct cost basis, and the most common thing crypto tax tools get wrong.

### 18.5 Data model additions
```sql
crypto_wallet(id, household_id, owner_person_id, label, chain, kind,          -- 'watch_xpub','watch_address','spend_descriptor'
              descriptor, xpub_enc NULL, fingerprint, derivation_path, gap_limit,
              signer_type NULL, is_watch_only bool, created_at)
crypto_address(id, wallet_id, address, path, is_change, first_seen, label)
crypto_tx(id, wallet_id NULL, exchange_account_id NULL, chain, tx_hash, block_time,
          direction, asset, quantity, fee_asset, fee_qty, counterparty, label,
          classification,          -- 'buy','sell','swap','transfer_in','transfer_out','income_staking','income_mining','airdrop','gift','spend','fee'
          transfer_group_id NULL, raw jsonb)
crypto_lot(id, household_id, asset, wallet_id NULL, exchange_account_id NULL,  -- per-wallet tracking
           acquired_at, quantity, remaining_qty, cost_basis_usd, price_source,
           origin_tx_id, covered_by_broker bool)
crypto_disposal(id, lot_id, disposed_at, quantity, proceeds_usd, basis_usd,
                term, form_8949_box, tx_id)
exchange_account(id, household_id, exchange_id, label, api_key_enc, api_secret_enc,
                 passphrase_enc NULL, permissions jsonb, last_synced_at, status)
price_history(asset, date, close_usd, source, PRIMARY KEY(asset, date, source))
```
Net worth (`v_net_worth_daily`) unions crypto holdings valued at each day's close. Crypto shows up in budgets only when you spend it or receive it as income.

### 18.6 Crypto tax (self-host tax module)
- Crypto is property: every sale, swap and spend is a disposal. Staking, mining and airdrops are income at fair market value when received, which then becomes that lot's basis.
- **Per-wallet cost basis** (the regulations moved taxpayers from a universal method to wallet-by-wallet tracking). Lots are tied to the wallet or exchange account that holds them. Support FIFO and specific identification, with the method recorded per year.
- **Form 1099-DA reconciliation:**
  - Tax year 2025 forms: brokers reported **gross proceeds only**, so basis must come from the app.
  - Tax year 2026 onward: brokers report basis only for **covered** assets (acquired on or after Jan 1, 2026 and held at that broker). Anything transferred in or bought earlier is **noncovered**, and the app supplies the basis.
  - Map each disposal to its Form 8949 box (G/J when basis was reported, H/K when it wasn't, I/L when there's no 1099-DA) and flag any mismatch between the broker's form and the app's numbers.
- Relief-scanner entries: crypto tax-loss harvesting (check the year's config for whether wash-sale rules apply to digital assets, rather than assuming), long-term holding thresholds, gifting appreciated crypto, donating crypto to charity, and specific-ID lot selection.
- Exports: Form 8949 worksheet, Schedule D totals, income summary, and a TurboTax/FreeTaxUSA-compatible CSV.

### 18.7 Security for the spend tier
- A PSBT review screen shows inputs, outputs, change address (verified as yours), fee rate and total. The user must confirm on the device too.
- Optional address allowlist and a per-transaction cap; re-auth with a passkey before building any spend.
- Broadcast only through your own node when configured. Log every built, signed and broadcast PSBT in the audit log.
- Multisig support (2-of-3 across different device brands) is encouraged; store the wallet descriptor backup encrypted and offer a printable backup sheet (public data only).
- Never auto-sign, never store signed-but-unbroadcast transactions longer than the session.

### 18.8 Competitive notes
- **rotki** (AGPL-3.0, local-first, Berlin): the main open-source crypto tracker/tax tool, with 180+ exchange, chain and DeFi integrations. Its free tier limits reports (about 1,000 most recent events); paid tiers start around €25/month. It's crypto-first, with no banks, budgets or 401(k)s.
- **Sure** links Coinbase. **Specter Desktop** and **Sparrow** are strong Bitcoin wallets, but neither is a finance app.
- The gap: **no single app combines bank budgeting, retirement accounts, crypto with hardware-wallet self-custody, and taxes across all of them.** That's the "really all-in-one" pitch.

### 18.9 Phasing
- **Phase 10a, Track (after Phase 5):** watch-only BTC (descriptors/xpubs via QR or file import), EVM addresses, CCXT exchanges, price history, transfer matching, crypto in net worth.
- **Phase 10b, Crypto tax (with Phase 7):** lots, disposals, 1099-DA reconciliation, 8949 export.
- **Phase 10c, Bitcoin wallet (self-host only):** the `Signer` interface; QR + file signing first, then WebUSB for Trezor/Ledger/BitBox/Jade; your own node for broadcast.
- **Phase 10d, EVM send:** only if there's real demand.
- **Acceptance (10c):** send testnet/signet coins with a Coldcard (file), a Jade or Keystone (QR from the phone PWA) and a Trezor (WebUSB). The server holds no private key material at any point (verified by grep/audit tests).

Directory additions:
```
backend/app/integrations/crypto/
├── chains/{bitcoin_electrum.py, bitcoin_core_rpc.py, evm_rpc.py, base.py}
├── exchanges/{ccxt_adapter.py, coinbase.py, csv_importers/}
├── prices.py
├── transfers.py          # exchange↔wallet matching
└── lots.py               # basis engine (FIFO / specific ID, per wallet)
web/src/features/crypto/
├── signers/{Signer.ts, trezor.ts, ledger.ts, bitbox.ts, jade.ts, qrUR.ts, qrBBQr.ts, filePsbt.ts}
├── PsbtReview.tsx
└── WalletOnboarding.tsx
```

---

## 19. Public website (GitHub Pages) + blockchain stance

### 19.1 Marketing/docs site on GitHub Pages
- Separate repo or `/site` folder, built with **Astro** (static, fast, good SEO) or **Starlight** (Astro's docs theme) and deployed by a GitHub Actions workflow to Pages on your own domain (e.g. `holdfast.app`; the private app stays on `money.yourdomain.com` behind Tailscale).
- Pages: home, features, comparison pages (vs Monarch, YNAB, Actual, Firefly, Sure, rotki: these rank well in search), install guide (Docker one-liner, Umbrel/Unraid/CasaOS), docs, changelog (generated from GitHub Releases), privacy/security page, blog.
- SEO: sitemap, Open Graph images, JSON-LD `SoftwareApplication` schema, fast Lighthouse scores, one comparison or how-to post per feature.
- A **static demo**: the React app built in demo mode with seeded fake data, no backend. Let people click around before installing.
- Downloads: GitHub Releases + container images on GHCR, with **signed images (Sigstore/cosign)** and checksums.
- **Limit:** GitHub's terms say Pages isn't for running an online business or commercial SaaS, and shouldn't be used for passwords or card numbers. Fine for the open-source project site and donation links. If a paid hosted tier launches, move the site (or at least signup/checkout) to Cloudflare Pages or Netlify, which allow commercial use. Soft limits: 1 GB site, 100 GB/month bandwidth.

### 19.2 Do we use blockchain/web3? Mostly no, on purpose
| Use | Verdict | Why |
|---|---|---|
| Storing any user financial data on-chain | **Never** | Public, permanent and un-deletable is the opposite of what a private finance app needs |
| A token, NFT, "points" or on-chain governance | **No** | Adds securities/regulatory risk and scares off the privacy audience; nothing it does needs a blockchain |
| Sign-in with a wallet (SIWE) | No | Passkeys are simpler and stronger for a self-hosted app |
| **Reading** blockchains for portfolio tracking | **Yes (Section 18)** | That's just a data source, like Plaid |
| **Self-custody signing** with hardware wallets | **Yes, self-host only (Section 18)** | The user's keys, the user's device |
| **OpenTimestamps proofs** for tax records and statements | **Optional, nice-to-have** | Hashes year-end tax packages and source documents and anchors only the hash in Bitcoin, so you can later prove a record existed unaltered at that date. No data goes on-chain, it's free, and it adds audit credibility |
| Accepting crypto donations/sponsorships | **Optional** | Self-hosted **BTCPay Server** (no middleman) next to GitHub Sponsors |
| Release integrity | Use **Sigstore/cosign + checksums**, not a blockchain | Industry standard for signed containers |

Net: blockchain is a feature for users who own crypto, not part of the architecture.

---

## 20. Market-driven amendments (Revision 1.1)

Source: `docs/research/Market_Research_Gap_Analysis_2026-11-18.pdf`. These changes fold the research's "Changes to make in PLAN.md" into the phases. Where this section conflicts with an earlier one, this section wins.

### 20.1 Positioning
"The open-source Monarch you run yourself." The self-hosted all-in-one (budgets + investments + net worth + crypto) with working US bank sync. Beachhead: self-hosters and homelab users. Second wedge: public-sector employees (pension, 457(b), PSLF).

**Scope change from Section 1:** the codebase is built for **any** self-hosting household, not one specific household. Personal facts (state, institutions, names) live in household settings and `.env`, never in code (Section 17.4 applies from day one). "Multi-tenant SaaS" stays a non-goal for now, but row-level security is built in (20.3).

### 20.2 Five differentiators (lead with these in UI and site copy)
1. One self-hosted app for budgets, investments and net worth, with real US bank sync (Plaid with your own free keys, or SimpleFIN).
2. Sync health you can see: per-account status, stale-data warnings, one-click relink, automatic duplicate cleanup.
3. Import your whole history: statement PDFs reconciled to the penny, plus importers for Mint, YNAB, Monarch and Copilot exports.
4. Private AI: Claude, a local Ollama model, or any OpenAI-compatible endpoint, with redaction and visible tool calls.
5. Honest forecasts: P10/P50/P90 bands with a published backtest error, free rather than a premium tier.

### 20.3 Phase changes
| Phase | Change |
|---|---|
| 1 (+5) | **Per-person account visibility** (`private`, `shared`, `summary_only`) enforced with **Postgres row-level security** keyed on `household_id` and the viewing person. Add `account.visibility` and RLS policies in the Phase 1 migration. |
| 2 | Add a **SimpleFIN connector** next to Plaid, behind a common `BankConnector` interface (`link`, `sync`, `relink`, `health`). Add a **Sync Health page**: per-account status, last success, stale warnings (> 48h), and duplicate cleanup after relink. |
| 2 | **Bring-your-own-keys** config abstraction (Section 17.4) is built here, not later. |
| 3 | **Competitor-export importers** (Mint, YNAB, Monarch, Copilot CSV) run through the same dedupe and reconcile pipeline. |
| 3+ | **Plugin hooks** for importers and bank connectors (entry points), so the community can add banks and countries. |
| 5 | **Irregular-income mode**: pay-yourself salary, buffer month, estimated-tax set-aside jar. |
| 7 → core | The **HSA shoebox vault** (medical tag + receipt vault + "reimbursable balance") moves out of `FEATURE_TAX` and becomes core, so every user gets it. |
| 8 | **AI provider abstraction**: `LLMProvider` interface with `anthropic`, `ollama` and `openai_compatible` implementations. Default model routing stays Section 10.1 for the Anthropic provider. Redaction (10.4) runs before **every** provider, including local ones (defense in depth). |
| 8 | A read-only **MCP server** exposing the same whitelisted agent tools (10.3), so users can bring their own client. |
| New, optional | **Public-sector benefits pack** (`FEATURE_PUBLIC_SECTOR`): pension formula modeling, 457(b) limits alongside 401(k)/403(b) in the tax config, and PSLF payment tracking. |

### 20.4 Env additions
```
AI_PROVIDER=anthropic              # anthropic | ollama | openai_compatible
OLLAMA_BASE_URL=
OPENAI_COMPAT_BASE_URL=
OPENAI_COMPAT_API_KEY=
SIMPLEFIN_ACCESS_URL=              # Fernet-encrypted when stored in DB
FEATURE_TAX=false
FEATURE_CRYPTO_SPEND=false
FEATURE_PUBLIC_SECTOR=false
```

### 20.5 Pricing (only if hosted later)
Self-host: free forever under AGPL with your own Plaid/SimpleFIN and AI keys. Hosted: about $5–7/month, undercutting the $95–109/yr premium apps, with AI on bring-your-own-key or a metered add-on. Confirm with real Plaid and AI costs (Section 17.5).

### 20.6 Two brands (ADR 0009, 2026-11-18)
Direction 01 stays the brand for self-host / open source (README, docs, OSS site, default app theme). **Holo** (brand PDF pages 9–12) is the brand for **Holdfast Cloud**, the paid hosted tier (Section 17), and for marketing aimed at less technical people. This amends ADR 0003 and the single-brand language in Section 11. The app ships both as whole themes (`data-brand="signal" | "holo"`, light + dark each), chosen by edition (`VITE_EDITION`) and selectable in Settings → Appearance. Components use semantic tokens only. Work items: (1) theme plumbing + Holo tokens + fonts + AA test for both themes, a small web task right after Phase 1; (2) the Cloud marketing site on Cloudflare Pages, built when the hosted tier is scheduled (17.6). Cloud copy must be truthful for hosting: no "on your server", watch-only crypto, no tax module.
