import { Info } from 'lucide-react'

import type { ImportBalance, ImportBlock, ImportResult } from '@/api/imports'
import { Money } from '@/components/Money'
import { AcceptButton } from './AcceptSheet'
import { ReconcileBadge, ReconcileFacts } from './Facts'
import { formatPeriod } from './format'
import { ReasonList } from './ReasonList'

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
      {flagged && (
        <>
          <ReasonList reason={block.reason} />
          <AcceptButton
            runId={block.run_id}
            label={block.account_name}
            numbers={block}
            currency={block.currency}
            reason={block.reason}
          />
        </>
      )}
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

function BalanceOnly({ balance }: { balance: ImportBalance }) {
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <p className="font-medium">
        {balance.account_last4 ? `Account ending ${balance.account_last4}` : 'Account'}
      </p>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Opening</dt>
          <dd>
            {balance.opening_balance === null ? (
              <span className="text-muted-foreground">Not found</span>
            ) : (
              <Money amount={balance.opening_balance} />
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Closing</dt>
          <dd>
            {balance.closing_balance === null ? (
              <span className="text-muted-foreground">Not found</span>
            ) : (
              <Money amount={balance.closing_balance} />
            )}
          </dd>
        </div>
      </dl>
    </li>
  )
}

export function ResultView({ result }: { result: ImportResult }) {
  const flagged = result.blocks.filter((b) => b.reconcile_status === 'needs_review').length
  let headline: string
  if (result.balances_only) headline = 'Balances only, needs review'
  else if (!result.created) headline = 'Already imported'
  else if (flagged > 0) headline = 'Imported, needs review'
  else headline = 'Imported and reconciled'
  return (
    <section aria-label="Import result" aria-live="polite" className="flex flex-col gap-3">
      <div>
        <h2 className="font-heading text-base font-semibold">{headline}</h2>
        <p className="truncate text-xs text-muted-foreground">{result.filename}</p>
      </div>
      {!result.created && !result.balances_only && (
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          This exact file is already in Holdfast. That is fine; your data is unchanged.
        </p>
      )}
      {result.balances_only && (
        <>
          <p className="text-sm text-muted-foreground">
            This statement had balances but no transactions. The file is saved and needs review. It
            does not appear in your imports list yet.
          </p>
          <ul className="flex flex-col gap-3">
            {result.balances.map((b, i) => (
              <BalanceOnly key={`${b.account_last4 ?? 'none'}-${String(i)}`} balance={b} />
            ))}
          </ul>
        </>
      )}
      <ul className="flex flex-col gap-3">
        {result.blocks.map((b) => (
          <Block key={b.run_id} block={b} />
        ))}
      </ul>
    </section>
  )
}
