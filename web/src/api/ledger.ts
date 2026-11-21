import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'

import { api, readData } from '@/api/client'
import type { components, operations } from '@/api/schema'

type Schemas = components['schemas']

export type Account = Schemas['AccountOut']
export type AccountType = Account['type']
export type Transaction = Schemas['TransactionOut']
export type CurrencyTotals = Schemas['CurrencyTotals']

type ListQuery = NonNullable<
  operations['list_transactions_api_transactions_get']['parameters']['query']
>
/** Everything the list and the summary share. Paging (`limit`, `cursor`) is added by the hook. */
export type TransactionFilters = Omit<ListQuery, 'limit' | 'cursor'>

export const PAGE_SIZE = 50

export const ledgerKeys = {
  accounts: ['ledger', 'accounts'] as const,
  transactions: (filters: TransactionFilters) => ['ledger', 'transactions', filters] as const,
  summary: (filters: TransactionFilters) => ['ledger', 'summary', filters] as const,
}

export function useAccounts() {
  return useQuery({
    queryKey: ledgerKeys.accounts,
    queryFn: ({ signal }) => readData(api.GET('/api/accounts', { signal })),
    select: (data) => data.items,
  })
}

/** Keyset pagination: the cursor is opaque, so pages are only ever fetched in order. */
export function useTransactions(filters: TransactionFilters, enabled = true) {
  return useInfiniteQuery({
    queryKey: ledgerKeys.transactions(filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      readData(
        api.GET('/api/transactions', {
          signal,
          params: { query: { ...filters, limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
    enabled,
  })
}

export function useTransactionSummary(filters: TransactionFilters, enabled = true) {
  return useQuery({
    queryKey: ledgerKeys.summary(filters),
    queryFn: ({ signal }) =>
      readData(api.GET('/api/transactions/summary', { signal, params: { query: filters } })),
    select: (data) => data.totals,
    placeholderData: keepPreviousData,
    enabled,
  })
}
