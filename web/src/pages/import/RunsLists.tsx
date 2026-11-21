import { useRef, type RefObject } from 'react'

import type { ImportRun } from '@/api/imports'
import { errorMessage } from '@/api/client'
import { useImportRuns } from '@/api/imports'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ReconcileBadge, ReconcileFacts } from './Facts'
import { AcceptButton } from './AcceptSheet'
import { ReasonList } from './ReasonList'
import { formatPeriod, formatWhen } from './format'

function runLabel(run: ImportRun): string {
  return run.account_name ?? run.filename
}

/** A statement that landed but did not reconcile: why, and the numbers behind it. */
function ReviewItem({
  run,
  fallbackFocus,
  onAccepted,
}: {
  run: ImportRun
  fallbackFocus: RefObject<HTMLHeadingElement | null>
  onAccepted: (runId: string) => void
}) {
  return (
    <li className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium">{runLabel(run)}</p>
          <p className="truncate text-xs text-muted-foreground">
            {formatPeriod(run.period_start, run.period_end)} · {run.filename}
          </p>
        </div>
        <ReconcileBadge status={run.reconcile_status} />
      </div>
      <ReasonList reason={run.reason} numbers={run} fromRun />
      <ReconcileFacts numbers={run} currency={run.currency} />
      <AcceptButton
        runId={run.id}
        label={runLabel(run)}
        numbers={run}
        currency={run.currency}
        reason={run.reason}
        fromRun
        fallbackFocus={fallbackFocus}
        onAccepted={onAccepted}
      />
    </li>
  )
}

function RunItem({ run }: { run: ImportRun }) {
  const failed = run.status === 'failed'
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{run.filename}</p>
          <p className="truncate text-xs text-muted-foreground">
            {runLabel(run)} · {formatPeriod(run.period_start, run.period_end)}
          </p>
        </div>
        {failed ? (
          <span className="inline-flex items-center rounded-full bg-destructive/12 px-2.5 py-1 text-xs font-medium whitespace-nowrap text-destructive-text">
            Failed
          </span>
        ) : (
          <ReconcileBadge status={run.reconcile_status} />
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {formatWhen(run.created_at)}
        {run.reviewed_at ? ` · accepted ${formatWhen(run.reviewed_at)}` : ''}
        {failed
          ? ` · ${run.error_class ?? 'Error'}, nothing was added`
          : ` · ${String(run.rows_inserted)} added, ${String(run.rows_duplicate)} duplicate of ${String(run.rows_parsed)} rows`}
      </p>
    </li>
  )
}

function ListSkeleton() {
  return (
    <div role="status" aria-busy="true" className="flex flex-col gap-px">
      <span className="sr-only">Loading imports</span>
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} aria-hidden="true" className="flex flex-col gap-2 px-4 py-3">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}

export function RunsSections({ onAccepted }: { onAccepted: (runId: string) => void }) {
  const runs = useImportRuns()
  const reviewHeading = useRef<HTMLHeadingElement>(null)

  if (runs.isPending) {
    return (
      <div className="rounded-card border border-border bg-card">
        <ListSkeleton />
      </div>
    )
  }
  if (runs.isError && !runs.isFetchNextPageError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-card border border-border bg-card p-4"
      >
        <p className="font-medium text-destructive-text">Couldn't load your imports.</p>
        <p className="text-sm text-muted-foreground">{errorMessage(runs.error)}</p>
        <Button variant="outline" size="lg" onClick={() => void runs.refetch()}>
          Retry
        </Button>
      </div>
    )
  }

  const all = runs.data.pages.flatMap((p) => p.items)
  const review = all.filter((r) => r.reconcile_status === 'needs_review')
  const allReconciled =
    !runs.hasNextPage &&
    all.length > 0 &&
    all.every((run) => run.status === 'succeeded' && run.reconcile_status === 'reconciled')
  const emptyReviewMessage = runs.hasNextPage
    ? 'Nothing needs review in the imports loaded so far.'
    : allReconciled
      ? 'Every imported statement reconciled.'
      : 'Nothing needs review.'
  const pagination = runs.hasNextPage && (
    <Button
      variant="outline"
      size="lg"
      disabled={runs.isFetchingNextPage}
      onClick={() => void runs.fetchNextPage()}
    >
      {runs.isFetchingNextPage ? 'Loading…' : runs.isFetchNextPageError ? 'Retry' : 'Load more'}
    </Button>
  )
  return (
    <>
      <section aria-labelledby="review-title" className="flex flex-col gap-2">
        <h2
          id="review-title"
          ref={reviewHeading}
          tabIndex={-1}
          aria-describedby={runs.hasNextPage ? 'review-scope' : undefined}
          className="font-heading text-lg font-semibold"
        >
          Needs review{' '}
          {review.length > 0 && <span className="money text-base">({review.length})</span>}
        </h2>
        {runs.hasNextPage && (
          <p id="review-scope" className="text-xs text-muted-foreground">
            This queue covers loaded pages only.
          </p>
        )}
        {review.length === 0 ? (
          <p className="rounded-card border border-border bg-card p-4 text-sm text-muted-foreground">
            {emptyReviewMessage}
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-card border border-border bg-card">
            {review.map((r) => (
              <ReviewItem
                key={r.id}
                run={r}
                fallbackFocus={reviewHeading}
                onAccepted={onAccepted}
              />
            ))}
          </ul>
        )}
        {runs.isFetchNextPageError && (
          <div role="alert">
            <p className="text-sm font-medium text-destructive-text">Couldn't load more.</p>
            <p className="text-sm text-muted-foreground">{errorMessage(runs.error)}</p>
          </div>
        )}
        {pagination}
      </section>

      <section aria-labelledby="runs-title" className="flex flex-col gap-2">
        <h2 id="runs-title" className="font-heading text-lg font-semibold">
          Recent imports
        </h2>
        {all.length === 0 ? (
          <p className="rounded-card border border-border bg-card p-4 text-sm text-muted-foreground">
            No imports yet. Choose a statement PDF above.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-card border border-border bg-card">
            {all.map((r) => (
              <RunItem key={r.id} run={r} />
            ))}
          </ul>
        )}
        {pagination}
      </section>
    </>
  )
}
