import Decimal from 'decimal.js'

import type { ImportBlock, ImportResult, ImportRun } from '@/api/imports'
import { LEDGER_IDS } from '@/mocks/ledger'
import { db, now } from '@/mocks/db'

/*
 * Invented import runs for the mock API. Every name, mask and amount is made up. Money is a
 * decimal string and any arithmetic goes through decimal.js.
 *
 * The mock decides an upload's outcome from the filename or file text so tests and screenshots can
 * reach every state: "review" flags the statement, "boom" is a 500, "huge" is a 413, "notpdf" is a 422.
 * Uploading the same name and bytes twice answers "already imported" (`created: false`), like the real SHA-256
 * dedupe does for identical bytes.
 */

function diff(opening: string, closing: string, sum: string): string {
  return new Decimal(closing).minus(new Decimal(opening).plus(sum)).toFixed(2)
}

interface RunSeed {
  id: string
  filename: string
  account: 'checking' | 'savings' | 'card'
  name: string
  start: string
  end: string
  opening: string | null
  closing: string | null
  sum: string | null
  reconcile: ImportRun['reconcile_status']
  status?: ImportRun['status']
  parsed: number
  inserted: number
  duplicate: number
  error?: string
  created: string
}

const RUNS: RunSeed[] = [
  {
    id: '00000000-0000-4000-8000-00000000b001',
    filename: 'example-checking-2026-10.pdf',
    account: 'checking',
    name: 'Everyday checking',
    start: '2026-10-01',
    end: '2026-10-31',
    opening: '1200.00',
    closing: '1534.50',
    sum: '334.50',
    reconcile: 'reconciled',
    parsed: 42,
    inserted: 42,
    duplicate: 0,
    created: '2026-11-02T14:03:00Z',
  },
  {
    id: '00000000-0000-4000-8000-00000000b002',
    filename: 'example-card-2026-10.pdf',
    account: 'card',
    name: 'Sample travel card',
    start: '2026-10-05',
    end: '2026-11-04',
    opening: '-410.00',
    closing: '-655.25',
    sum: '-200.00',
    reconcile: 'needs_review',
    parsed: 31,
    inserted: 31,
    duplicate: 0,
    created: '2026-11-05T09:30:00Z',
  },
  {
    id: '00000000-0000-4000-8000-00000000b003',
    filename: 'example-savings-2026-10.pdf',
    account: 'savings',
    name: 'Rainy day savings',
    start: '2026-10-01',
    end: '2026-10-31',
    opening: null,
    closing: '2500.00',
    sum: null,
    reconcile: 'needs_review',
    parsed: 3,
    inserted: 3,
    duplicate: 0,
    created: '2026-11-06T18:12:00Z',
  },
  {
    id: '00000000-0000-4000-8000-00000000b004',
    filename: 'example-checking-2026-09.pdf',
    account: 'checking',
    name: 'Everyday checking',
    start: '2026-09-01',
    end: '2026-09-30',
    opening: '900.00',
    closing: '1200.00',
    sum: '300.00',
    reconcile: 'accepted',
    parsed: 38,
    inserted: 38,
    duplicate: 0,
    created: '2026-10-03T11:45:00Z',
  },
  {
    id: '00000000-0000-4000-8000-00000000b005',
    filename: 'example-scan.pdf',
    account: 'checking',
    name: 'Everyday checking',
    start: '2026-08-01',
    end: '2026-08-31',
    opening: null,
    closing: null,
    sum: null,
    reconcile: 'pending',
    status: 'failed',
    parsed: 0,
    inserted: 0,
    duplicate: 0,
    error: 'ParseError',
    created: '2026-10-01T08:00:00Z',
  },
]

function toRun(s: RunSeed): ImportRun {
  return {
    id: s.id,
    document_id: s.id.replace('b0', 'c0'),
    filename: s.filename,
    account_id: LEDGER_IDS[s.account],
    account_name: s.name,
    currency: 'USD',
    status: s.status ?? 'succeeded',
    reconcile_status: s.reconcile,
    period_start: s.start,
    period_end: s.end,
    opening_balance: s.opening,
    closing_balance: s.closing,
    txn_sum: s.sum,
    balance_diff:
      s.opening !== null && s.closing !== null && s.sum !== null
        ? diff(s.opening, s.closing, s.sum)
        : null,
    rows_parsed: s.parsed,
    rows_inserted: s.inserted,
    rows_duplicate: s.duplicate,
    error_class: s.error ?? null,
    created_at: s.created,
  }
}

export function seedImportRuns({ empty = false }: { empty?: boolean } = {}): void {
  db.importRuns = empty ? [] : RUNS.map(toRun)
  db.importedFiles = []
}

/** Build the response for an upload, record the run, and remember the filename. */
export function importFile(filename: string, content: string): ImportResult {
  const key = `${filename}\n${content}`
  const already = db.importedFiles.includes(key)
  const flagged = /review/i.test(`${filename} ${content}`)
  const opening = '1534.50'
  const sum = flagged ? '-210.00' : '-98.35'
  const closing = flagged ? '1400.00' : '1436.15'
  const runId = `00000000-0000-4000-8000-${(0xd000 + db.importRuns.length + 1).toString(16).padStart(12, '0')}`
  const block: ImportBlock = {
    run_id: runId,
    account_id: LEDGER_IDS.checking,
    account_name: 'Everyday checking',
    currency: 'USD',
    reconcile_status: flagged ? 'needs_review' : 'reconciled',
    period_start: '2026-11-01',
    period_end: '2026-11-30',
    opening_balance: opening,
    closing_balance: closing,
    txn_sum: sum,
    balance_diff: diff(opening, closing, sum),
    rows_parsed: 27,
    rows_inserted: already ? 0 : 27,
    rows_duplicate: already ? 27 : 0,
    created: !already,
  }
  if (!already) {
    db.importedFiles.push(key)
    db.importRuns.unshift({
      id: runId,
      document_id: runId.replace('d', 'e'),
      filename,
      account_id: block.account_id,
      account_name: block.account_name,
      currency: 'USD',
      status: 'succeeded',
      reconcile_status: block.reconcile_status,
      period_start: block.period_start,
      period_end: block.period_end,
      opening_balance: block.opening_balance,
      closing_balance: block.closing_balance,
      txn_sum: block.txn_sum,
      balance_diff: block.balance_diff,
      rows_parsed: block.rows_parsed,
      rows_inserted: block.rows_inserted,
      rows_duplicate: 0,
      error_class: null,
      created_at: now(),
    })
  }
  return { document_id: runId.replace('d', 'e'), filename, balances_only: false, blocks: [block] }
}
