import { Info } from 'lucide-react'

import type { ImportBlock, ImportResult } from '@/api/imports'
import { ReconcileBadge, ReconcileFacts } from './Facts'
import { formatPeriod, reviewReason } from './format'

function Block({ block }: { block: ImportBlock }) {
  const flagged = block.reconcile_status === 'needs_review'
  return (
    <li className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium">{block.account_name}</p>
          <p className="text-xs text-muted-foreground">
            {formatPeriod(block.period_start, block.period_end)}
          </p>
        </div>
        <ReconcileBadge status={block.reconcile_status} />
      </div>
      <ReconcileFacts numbers={block} currency={block.currency} />
      {flagged && <p className="text-sm text-warning-text">{reviewReason(block)}</p>}
      <p className="text-xs text-muted-foreground">
        {block.created
          ? `${String(block.rows_inserted)} of ${String(block.rows_parsed)} rows added${
              block.rows_duplicate > 0 ? `, ${String(block.rows_duplicate)} already on file` : ''
            }.`
          : `${String(block.rows_duplicate)} rows already on file. Nothing was added.`}
      </p>
    </li>
  )
}

export function ResultView({ result }: { result: ImportResult }) {
  const already = result.blocks.length > 0 && result.blocks.every((b) => !b.created)
  const flagged = result.blocks.filter((b) => b.reconcile_status === 'needs_review').length
  let headline: string
  if (already) headline = 'Already imported'
  else if (flagged > 0) headline = 'Imported, needs review'
  else headline = 'Imported and reconciled'
  return (
    <section aria-label="Import result" aria-live="polite" className="flex flex-col gap-3">
      <div>
        <h2 className="font-heading text-base font-semibold">{headline}</h2>
        <p className="truncate text-xs text-muted-foreground">{result.filename}</p>
      </div>
      {already && (
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          This exact file is already in Holdfast. That is fine; your data is unchanged.
        </p>
      )}
      {result.balances_only && (
        <p className="text-sm text-muted-foreground">This file had balances but no transactions.</p>
      )}
      <ul className="flex flex-col gap-3">
        {result.blocks.map((b) => (
          <Block key={b.run_id} block={b} />
        ))}
      </ul>
    </section>
  )
}
