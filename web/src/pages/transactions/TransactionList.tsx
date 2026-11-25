import { ArrowDownLeft, ArrowUpRight } from 'lucide-react'

import type { Account, Transaction } from '@/api/ledger'
import type { CategoryInfo } from './categories'
import { Money } from '@/components/Money'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { parseMoney } from '@/lib/money'
import { formatDay } from './format'

function groupByDay(items: Transaction[]): { date: string; rows: Transaction[] }[] {
  const groups: { date: string; rows: Transaction[] }[] = []
  for (const t of items) {
    const last = groups.at(-1)
    if (last?.date === t.posted_date) last.rows.push(t)
    else groups.push({ date: t.posted_date, rows: [t] })
  }
  return groups
}

const neutralBadge = 'text-muted-foreground'
const warnBadge = 'border-warning/50 bg-transparent text-warning-text'

function CategoryChip({ t, info }: { t: Transaction; info: CategoryInfo | undefined }) {
  if (t.is_transfer)
    return (
      <Badge variant="outline" className={neutralBadge}>
        Transfer
      </Badge>
    )
  if (!t.category_id && !t.can_edit) {
    // Nothing the person can do about it, so no call to action.
    return (
      <Badge variant="outline" className={neutralBadge}>
        Uncategorised
      </Badge>
    )
  }
  if (!t.category_id) {
    return (
      <Badge variant="outline" className={warnBadge}>
        Needs category
      </Badge>
    )
  }
  const by = t.category_source === 'user' ? 'you' : t.category_source === 'rule' ? 'rule' : null
  return (
    <Badge variant="outline" className={neutralBadge} title={info?.group ?? undefined}>
      {info?.name ?? 'Category'}
      {by && (
        <>
          <span className="sr-only">, set by {by}</span>
          <span aria-hidden="true">· {by}</span>
        </>
      )}
    </Badge>
  )
}

function Row({
  t,
  account,
  info,
  onOpen,
}: {
  t: Transaction
  account: Account | undefined
  info: CategoryInfo | undefined
  onOpen: (t: Transaction, opener: HTMLElement) => void
}) {
  const inflow = parseMoney(t.amount).isPositive() && !parseMoney(t.amount).isZero()
  const outflow = parseMoney(t.amount).isNegative()
  return (
    <li className="border-b border-border">
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={(e) => {
          onOpen(t, e.currentTarget)
        }}
        className="grid min-h-14 w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 px-3 py-3 text-left hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:outline-none min-[1200px]:min-h-11 min-[1200px]:grid-cols-[minmax(8rem,1fr)_11rem_auto_9rem] min-[1200px]:items-center min-[1200px]:gap-y-0 min-[1200px]:py-2"
      >
        <span className="min-w-0 truncate text-sm font-medium">{t.description}</span>
        <span className="col-start-2 row-span-2 row-start-1 flex items-center justify-end gap-1 self-center text-right text-sm min-[1200px]:col-start-4 min-[1200px]:row-span-1 min-[1200px]:row-start-auto">
          {inflow && <ArrowDownLeft aria-hidden="true" className="size-3.5 text-success-text" />}
          {outflow && (
            <ArrowUpRight aria-hidden="true" className="size-3.5 text-muted-foreground" />
          )}
          <span className="sr-only">{inflow ? 'Inflow ' : outflow ? 'Outflow ' : ''}</span>
          <Money amount={t.amount} currency={t.currency} showPlus />
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 min-[1200px]:contents">
          <span className="min-w-0 truncate text-xs text-muted-foreground min-[1200px]:text-sm">
            {account
              ? `${account.name}${account.mask ? ` ··${account.mask}` : ''}`
              : 'Unknown account'}
          </span>
          <span className="flex flex-wrap gap-1 min-[1200px]:flex-nowrap min-[1200px]:whitespace-nowrap">
            {t.is_pending && (
              <Badge variant="outline" className={warnBadge}>
                Pending
              </Badge>
            )}
            <CategoryChip t={t} info={info} />
            {t.needs_review && (
              <Badge variant="outline" className={warnBadge}>
                Needs review
              </Badge>
            )}
          </span>
        </span>
      </button>
    </li>
  )
}

export function TransactionList({
  items,
  accounts,
  categories,
  onOpen,
}: {
  items: Transaction[]
  accounts: Account[]
  categories: Map<string, CategoryInfo>
  onOpen: (t: Transaction, opener: HTMLElement) => void
}) {
  const byId = new Map(accounts.map((a) => [a.id, a]))
  return (
    <div className="flex flex-col">
      {groupByDay(items).map((group) => {
        const headingId = `day-${group.date}`
        return (
          <section key={group.date} aria-labelledby={headingId}>
            <h2
              id={headingId}
              className="sticky top-[calc(3rem+env(safe-area-inset-top))] z-10 border-b border-border bg-background px-3 py-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase lg:top-0"
            >
              {formatDay(group.date)}
            </h2>
            <ul>
              {group.rows.map((t) => (
                <Row
                  key={t.id}
                  t={t}
                  account={byId.get(t.account_id)}
                  info={t.category_id ? categories.get(t.category_id) : undefined}
                  onOpen={onOpen}
                />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

export function ListSkeleton() {
  return (
    <div role="status" aria-busy="true" className="flex flex-col gap-px">
      <span className="sr-only">Loading transactions</span>
      {Array.from({ length: 8 }, (_, i) => (
        <div
          key={i}
          className="flex items-center justify-between gap-4 px-3 py-3"
          aria-hidden="true"
        >
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  )
}
