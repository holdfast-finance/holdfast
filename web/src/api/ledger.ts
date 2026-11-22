import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'

import { api, readData } from '@/api/client'
import type { components, operations } from '@/api/schema'

type Schemas = components['schemas']

export type Account = Schemas['AccountOut']
export type AccountType = Account['type']
export type Transaction = Schemas['TransactionOut']
export type TransactionPage = Schemas['TransactionPage']
export type CategoryNode = Schemas['CategoryNode']
export type CategorySource = NonNullable<Transaction['category_source']>
export type CurrencyTotals = Schemas['CurrencyTotals']

type ListQuery = NonNullable<
  operations['list_transactions_api_transactions_get']['parameters']['query']
>
/** Everything the list and the summary share. Paging (`limit`, `cursor`) is added by the hook. */
export type TransactionFilters = Omit<ListQuery, 'limit' | 'cursor'>

export const PAGE_SIZE = 50

export const ledgerKeys = {
  accounts: ['ledger', 'accounts'] as const,
  categories: ['ledger', 'categories'] as const,
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

/** The household's category tree: groups with their children. Changes rarely. */
export function useCategories() {
  return useQuery({
    queryKey: ledgerKeys.categories,
    queryFn: ({ signal }) => readData(api.GET('/api/categories', { signal })),
    select: (data) => data.items,
    staleTime: 5 * 60_000,
  })
}

type Snapshot = [readonly unknown[], InfiniteData<TransactionPage> | undefined][]

/**
 * Optimistic edit of one row in every cached transactions list. On error the snapshots go back
 * exactly as they were; either way the lists and summaries refetch so the server has the last say.
 */
function useCategoryMutation<V extends { id: string }>(
  send: (vars: V) => Promise<Transaction>,
  patch: (vars: V) => (t: Transaction) => Transaction,
) {
  const client = useQueryClient()
  const listKey = ['ledger', 'transactions'] as const
  return useMutation<Transaction, Error, V, { snapshots: Snapshot }>({
    mutationFn: send,
    onMutate: async (vars) => {
      await client.cancelQueries({ queryKey: listKey })
      const snapshots = client.getQueriesData<InfiniteData<TransactionPage>>({ queryKey: listKey })
      const apply = patch(vars)
      client.setQueriesData<InfiniteData<TransactionPage>>({ queryKey: listKey }, (data) =>
        data
          ? {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                items: page.items.map((t) => (t.id === vars.id ? apply(t) : t)),
              })),
            }
          : data,
      )
      return { snapshots }
    },
    onError: (_error, _vars, context) => {
      for (const [key, data] of context?.snapshots ?? []) client.setQueryData(key, data)
    },
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: listKey })
      await client.invalidateQueries({ queryKey: ['ledger', 'summary'] })
    },
  })
}

export function useSetCategory() {
  return useCategoryMutation<{ id: string; categoryId: string }>(
    ({ id, categoryId }) =>
      readData(
        api.PUT('/api/transactions/{transaction_id}/category', {
          params: { path: { transaction_id: id } },
          body: { category_id: categoryId },
        }),
      ),
    ({ categoryId }) =>
      (t) => ({ ...t, category_id: categoryId, category_source: 'user' }),
  )
}

/** Hand a row back to automation: the category stays until a rule or the bank replaces it. */
export function useReleaseCategory() {
  return useCategoryMutation<{ id: string }>(
    ({ id }) =>
      readData(
        api.POST('/api/transactions/{transaction_id}/category/release', {
          params: { path: { transaction_id: id } },
        }),
      ),
    () => (t) => ({ ...t, category_source: null }),
  )
}
