import type { ImportResult, ReconcileStatus } from '@/api/imports'

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

const REASON_COPY: Record<string, string> = {
  missing_balance:
    'Holdfast could not find the opening or closing balance, so it could not check the math.',
  balance_mismatch:
    'Opening balance plus the transactions does not equal the closing balance. A row may be missing or misread.',
  date_out_of_period: 'Some transactions are dated outside the statement period.',
  no_transactions_in_document: 'This statement had balances but no transactions.',
  sign_conflict:
    'Some rows have a type (debit or credit) that disagrees with the sign of the amount. Check the signs before accepting.',
  duplicate_fitid: 'The file reuses a transaction id on more than one row.',
  ofx_correction: 'The file corrects or deletes earlier rows, which Holdfast does not apply yet.',
  investment_data_skipped: 'The file also holds investment data, which was not imported.',
  ledger_balance_after_period_end:
    'The closing balance was struck after the statement period ended, so it includes later activity.',
  unexplained_duplicates:
    'Some rows were skipped as duplicates and could not be matched to an earlier statement.',
}

export function reasonCopy(code: string): string | undefined {
  return Object.hasOwn(REASON_COPY, code) ? REASON_COPY[code] : undefined
}

export function resultHeadline(result: ImportResult): string {
  if (result.balances_only) return 'Balances only, needs review'
  if (!result.created) return 'Already imported'
  if (result.blocks.some((block) => block.reconcile_status === 'needs_review')) {
    return 'Imported, needs review'
  }
  if (result.blocks.some((block) => block.reconcile_status === 'accepted')) {
    return 'Imported and accepted'
  }
  return 'Imported and reconciled'
}
