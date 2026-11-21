import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { MAX_UPLOAD_BYTES } from '@/api/imports'
import { db, seedHousehold } from '@/mocks/db'
import { uploadFailure } from '@/mocks/handlers'
import {
  UPLOAD_ERRORS,
  balancesOnlyResult,
  importFile,
  seedImportRuns,
  type UploadErrorKey,
} from '@/mocks/imports'
import { seedLedger } from '@/mocks/ledger'
import { server } from '@/mocks/node'
import { renderApp } from '@/test/render'

function open(options: { empty?: boolean; path?: string } = {}) {
  seedHousehold({ signedIn: true })
  seedLedger({ empty: options.path === '/transactions' })
  seedImportRuns(options)
  return renderApp(options.path ?? '/import')
}

// jsdom's XHR drops the filename and the bytes, so tests pick the mock's outcome with
// `server.use` (in the browser the mock also reads it from the filename: "review", "busy", ...).
const pdf = (name: string, bytes = 2048) =>
  new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })

const respond = (build: () => Response) => {
  server.use(http.post('*/api/imports/statements', build))
}
const fail = (key: UploadErrorKey) => {
  respond(() => uploadFailure(UPLOAD_ERRORS[key]))
}

async function upload(file: File) {
  await userEvent.upload(await screen.findByLabelText('Statement PDF'), file)
  await userEvent.click(screen.getByRole('button', { name: 'Import statement' }))
}

const queue = () => screen.findByRole('region', { name: /Needs review/ })
const result = () => screen.findByRole('region', { name: 'Import result' })

describe('Import page: runs and the review queue', () => {
  it('lists runs and the needs_review queue with the figures behind each flag', async () => {
    open()
    const review = await queue()
    expect(within(review).getByText('Sample travel card')).toBeInTheDocument()
    // opening -410.00 + -200.00 = -610.00 against a closing of -655.25.
    expect(within(review).getByText('-$45.25')).toBeInTheDocument()
    // The savings statement has no opening balance or transaction total: say so, not zero.
    expect(within(review).getAllByText('Not found').length).toBeGreaterThan(0)
    // The runs list carries no reason, so say so instead of guessing.
    expect(within(review).getAllByText('Reason not recorded.')).toHaveLength(2)
    const recent = screen.getByRole('region', { name: 'Recent imports' })
    expect(within(recent).getAllByRole('listitem')).toHaveLength(5)
    expect(within(recent).getByText(/ParseError, nothing was added/)).toBeInTheDocument()
    expect(within(recent).getByText(/accepted Oct 4/)).toBeInTheDocument()
  })

  it('shows an empty state with no imports', async () => {
    open({ empty: true })
    expect(await screen.findByText(/Nothing to review/)).toBeInTheDocument()
    expect(screen.getByText(/No imports yet/)).toBeInTheDocument()
  })

  it('pages the run list with Load more', async () => {
    seedHousehold({ signedIn: true })
    seedLedger()
    seedImportRuns()
    const base = db.importRuns[0]
    if (!base) throw new Error('seed missing')
    for (let i = 0; i < 20; i += 1) {
      db.importRuns.push({
        ...base,
        id: `00000000-0000-4000-8000-0000000f${String(i).padStart(4, '0')}`,
        filename: `older-${String(i)}.pdf`,
      })
    }
    renderApp('/import')
    const recent = await screen.findByRole('region', { name: 'Recent imports' })
    await waitFor(() => {
      expect(within(recent).getAllByRole('listitem')).toHaveLength(20)
    })
    await userEvent.click(within(recent).getByRole('button', { name: 'Load more' }))
    await waitFor(() => {
      expect(within(recent).getAllByRole('listitem')).toHaveLength(25)
    })
    expect(within(recent).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })
})

describe('Import page: accepting a flagged statement', () => {
  async function openAccept() {
    open()
    const review = await queue()
    await userEvent.click(
      within(review).getAllByRole('button', { name: 'Review and accept' })[0] as HTMLElement,
    )
    return screen.findByRole('dialog', { name: 'Accept this statement?' })
  }

  it('shows why and the difference first, says it does not reconcile, then accepts', async () => {
    const dialog = await openAccept()
    expect(within(dialog).getByText('Reason not recorded.')).toBeInTheDocument()
    expect(within(dialog).getByText('-$45.25')).toBeInTheDocument()
    expect(within(dialog).getByText(/make it reconciled/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept statement' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    const review = await queue()
    await waitFor(() => {
      expect(within(review).queryByText('Sample travel card')).not.toBeInTheDocument()
    })
    expect(
      db.importRuns.find((r) => r.account_name === 'Sample travel card')?.reconcile_status,
    ).toBe('accepted')
  })

  it('cancel leaves the run untouched', async () => {
    const dialog = await openAccept()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(db.importRuns.filter((r) => r.reconcile_status === 'needs_review')).toHaveLength(2)
  })

  it('shows the server answer when the run is no longer reviewable', async () => {
    const dialog = await openAccept()
    server.use(
      http.post('*/api/imports/runs/:id/accept', () =>
        HttpResponse.json(
          { error: { code: 'conflict', message: 'Only a run that needs review can be accepted.' } },
          { status: 409 },
        ),
      ),
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept statement' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Only a run that needs review',
    )
  })

  it('asks for step-up when the session is stale', async () => {
    const dialog = await openAccept()
    if (db.session) db.session.reauthAt = Date.now() - 10 * 60 * 1000
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept statement' }))
    expect(await screen.findByRole('heading', { name: /Confirm it.s you/i })).toBeVisible()
  })
})

describe('Import page: uploading', () => {
  it('imports a statement that reconciles', async () => {
    open()
    await upload(pdf('november.pdf'))
    expect(await screen.findByRole('heading', { name: 'Imported and reconciled' })).toBeVisible()
    const res = await result()
    expect(within(res).getByText('Reconciled')).toBeInTheDocument()
    expect(within(res).getByText('Nov 1, 2026 to Nov 30, 2026')).toBeInTheDocument()
    expect(within(res).getByText('$1,534.50')).toBeInTheDocument()
    expect(within(res).getByText('-$98.35')).toBeInTheDocument()
    expect(within(res).getByText('$1,436.15')).toBeInTheDocument()
    expect(within(res).getByText('27 of 27 rows added.')).toBeInTheDocument()
    await waitFor(() => {
      expect(
        within(screen.getByRole('region', { name: 'Recent imports' })).getAllByRole('listitem'),
      ).toHaveLength(6)
    })
  })

  it('flags a statement, maps known reasons to plain copy and shows unknown codes verbatim', async () => {
    respond(() => HttpResponse.json(importFile('november-dates.pdf', 'dates')))
    open()
    await upload(pdf('november-dates.pdf'))
    expect(await screen.findByRole('heading', { name: 'Imported, needs review' })).toBeVisible()
    const res = await result()
    expect(within(res).getByText('Needs review')).toBeInTheDocument()
    expect(within(res).getByText(/dated outside the statement period/)).toBeInTheDocument()
    expect(within(res).getByText('Flagged: odd_new_code')).toBeInTheDocument()
  })

  it('explains a balance mismatch and can accept it straight from the result', async () => {
    respond(() => HttpResponse.json(importFile('november-review.pdf', 'review')))
    open()
    await upload(pdf('november-review.pdf'))
    const res = await result()
    expect(within(res).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    await userEvent.click(within(res).getByRole('button', { name: 'Review and accept' }))
    const dialog = await screen.findByRole('dialog', { name: 'Accept this statement?' })
    // The upload response carries the reason, so it is shown here too.
    expect(within(dialog).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept statement' }))
    await waitFor(() => {
      expect(db.importRuns[0]?.reconcile_status).toBe('accepted')
    })
  })

  it('treats a re-upload as already imported, not an error', async () => {
    open()
    await upload(pdf('november.pdf'))
    await screen.findByRole('heading', { name: 'Imported and reconciled' })
    await userEvent.click(screen.getByRole('button', { name: 'Import another statement' }))
    await upload(pdf('november.pdf'))
    expect(await screen.findByRole('heading', { name: 'Already imported' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText(/Nothing was added/)).toBeInTheDocument()
  })

  it('shows balances for a statement with no transactions and says where it stands', async () => {
    respond(() => HttpResponse.json(balancesOnlyResult('balances.pdf')))
    open()
    await upload(pdf('balances.pdf'))
    expect(
      await screen.findByRole('heading', { name: 'Balances only, needs review' }),
    ).toBeVisible()
    const res = await result()
    expect(within(res).getByText('Account ending 3333')).toBeInTheDocument()
    expect(within(res).getByText('$820.00')).toBeInTheDocument()
    expect(within(res).getByText('$905.40')).toBeInTheDocument()
    expect(within(res).getByText(/does not appear in your imports list yet/)).toBeInTheDocument()
  })

  it('refuses an oversize file before sending it', async () => {
    let posted = false
    respond(() => {
      posted = true
      return HttpResponse.json({})
    })
    open()
    await userEvent.upload(
      await screen.findByLabelText('Statement PDF'),
      pdf('big.pdf', MAX_UPLOAD_BYTES + 1),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/over the 20 MiB upload limit/)
    await userEvent.click(screen.getByRole('button', { name: 'Import statement' }))
    expect(posted).toBe(false)
  })

  it.each([
    ['huge', /over the 20 MiB limit/],
    ['notpdf', /isn't a PDF/],
    ['slowbody', /took too long to arrive/],
    ['range', /out of range/],
    ['otherperson', /belongs to another person in this household/],
    ['inprogress', /already being imported/],
    ['boom', /couldn't be saved/],
  ] as const)('shows the server answer for %s and lets you retry', async (key, text) => {
    fail(key)
    open()
    await upload(pdf('x.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
  })

  it.each([
    ['running', /still running.*Try again in 5 seconds/],
    ['busy', /busy reading other statements.*Try again in 3 seconds/],
  ] as const)('respects Retry-After on a 429 (%s)', async (key, text) => {
    fail(key)
    open()
    await upload(pdf('x.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })
})

describe('Transactions empty state', () => {
  it('links to Import', async () => {
    open({ path: '/transactions' })
    await userEvent.click(await screen.findByRole('link', { name: 'Import a statement' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Import' })).toBeVisible()
  })
})
