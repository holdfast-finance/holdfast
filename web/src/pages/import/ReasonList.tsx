import Decimal from 'decimal.js'

import type { ReconcileNumbers } from '@/api/imports'
import { reasonLines } from './format'

function derivedReason(numbers: ReconcileNumbers): string | null {
  if (numbers.opening_balance === null || numbers.closing_balance === null) return 'missing_balance'
  if (numbers.balance_diff !== null && !new Decimal(numbers.balance_diff).isZero()) {
    return 'balance_mismatch'
  }
  return null
}

export function ReasonList({
  reason,
  numbers,
}: {
  reason: string | null | undefined
  numbers: ReconcileNumbers
}) {
  const fromList = reason === undefined
  const lines = reasonLines(fromList ? derivedReason(numbers) : reason)
  if (lines.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {fromList ? "This list doesn't carry the reason." : 'No reason in the import response.'}
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-1 text-sm text-warning-text">
      {lines.map((line) => (
        <li key={line}>
          {fromList && 'From the figures: '}
          {line}
        </li>
      ))}
    </ul>
  )
}
