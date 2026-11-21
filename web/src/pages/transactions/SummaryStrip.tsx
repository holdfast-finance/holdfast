import { Money } from '@/components/Money'
import { Skeleton } from '@/components/ui/skeleton'
import type { CurrencyTotals } from '@/api/ledger'

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium md:text-base">{children}</dd>
    </div>
  )
}

/** One block per currency. Currencies are never added together. */
export function SummaryStrip({
  totals,
  loading,
  failed,
}: {
  totals: CurrencyTotals[] | undefined
  loading: boolean
  failed: boolean
}) {
  return (
    <section
      aria-label="Summary for current filters"
      className="flex flex-col gap-3 rounded-card border border-border bg-card p-4"
    >
      {loading && <Skeleton className="h-12 w-full" aria-hidden="true" />}
      {failed && (
        <p className="text-sm text-muted-foreground">
          Couldn't load the summary for these filters.
        </p>
      )}
      {totals?.length === 0 && (
        <p className="text-sm text-muted-foreground">No transactions match.</p>
      )}
      {totals?.map((t) => (
        <div
          key={t.currency}
          role="group"
          aria-label={`${t.currency} totals`}
          className="flex flex-col gap-2 border-border not-first:border-t not-first:pt-3"
        >
          <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {t.currency}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-4">
            <Stat label="Inflow">
              <Money amount={t.inflow} currency={t.currency} showPlus />
            </Stat>
            <Stat label="Outflow">
              <Money amount={t.outflow} currency={t.currency} />
            </Stat>
            <Stat label="Net">
              <Money amount={t.net} currency={t.currency} showPlus />
            </Stat>
            <Stat label="Transactions">
              <span className="money">{t.count}</span>
            </Stat>
          </dl>
        </div>
      ))}
    </section>
  )
}
