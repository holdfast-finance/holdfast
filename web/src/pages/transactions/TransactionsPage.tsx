import { Landmark, ListFilter, Search, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import { errorMessage } from '@/api/client'
import { useAccounts, useTransactionSummary, useTransactions } from '@/api/ledger'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useIsWide } from '@/hooks/useBreakpoint'
import { AccountsPanel, AccountsSkeleton } from './AccountsPanel'
import { FiltersForm } from './FiltersForm'
import {
  activeFilterCount,
  readFilters,
  toApiFilters,
  writeFilters,
  type FilterFormValues,
  type ViewFilters,
} from './filters'
import { SummaryStrip } from './SummaryStrip'
import { ListSkeleton, TransactionList } from './TransactionList'

const SEARCH_DEBOUNCE_MS = 300

function SearchBox({ value, onCommit }: { value: string; onCommit: (q: string) => void }) {
  const [text, setText] = useState(value)
  useEffect(() => {
    if (text.trim() === value.trim()) return
    const timer = setTimeout(() => {
      onCommit(text)
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
    }
  }, [text, value, onCommit])
  return (
    <div className="relative min-w-full flex-1 min-[1200px]:min-w-0">
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        aria-label="Search descriptions"
        placeholder="Search descriptions"
        className="pl-9"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
        }}
      />
    </div>
  )
}

export function TransactionsPage() {
  const isWide = useIsWide()
  const [params, setParams] = useSearchParams()
  const filters = useMemo(() => readFilters(params), [params])
  // Bumped by "Clear all" so the uncontrolled search box and filter form restart from the URL.
  const [resetCount, setResetCount] = useState(0)
  const [sheet, setSheet] = useState<'accounts' | 'filters' | null>(null)

  const update = (patch: Partial<ViewFilters>) => {
    setParams(writeFilters({ ...filters, ...patch }), { replace: true })
  }
  const accountsQuery = useAccounts()
  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data])

  const known = new Set(accounts.map((a) => a.id))
  const selected = filters.accounts.filter((id) => known.has(id))
  const visibleIds = accounts.filter((a) => !a.is_hidden).map((a) => a.id)
  const hasHidden = visibleIds.length !== accounts.length
  const defaultIds = hasHidden && !filters.showHidden ? visibleIds : null
  const apiFilters = toApiFilters({ ...filters, accounts: selected }, defaultIds)

  const ready = accountsQuery.isSuccess && accounts.length > 0
  const list = useTransactions(apiFilters, ready)
  const summary = useTransactionSummary(apiFilters, ready)
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data])

  const filterCount = activeFilterCount(filters)
  const anyFilter = filterCount > 0 || selected.length > 0 || filters.showHidden
  const clearAll = () => {
    setParams(new URLSearchParams(), { replace: true })
    setResetCount((n) => n + 1)
  }
  const applyForm = (values: FilterFormValues) => {
    update(values)
  }

  const accountsPanel = (
    <AccountsPanel
      accounts={accounts}
      selected={selected}
      showHidden={filters.showHidden}
      onSelect={(ids) => {
        update({ accounts: ids })
      }}
      onShowHidden={(showHidden) => {
        update({ showHidden })
      }}
    />
  )
  const filtersForm = <FiltersForm key={resetCount} filters={filters} onApply={applyForm} />

  const loadMoreFailed = list.isFetchNextPageError
  let body
  if (accountsQuery.isPending) {
    body = <ListSkeleton />
  } else if (accountsQuery.isError) {
    body = <ErrorState error={accountsQuery.error} onRetry={() => void accountsQuery.refetch()} />
  } else if (accounts.length === 0) {
    body = <EmptyState />
  } else if (list.isPending) {
    body = <ListSkeleton />
  } else if (list.isError) {
    body = <ErrorState error={list.error} onRetry={() => void list.refetch()} />
  } else if (items.length === 0) {
    body = anyFilter ? (
      <div className="flex flex-col items-start gap-3 px-3 py-10">
        <p className="font-medium">No transactions match these filters.</p>
        <Button variant="outline" onClick={clearAll}>
          Clear all filters
        </Button>
      </div>
    ) : (
      <EmptyState />
    )
  } else {
    body = (
      <>
        <TransactionList items={items} accounts={accounts} />
        {list.hasNextPage && (
          <div className="flex justify-center py-6">
            <Button
              variant="outline"
              disabled={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
            >
              {list.isFetchingNextPage ? 'Loading…' : 'Load more'}
            </Button>
          </div>
        )}
        {loadMoreFailed && (
          <p role="alert" className="px-3 pb-4 text-sm text-destructive-text">
            Couldn't load more. Try Load more again.
          </p>
        )}
      </>
    )
  }

  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-6xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 id="page-title" className="text-3xl md:text-4xl">
          Transactions
        </h1>
        <p className="text-sm text-muted-foreground">Everything you have imported, newest first.</p>
      </header>

      <div className="grid gap-6 min-[1200px]:grid-cols-[17rem_minmax(0,1fr)] min-[1200px]:items-start">
        {isWide && (
          <aside
            aria-label="Accounts and filters"
            className="flex flex-col gap-6 rounded-card border border-border bg-card p-3"
          >
            <div>
              <h2 className="px-2 pb-2 text-sm font-semibold">Accounts</h2>
              {accountsQuery.isPending ? <AccountsSkeleton /> : accountsPanel}
            </div>
            <div className="px-2">
              <h2 className="pb-3 text-sm font-semibold">Filters</h2>
              {filtersForm}
            </div>
          </aside>
        )}

        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            <SearchBox
              key={resetCount}
              value={filters.q}
              onCommit={(q) => {
                update({ q })
              }}
            />
            {!isWide && (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSheet('accounts')
                  }}
                >
                  <Landmark aria-hidden="true" />
                  Accounts
                  <Badge variant="secondary">
                    {selected.length === 0 ? 'All' : selected.length}
                  </Badge>
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSheet('filters')
                  }}
                >
                  <ListFilter aria-hidden="true" />
                  Filters
                  {filterCount > 0 && <Badge variant="secondary">{filterCount}</Badge>}
                </Button>
              </>
            )}
            {anyFilter && (
              <Button variant="ghost" onClick={clearAll}>
                <X aria-hidden="true" />
                Clear all
              </Button>
            )}
          </div>

          {ready && (
            <SummaryStrip
              totals={summary.data}
              loading={summary.isPending}
              failed={summary.isError}
              hideTransfers={filters.hideTransfers}
            />
          )}

          <div className="rounded-card border border-border bg-card">{body}</div>
        </div>
      </div>

      {!isWide && (
        <>
          <ResponsiveSheet
            open={sheet === 'accounts'}
            onOpenChange={(open) => {
              setSheet(open ? 'accounts' : null)
            }}
            title="Accounts"
            description="Pick one, several or all. Hidden accounts stay out unless you show them."
          >
            {accountsPanel}
          </ResponsiveSheet>
          <ResponsiveSheet
            open={sheet === 'filters'}
            onOpenChange={(open) => {
              setSheet(open ? 'filters' : null)
            }}
            title="Filters"
          >
            {filtersForm}
            <Button
              variant="outline"
              onClick={() => {
                setSheet(null)
              }}
            >
              Done
            </Button>
          </ResponsiveSheet>
        </>
      )}
    </section>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col gap-2 px-4 py-12">
      <p className="font-medium">No transactions yet.</p>
      <p className="max-w-prose text-sm text-muted-foreground">
        Import a statement PDF and its transactions land here, newest first. Nothing leaves your
        server.
      </p>
    </div>
  )
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3 px-4 py-10">
      <p className="font-medium text-destructive-text">Couldn't load transactions.</p>
      <p className="text-sm text-muted-foreground">{errorMessage(error)}</p>
      <Button variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  )
}
