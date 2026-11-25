import Decimal from 'decimal.js'

import type {
  Account,
  CategoryNode,
  CurrencyTotals,
  Transaction,
  TransactionFilters,
} from '@/api/ledger'
import { db } from '@/mocks/db'

/*
 * Invented accounts and transactions for the mock API. Every merchant, mask and amount is made
 * up (masks are 0000-style, institutions are "First Example Bank" and friends). Amounts are
 * strings and all arithmetic goes through decimal.js, same as the app.
 */

export const LEDGER_IDS = {
  checking: '00000000-0000-4000-8000-00000000a001',
  savings: '00000000-0000-4000-8000-00000000a002',
  card: '00000000-0000-4000-8000-00000000a003',
  euro: '00000000-0000-4000-8000-00000000a004',
  hidden: '00000000-0000-4000-8000-00000000a005',
} as const

const cat = (n: number) => `00000000-0000-4000-8000-0000000c${String(n).padStart(4, '0')}`

export const CATEGORY_IDS = {
  food: cat(1),
  groceries: cat(2),
  dining: cat(3),
  home: cat(4),
  hardware: cat(5),
  income: cat(6),
  paycheque: cat(7),
  fun: cat(8),
  hidden: cat(9),
} as const

const node = (
  id: string,
  slug: string,
  name: string,
  kind: CategoryNode['kind'],
  children: CategoryNode[] = [],
  is_hidden = false,
): CategoryNode => ({
  id,
  slug,
  name,
  kind,
  children,
  is_hidden,
  is_essential: false,
  counts_toward_spending: kind === 'expense',
  origin: 'system',
})

/** The mock category tree: two levels, one hidden category, invented names. */
export const MOCK_CATEGORIES: CategoryNode[] = [
  node(CATEGORY_IDS.food, 'food', 'Food', 'expense', [
    node(CATEGORY_IDS.groceries, 'groceries', 'Groceries', 'expense'),
    node(CATEGORY_IDS.dining, 'dining', 'Dining out', 'expense'),
  ]),
  node(CATEGORY_IDS.home, 'home', 'Home', 'expense', [
    node(CATEGORY_IDS.hardware, 'hardware', 'Hardware', 'expense'),
  ]),
  node(CATEGORY_IDS.income, 'income', 'Income', 'income', [
    node(CATEGORY_IDS.paycheque, 'paycheck', 'Paycheck', 'income'),
  ]),
  node(CATEGORY_IDS.fun, 'fun', 'Fun', 'expense'),
  node(CATEGORY_IDS.hidden, 'old', 'Retired category', 'expense', [], true),
]

const ACCOUNTS: Omit<
  Account,
  'transaction_count' | 'earliest_posted_date' | 'latest_posted_date'
>[] = [
  {
    id: LEDGER_IDS.checking,
    name: 'Everyday checking',
    type: 'depository',
    source: 'import',
    institution_name: 'First Example Bank',
    mask: '0000',
    currency: 'USD',
    visibility: 'shared',
    owner: 'me',
    owner_person_id: '00000000-0000-4000-8000-00000000b001',
    can_edit: true,
    can_change_owner: true,
    is_hidden: false,
    latest_balance: null,
    latest_balance_as_of: null,
  },
  {
    id: LEDGER_IDS.savings,
    name: 'Rainy day savings',
    type: 'depository',
    source: 'import',
    institution_name: 'First Example Bank',
    mask: '1111',
    currency: 'USD',
    visibility: 'shared',
    owner: 'me',
    owner_person_id: '00000000-0000-4000-8000-00000000b001',
    can_edit: true,
    can_change_owner: true,
    is_hidden: false,
    latest_balance: '2500.00',
    latest_balance_as_of: '2026-11-01',
  },
  {
    id: LEDGER_IDS.card,
    name: 'Sample travel card',
    type: 'credit',
    source: 'import',
    institution_name: 'Example Card Co',
    mask: '2222',
    currency: 'USD',
    visibility: 'shared',
    owner: 'me',
    owner_person_id: '00000000-0000-4000-8000-00000000b001',
    can_edit: true,
    can_change_owner: true,
    is_hidden: false,
    latest_balance: null,
    latest_balance_as_of: null,
  },
  {
    id: LEDGER_IDS.euro,
    name: 'Euro account',
    type: 'depository',
    source: 'manual',
    institution_name: null,
    mask: null,
    currency: 'EUR',
    visibility: 'private',
    owner: 'me',
    owner_person_id: '00000000-0000-4000-8000-00000000b001',
    can_edit: true,
    can_change_owner: true,
    is_hidden: false,
    latest_balance: null,
    latest_balance_as_of: null,
  },
  {
    id: LEDGER_IDS.hidden,
    name: 'Old closed account',
    type: 'other',
    source: 'import',
    institution_name: 'Retired Example Bank',
    mask: '9999',
    currency: 'USD',
    visibility: 'private',
    owner: 'me',
    owner_person_id: '00000000-0000-4000-8000-00000000b001',
    can_edit: true,
    can_change_owner: true,
    is_hidden: true,
    latest_balance: null,
    latest_balance_as_of: null,
  },
]

type Row = readonly [
  account: keyof typeof LEDGER_IDS,
  date: string,
  description: string,
  amount: string,
]

/** A handful of hand-written rows that exercise decimals, transfers, large values and EUR. */
const FIXED: Row[] = [
  ['checking', '2026-11-20', 'Harbor Grocers', '-0.10'],
  ['checking', '2026-11-20', 'Lantern Coffee', '-0.20'],
  ['checking', '2026-11-20', 'Payroll deposit Example Co', '3200.00'],
  ['checking', '2026-11-19', 'Transfer to Rainy day savings', '-500.00'],
  ['savings', '2026-11-19', 'Transfer from Everyday checking', '500.00'],
  ['card', '2026-11-18', 'Fernwood Bookshop', '-23.45'],
  ['card', '2026-11-18', 'Northline Airways', '-1234567.89'],
  ['euro', '2026-11-17', 'Café Lumière', '-7.80'],
  ['euro', '2026-11-17', 'Refund Atelier Nord', '12.30'],
  ['hidden', '2026-03-02', 'Final fee, closed account', '-5.00'],
]

const MERCHANTS = [
  'Harbor Grocers',
  'Lantern Coffee',
  'Fernwood Bookshop',
  'Quarry Hardware',
  'Paper Kite Cinema',
  'Tidewater Fuel',
  'Juniper Pharmacy',
  'Brightside Bakery',
]

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** Deterministic filler: `count` USD checking rows, one per day going back from 2026-11-16. */
function filler(count: number): Row[] {
  return Array.from({ length: count }, (_, i) => {
    const day = new Date(Date.UTC(2026, 10, 16 - i))
    const date = `${String(day.getUTCFullYear())}-${pad(day.getUTCMonth() + 1)}-${pad(day.getUTCDate())}`
    const cents = 500 + ((i * 737) % 9000)
    const amount = `-${String(Math.floor(cents / 100))}.${pad(cents % 100)}`
    return ['checking', date, MERCHANTS[i % MERCHANTS.length] ?? 'Corner Shop', amount] as Row
  })
}

/** A few rows start categorised (by each source) so the chips and filters have something to show. */
function seedCategory(description: string): Pick<Transaction, 'category_id' | 'category_source'> {
  switch (description) {
    case 'Payroll deposit Example Co':
      return { category_id: CATEGORY_IDS.paycheque, category_source: 'rule' }
    case 'Harbor Grocers':
      return { category_id: CATEGORY_IDS.groceries, category_source: 'user' }
    case 'Brightside Bakery':
      return { category_id: CATEGORY_IDS.dining, category_source: 'plaid' }
    default:
      return { category_id: null, category_source: null }
  }
}

export interface SeedLedgerOptions {
  /** Extra filler rows on the checking account (default 60, so the list needs a second page). */
  filler?: number
  /** Start with no accounts at all (a fresh instance that has imported nothing). */
  empty?: boolean
}

export function seedLedger({
  filler: fillerCount = 60,
  empty = false,
}: SeedLedgerOptions = {}): void {
  db.accounts = []
  db.transactions = []
  if (empty) return
  const rows = [...FIXED, ...filler(fillerCount)]
  db.transactions = rows.map(([account, date, description, amount], i) => {
    const owner = ACCOUNTS.find((a) => a.id === LEDGER_IDS[account])
    const isTransfer = description.startsWith('Transfer')
    return {
      id: `00000000-0000-4000-9000-${String(i + 1).padStart(12, '0')}`,
      account_id: LEDGER_IDS[account],
      posted_date: date,
      description,
      amount,
      currency: owner?.currency ?? 'USD',
      is_pending: description === 'Lantern Coffee' && date === '2026-11-20',
      is_transfer: isTransfer,
      import_reconcile_status: null,
      needs_review: description === 'Fernwood Bookshop',
      merchant_key: null,
      ...seedCategory(description),
      has_splits: false,
      // The travel card belongs to someone else in the household: read-only for this person.
      can_edit: account !== 'card',
    }
  })
  db.accounts = ACCOUNTS.map((a) => {
    const dates = db.transactions
      .filter((t) => t.account_id === a.id)
      .map((t) => t.posted_date)
      .sort()
    return {
      ...a,
      transaction_count: dates.length,
      earliest_posted_date: dates[0] ?? null,
      latest_posted_date: dates.at(-1) ?? null,
    }
  })
}

// --- Query semantics (mirror backend/app/services/ledger.py) -----------------------------------

export function filterFromParams(params: URLSearchParams): TransactionFilters {
  const direction = params.get('direction')
  return {
    account_id: params.getAll('account_id'),
    date_from: params.get('date_from'),
    date_to: params.get('date_to'),
    q: params.get('q'),
    direction: direction === 'inflow' || direction === 'outflow' ? direction : null,
    min_amount: params.get('min_amount'),
    max_amount: params.get('max_amount'),
    exclude_transfers: params.get('exclude_transfers') === 'true',
    category_id: params.getAll('category_id'),
    uncategorized: params.get('uncategorized') === 'true',
    category_source: params.get('category_source') as TransactionFilters['category_source'],
    transfer: params.get('transfer') as TransactionFilters['transfer'],
  }
}

export function matching(f: TransactionFilters): Transaction[] {
  const ids = f.account_id ?? []
  return db.transactions.filter((t) => {
    if (ids.length > 0 && !ids.includes(t.account_id)) return false
    if (f.date_from && t.posted_date < f.date_from) return false
    if (f.date_to && t.posted_date > f.date_to) return false
    if (f.q && !t.description.toLowerCase().includes(f.q.toLowerCase())) return false
    const amount = new Decimal(t.amount)
    if (f.direction === 'inflow' && !amount.isPositive()) return false
    if (f.direction === 'outflow' && !amount.isNegative()) return false
    if (f.min_amount && amount.lt(f.min_amount)) return false
    if (f.max_amount && amount.gt(f.max_amount)) return false
    if (f.exclude_transfers && t.is_transfer) return false
    if (f.transfer === 'only' && !t.is_transfer) return false
    if (f.transfer === 'exclude' && t.is_transfer) return false
    if (f.category_id && f.category_id.length > 0) {
      if (!t.category_id || !f.category_id.includes(t.category_id)) return false
    }
    if (f.uncategorized && t.category_id) return false
    if (f.category_source && t.category_source !== f.category_source) return false
    return true
  })
}

/** Newest first, ties broken by id (the real API's keyset order). */
export function newestFirst(rows: Transaction[]): Transaction[] {
  return [...rows].sort((a, b) =>
    a.posted_date === b.posted_date
      ? b.id.localeCompare(a.id)
      : b.posted_date.localeCompare(a.posted_date),
  )
}

export function encodeCursor(offset: number): string {
  return btoa(`mock-cursor:${String(offset)}`)
}

export function decodeCursor(cursor: string | null): number {
  if (!cursor) return 0
  const match = /^mock-cursor:(\d+)$/.exec(atob(cursor))
  return match?.[1] ? Number.parseInt(match[1], 10) : 0
}

/** One entry per currency, never summed across currencies. */
export function totalsByCurrency(rows: Transaction[]): CurrencyTotals[] {
  const byCurrency = new Map<string, { inflow: Decimal; outflow: Decimal; count: number }>()
  for (const t of rows) {
    const entry = byCurrency.get(t.currency) ?? {
      inflow: new Decimal(0),
      outflow: new Decimal(0),
      count: 0,
    }
    const amount = new Decimal(t.amount)
    if (amount.isNegative()) entry.outflow = entry.outflow.plus(amount)
    else entry.inflow = entry.inflow.plus(amount)
    entry.count += 1
    byCurrency.set(t.currency, entry)
  }
  return [...byCurrency.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, e]) => ({
      currency,
      inflow: e.inflow.toFixed(2),
      outflow: e.outflow.toFixed(2),
      net: e.inflow.plus(e.outflow).toFixed(2),
      count: e.count,
    }))
}

export function categoryExists(id: string): boolean {
  const walk = (nodes: CategoryNode[]): boolean =>
    nodes.some((n) => n.id === id || walk(n.children))
  return walk(MOCK_CATEGORIES)
}
