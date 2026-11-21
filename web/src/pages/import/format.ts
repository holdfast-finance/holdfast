import Decimal from 'decimal.js'

import type { ReconcileNumbers, ReconcileStatus } from '@/api/imports'
import { parseMoney } from '@/lib/money'

const day = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

function fmt(date: string): string {
  return day.format(new Date(`${date}T00:00:00Z`))
}

/** "Oct 1 – Oct 31, 2026" style range, tolerant of a missing end. */
export function formatPeriod(start: string | null, end: string | null): string {
  if (start && end) return `${fmt(start)} to ${fmt(end)}`
  if (end) return `Ending ${fmt(end)}`
  if (start) return `From ${fmt(start)}`
  return 'Period not found'
}

export function formatWhen(iso: string): string {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(iso))
}

export const RECONCILE_LABEL: Record<ReconcileStatus, string> = {
  pending: 'Pending',
  reconciled: 'Reconciled',
  needs_review: 'Needs review',
  accepted: 'Accepted',
}

/**
 * Why a statement did not reconcile, in plain words. Only the three stored numbers are used, so
 * the explanation can never disagree with the figures shown beside it.
 */
export function reviewReason(n: ReconcileNumbers): string {
  const missing: string[] = []
  if (n.opening_balance === null) missing.push('opening balance')
  if (n.closing_balance === null) missing.push('closing balance')
  if (n.txn_sum === null) missing.push('transaction total')
  if (missing.length > 0) {
    return `Holdfast could not find the ${missing.join(' and ')} on this statement, so it could not check the math.`
  }
  if (n.balance_diff !== null && !new Decimal(parseMoney(n.balance_diff)).abs().lte('0.01')) {
    return 'Opening balance plus the transactions does not equal the closing balance. A row may be missing or misread.'
  }
  return 'Holdfast flagged this statement for a manual look.'
}
