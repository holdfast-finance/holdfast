import { Landmark, ListFilter, Search, Tags, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { errorMessage } from '@/api/client'
import {
  useAccounts,
  useCategories,
  useReleaseCategory,
  useSetCategory,
  useTransactionSummary,
  useTransactions,
  type Transaction,
} from '@/api/ledger'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useIsWide } from '@/hooks/useBreakpoint'
import { AccountsPanel, AccountsSkeleton } from './AccountsPanel'
import { indexCategories, withDescendants } from './categories'
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
import { TransactionSheet } from './TransactionSheet'
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
  const categoriesQuery = useCategories()
  const tree = useMemo(() => categoriesQuery.data ?? [], [categoriesQuery.data])
  const categoryIndex = useMemo(() => indexCategories(tree), [tree])
  const categoryIds = filters.category ? withDescendants(tree, filters.category) : []
  const apiFilters = toApiFilters({ ...filters, accounts: selected }, defaultIds, categoryIds)

  const ready =
    accountsQuery.isSuccess &&
    accounts.length > 0 &&
    (!filters.category || categoriesQuery.isSuccess)
  const list = useTransactions(apiFilters, ready)
  const summary = useTransactionSummary(apiFilters, ready)
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data])

  const setCategory = useSetCategory()
  const releaseCategory = useReleaseCategory()
  const [openId, setOpenId] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const open = items.find((t) => t.id === openId) ?? null
  const [announcement, setAnnouncement] = useState('')
  // The row that opened the sheet, so focus can go back to it (Radix only does that for a Trigger).
  const openerRef = useRef<HTMLElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const failed = (message: string) => (error: Error) => {
    setSaveError(`${message} It's back how it was. ${errorMessage(error)}`)
  }
  const onSetCategory = (t: Transaction, categoryId: string) => {
    setSaveError(null)
    setOpenId(null)
    setCategory.mutate(
      { id: t.id, categoryId },
      {
        onSuccess: () => {
          setAnnouncement(
            `${categoryIndex.get(categoryId)?.name ?? 'Category'} set for ${t.description}`,
          )
        },
        onError: failed(`Couldn't save the category for ${t.description}.`),
      },
    )
  }
  const onRelease = (t: Transaction) => {
    setSaveError(null)
    setOpenId(null)
    releaseCategory.mutate(
      { id: t.id },
      {
        onSuccess: () => {
          setAnnouncement(`${t.description} handed back to automation`)
        },
        onError: failed(`Couldn't hand ${t.description} back to automation.`),
      },
    )
  }
  const restoreFocus = (event: Event) => {
    event.preventDefault()
    const opener = openerRef.current
    if (opener?.isConnected) opener.focus()
    else listRef.current?.focus()
  }
  const toggleUncategorized = () => {
    update({ uncategorized: !filters.uncategorized, category: '', source: '' })
    setResetCount((n) => n + 1)
  }

  const filterCount = activeFilterCount(filters)
  // The Needs category toggle is its own button, so the Filters badge doesn't count it.
  const badgeCount = filterCount - (filters.uncategorized ? 1 : 0)
  const anyFilter = filterCount > 0 || selected.length > 0 || filters.showHidden
  const clearAll = () => {
    setParams(new URLSearchParams(), { replace: true })
    setResetCount((n) => n + 1)
  }
  const applyForm = (values: FilterFormValues) => {
    // Picking a category or source means leaving the needs-category view, which can't combine
    // with them (the API refuses both), so turn it off instead of silently ignoring the pick.
    update(values.category || values.source ? { ...values, uncategorized: false } : values)
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
  const filtersForm = (
    <FiltersForm key={resetCount} filters={filters} categories={tree} onApply={applyForm} />
  )

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
        <TransactionList
          items={items}
          accounts={accounts}
          categories={categoryIndex}
          onOpen={(t, opener) => {
            openerRef.current = opener
            setOpenId(t.id)
          }}
        />
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
                  {badgeCount > 0 && <Badge variant="secondary">{badgeCount}</Badge>}
                </Button>
              </>
            )}
            <Button
              variant={filters.uncategorized ? 'default' : 'outline'}
              aria-pressed={filters.uncategorized}
              onClick={toggleUncategorized}
            >
              <Tags aria-hidden="true" />
              Needs category
            </Button>
            {anyFilter && (
              <Button variant="ghost" onClick={clearAll}>
                <X aria-hidden="true" />
                Clear all
              </Button>
            )}
          </div>

          {categoriesQuery.isError && (
            <div
              role="alert"
              className="flex items-center justify-between gap-3 rounded-card border border-destructive/50 p-3 text-sm text-destructive-text"
            >
              <p>Couldn't load categories. You can't pick one until they load.</p>
              <Button variant="outline" onClick={() => void categoriesQuery.refetch()}>
                Retry
              </Button>
            </div>
          )}

          <div role="status" aria-live="polite" className="sr-only">
            {announcement}
          </div>

          {saveError && (
            <div
              role="alert"
              className="sticky top-[calc(3rem+env(safe-area-inset-top))] z-20 flex items-start justify-between gap-3 rounded-card border border-destructive/50 bg-background p-3 text-sm text-destructive-text lg:static"
            >
              <p>{saveError}</p>
              <Button
                variant="ghost"
                size="sm"
                aria-label="Dismiss message"
                onClick={() => {
                  setSaveError(null)
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          )}

          {ready && (
            <SummaryStrip
              totals={summary.data}
              loading={summary.isPending}
              failed={summary.isError}
              hideTransfers={filters.hideTransfers}
            />
          )}

          <div
            ref={listRef}
            tabIndex={-1}
            role="region"
            aria-label="Transaction list"
            className="rounded-card border border-border bg-card focus:outline-none"
          >
            {body}
          </div>
        </div>
      </div>

      <TransactionSheet
        transaction={open}
        account={open ? accounts.find((a) => a.id === open.account_id) : undefined}
        categories={tree}
        onClose={() => {
          setOpenId(null)
        }}
        onSetCategory={onSetCategory}
        onRelease={onRelease}
        onCloseAutoFocus={restoreFocus}
      />

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
      <Button asChild size="lg" className="mt-2 self-start">
        <Link to="/import">Import a statement</Link>
      </Button>
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
