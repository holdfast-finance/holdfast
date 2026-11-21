import Decimal from 'decimal.js'

import type { ReconcileNumbers } from '@/api/imports'
import { reasonCopy } from './format'

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
  fromRun = false,
}: {
  reason: string | null
  numbers: ReconcileNumbers
  fromRun?: boolean
}) {
  const codes = (reason ?? '')
    .split(',')
    .map((code) => code.trim())
    .filter(Boolean)
  const fallback = () => {
    const derived = derivedReason(numbers)
    return `From the figures: ${(derived && reasonCopy(derived)) || 'No reason could be determined.'}`
  }
  const lines = new Set(
    codes.map((code) => reasonCopy(code) ?? (fromRun ? fallback() : `Flagged: ${code}`)),
  )
  if (fromRun && lines.size === 0) lines.add(fallback())
  if (lines.size === 0) {
    return <p className="text-sm text-muted-foreground">No reason in the import response.</p>
  }
  return (
    <ul className="flex flex-col gap-1 text-sm text-warning-text">
      {[...lines].map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  )
}
