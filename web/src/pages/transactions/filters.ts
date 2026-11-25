import { z } from 'zod'

import type { TransactionFilters } from '@/api/ledger'
import { parseMoney } from '@/lib/money'

/*
 * Filter state lives in the URL (`/transactions?account=…&from=…`), so a view survives a reload
 * and can be shared. These helpers are the only place that knows the parameter names.
 *
 * Amounts stay strings. `min`/`max` are signed, like the API: outflow is negative, so "-200" to
 * "-50" finds spend between $50 and $200.
 */

export const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SOURCES: string[] = ['user', 'rule', 'plaid', 'ml']
export const AMOUNT_PATTERN = /^-?\d{1,12}(\.\d{1,2})?$/

export interface ViewFilters {
  accounts: string[]
  showHidden: boolean
  q: string
  from: string
  to: string
  direction: '' | 'inflow' | 'outflow'
  min: string
  max: string
  hideTransfers: boolean
  onlyTransfers: boolean
  /** One category (or group) id; '' for any. */
  category: string
  source: '' | 'user' | 'rule' | 'plaid' | 'ml'
  /** The "needs category" view. It excludes the category and source filters (the API refuses both). */
  uncategorized: boolean
}

export const EMPTY_FILTERS: ViewFilters = {
  accounts: [],
  showHidden: false,
  q: '',
  from: '',
  to: '',
  direction: '',
  min: '',
  max: '',
  hideTransfers: false,
  onlyTransfers: false,
  category: '',
  source: '',
  uncategorized: false,
}

export function readFilters(params: URLSearchParams): ViewFilters {
  const date = (key: string) => {
    const value = params.get(key) ?? ''
    return DATE_PATTERN.test(value) ? value : ''
  }
  const amount = (key: string) => {
    const value = params.get(key) ?? ''
    return AMOUNT_PATTERN.test(value) ? value : ''
  }
  const dir = params.get('dir')
  const src = params.get('source') ?? ''
  const category = params.get('category') ?? ''
  return {
    accounts: params.getAll('account'),
    showHidden: params.get('hidden') === '1',
    q: params.get('q') ?? '',
    from: date('from'),
    to: date('to'),
    direction: dir === 'inflow' || dir === 'outflow' ? dir : '',
    min: amount('min'),
    max: amount('max'),
    hideTransfers: params.get('transfers') === 'hide',
    onlyTransfers: params.get('transfers') === 'only',
    category: UUID_PATTERN.test(category) ? category : '',
    source: SOURCES.includes(src) ? (src as ViewFilters['source']) : '',
    uncategorized: params.get('uncategorized') === '1',
  }
}

export function writeFilters(filters: ViewFilters): URLSearchParams {
  const params = new URLSearchParams()
  for (const id of filters.accounts) params.append('account', id)
  if (filters.showHidden) params.set('hidden', '1')
  if (filters.q.trim()) params.set('q', filters.q.trim())
  if (filters.from) params.set('from', filters.from)
  if (filters.to) params.set('to', filters.to)
  if (filters.direction) params.set('dir', filters.direction)
  if (filters.min) params.set('min', filters.min)
  if (filters.max) params.set('max', filters.max)
  if (filters.hideTransfers) params.set('transfers', 'hide')
  else if (filters.onlyTransfers) params.set('transfers', 'only')
  if (filters.uncategorized) params.set('uncategorized', '1')
  else {
    if (filters.category) params.set('category', filters.category)
    if (filters.source) params.set('source', filters.source)
  }
  return params
}

/** How many filters besides the account selection are active (the mobile "Filters" badge). */
export function activeFilterCount(f: ViewFilters): number {
  return [
    f.q.trim(),
    f.from,
    f.to,
    f.direction,
    f.min,
    f.max,
    f.hideTransfers || f.onlyTransfers,
    f.category,
    f.source,
    f.uncategorized,
  ].filter(Boolean).length
}

/**
 * Filters as the API wants them. With no accounts picked the view means "all visible accounts":
 * the API has no hidden flag, so when some accounts are hidden we send the visible ids.
 */
export function toApiFilters(
  f: ViewFilters,
  defaultAccountIds: string[] | null,
  /** The ids a chosen category stands for (itself plus its children). */
  categoryIds: string[] = [],
): TransactionFilters {
  const ids = f.accounts.length > 0 ? f.accounts : (defaultAccountIds ?? [])
  return {
    ...(ids.length > 0 && { account_id: ids }),
    ...(f.q.trim() && { q: f.q.trim() }),
    ...(f.from && { date_from: f.from }),
    ...(f.to && { date_to: f.to }),
    ...(f.direction && { direction: f.direction }),
    ...(f.min && { min_amount: f.min }),
    ...(f.max && { max_amount: f.max }),
    ...(f.hideTransfers && { exclude_transfers: true }),
    ...(f.onlyTransfers && !f.hideTransfers && { transfer: 'only' as const }),
    ...(f.uncategorized && { uncategorized: true }),
    ...(!f.uncategorized && categoryIds.length > 0 && { category_id: categoryIds }),
    ...(!f.uncategorized && f.source && { category_source: f.source }),
  }
}

const amountField = z
  .string()
  .trim()
  .refine((v) => v === '' || AMOUNT_PATTERN.test(v), 'Use a plain amount like 12.50 or -40')

/** The filter form (everything except search and accounts, which have their own controls). */
export const filterFormSchema = z
  .object({
    from: z.string(),
    to: z.string(),
    direction: z.enum(['', 'inflow', 'outflow']),
    min: amountField,
    max: amountField,
    hideTransfers: z.boolean(),
    onlyTransfers: z.boolean(),
    category: z.string(),
    source: z.enum(['', 'user', 'rule', 'plaid', 'ml']),
  })
  .refine((v) => !(v.hideTransfers && v.onlyTransfers), {
    path: ['onlyTransfers'],
    message: 'Pick hide transfers or only transfers, not both',
  })
  .refine((v) => !v.from || !v.to || v.from <= v.to, {
    path: ['to'],
    message: 'End date is before the start date',
  })
  .refine(
    (v) => {
      if (!v.min || !v.max || !AMOUNT_PATTERN.test(v.min) || !AMOUNT_PATTERN.test(v.max))
        return true
      return parseMoney(v.min).lte(parseMoney(v.max))
    },
    { path: ['max'], message: 'Max is below min' },
  )

export type FilterFormValues = z.infer<typeof filterFormSchema>
