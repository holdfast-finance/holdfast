import Decimal from 'decimal.js'

import type { ImportBlock, ImportResult, ImportRun } from '@/api/imports'
import { LEDGER_IDS } from '@/mocks/ledger'
import { db, now } from '@/mocks/db'

/*
 * Invented import runs for the mock API. Every name, mask and amount is made up. Money is a
 * decimal string and any arithmetic goes through decimal.js.
 *
 * The mock decides an upload's outcome from the filename (in the browser) so screenshots can reach
 * every state: "review" flags the statement, "dates" adds an unknown reason code, "balances" is a
 * balances-only statement, and any key of UPLOAD_ERRORS ("busy", "running", "huge", "boom", ...)
 * answers that refusal. jsdom's XHR drops filenames, so tests pick an outcome with `server.use`.
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
  reason?: string
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
    reason: 'balance_mismatch,date_out_of_period',
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
    sum: '0.00',
    reconcile: 'needs_review',
    reason: 'unknown',
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
  {
    id: '00000000-0000-4000-8000-00000000b006',
    filename: 'example-checking-2026-11.pdf',
    account: 'checking',
    name: 'Everyday checking',
    start: '2026-11-01',
    end: '2026-11-30',
    opening: '1534.50',
    closing: '1436.15',
    sum: '-98.35',
    reconcile: 'needs_review',
    reason: 'unexplained_duplicates',
    parsed: 27,
    inserted: 25,
    duplicate: 2,
    created: '2026-12-02T13:00:00Z',
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
    reason: s.reason ?? null,
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
    reviewed_at: s.reconcile === 'accepted' ? '2026-10-04T10:00:00Z' : null,
    created_at: s.created,
  }
}

export function seedImportRuns({ empty = false }: { empty?: boolean } = {}): void {
  db.importRuns = empty ? [] : RUNS.map(toRun)
  db.importedFiles = []
}

/** Build the response for an upload, record the run, and remember the file. */
export function importFile(filename: string, content: string): ImportResult {
  const key = `${filename}\n${content}`
  const already = db.importedFiles.includes(key)
  const scenario = `${filename} ${content}`
  const flagged = /review/i.test(scenario)
  const stale = /dates/i.test(scenario)
  const opening = '1534.50'
  const sum = flagged ? '-210.00' : '-98.35'
  const closing = flagged ? '1400.00' : '1436.15'
  const runId = `00000000-0000-4000-8000-${(0xd000 + db.importRuns.length + 1).toString(16).padStart(12, '0')}`
  const documentId = runId.replace('d', 'e')
  const status = flagged || stale ? 'needs_review' : 'reconciled'
  const block: ImportBlock = {
    run_id: runId,
    account_id: LEDGER_IDS.checking,
    account_name: 'Everyday checking',
    currency: 'USD',
    reconcile_status: status,
    reason: stale ? 'date_out_of_period,odd_new_code' : flagged ? 'balance_mismatch' : null,
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
      document_id: documentId,
      filename,
      account_id: block.account_id,
      account_name: block.account_name,
      currency: 'USD',
      status: 'succeeded',
      reconcile_status: block.reconcile_status,
      reason: block.reason,
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
      reviewed_at: null,
      created_at: now(),
    })
  }
  return {
    document_id: documentId,
    filename,
    created: !already,
    balances_only: false,
    balances: [],
    blocks: [block],
  }
}

/** A statement with balances and no transactions: nothing is stored as a run. */
export function balancesOnlyResult(filename: string): ImportResult {
  return {
    document_id: '00000000-0000-4000-8000-00000000e0ff',
    filename,
    created: true,
    balances_only: true,
    balances: [{ account_last4: '3333', opening_balance: '820.00', closing_balance: '905.40' }],
    blocks: [],
  }
}

export interface ScenarioError {
  status: number
  code: string
  message: string
  retryAfter?: number
}

/** Every refusal the upload route can answer, with the server's fixed messages. */
export const UPLOAD_ERRORS = {
  huge: { status: 413, code: 'payload_too_large', message: 'That PDF is over the 20 MiB limit.' },
  notpdf: { status: 422, code: 'validation_error', message: "That file isn't a PDF." },
  empty: { status: 422, code: 'validation_error', message: 'That file is empty.' },
  slowbody: {
    status: 422,
    code: 'validation_error',
    message: 'That upload took too long to arrive. Try again on a steadier connection.',
  },
  range: {
    status: 422,
    code: 'validation_error',
    message: 'An amount in that statement is out of range.',
  },
  running: {
    status: 429,
    code: 'rate_limited',
    message: 'Another upload of yours is still running. Wait for it to finish.',
    retryAfter: 5,
  },
  busy: {
    status: 429,
    code: 'rate_limited',
    message: 'Holdfast is busy reading other statements. Try again shortly.',
    retryAfter: 3,
  },
  otherperson: {
    status: 409,
    code: 'conflict',
    message: "That statement's account belongs to another person in this household.",
  },
  inprogress: {
    status: 409,
    code: 'conflict',
    message: 'That statement is already being imported.',
  },
  boom: { status: 500, code: 'server_error', message: "The statement couldn't be saved." },
} as const satisfies Record<string, ScenarioError>

export type UploadErrorKey = keyof typeof UPLOAD_ERRORS

export function uploadErrorKey(scenario: string): UploadErrorKey | null {
  return (Object.keys(UPLOAD_ERRORS) as UploadErrorKey[]).find((k) => scenario.includes(k)) ?? null
}
