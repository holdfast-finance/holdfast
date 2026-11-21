import { CircleAlert, CircleCheck, CircleDashed, ShieldCheck } from 'lucide-react'

import type { ReconcileNumbers, ReconcileStatus } from '@/api/imports'
import { Money } from '@/components/Money'
import { cn } from '@/lib/utils'
import { RECONCILE_LABEL } from './format'

const TONE: Record<ReconcileStatus, string> = {
  reconciled: 'bg-success/12 text-success-text',
  accepted: 'bg-secondary/15 text-secondary-text',
  needs_review: 'bg-warning/12 text-warning-text',
  pending: 'bg-muted text-muted-foreground',
}
const ICON = {
  reconciled: CircleCheck,
  accepted: ShieldCheck,
  needs_review: CircleAlert,
  pending: CircleDashed,
} as const

/** Icon plus label, never colour alone. */
export function ReconcileBadge({ status }: { status: ReconcileStatus }) {
  const Icon = ICON[status]
  return (
    <span
      data-status={status}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap',
        TONE[status],
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {RECONCILE_LABEL[status]}
    </span>
  )
}

function Cell({
  label,
  value,
  currency,
}: {
  label: string
  value: string | null
  currency: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 sm:flex-col sm:items-start sm:gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">
        {value === null ? (
          <span className="text-muted-foreground">Not found</span>
        ) : (
          <Money amount={value} currency={currency} />
        )}
      </dd>
    </div>
  )
}

/** opening + Σ transactions = closing, with the computed difference. */
export function ReconcileFacts({
  numbers,
  currency,
}: {
  numbers: ReconcileNumbers
  currency: string
}) {
  return (
    <dl className="grid grid-cols-1 divide-y divide-border sm:grid-cols-4 sm:divide-x sm:divide-y-0 sm:[&>div]:px-3 sm:[&>div:first-child]:pl-0">
      <Cell label="Opening" value={numbers.opening_balance} currency={currency} />
      <Cell label="Transactions" value={numbers.txn_sum} currency={currency} />
      <Cell label="Closing" value={numbers.closing_balance} currency={currency} />
      <Cell label="Difference" value={numbers.balance_diff} currency={currency} />
    </dl>
  )
}
