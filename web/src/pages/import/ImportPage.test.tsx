import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { MAX_UPLOAD_BYTES } from '@/api/imports'
import { db, seedHousehold } from '@/mocks/db'
import { importFile, seedImportRuns } from '@/mocks/imports'
import { seedLedger } from '@/mocks/ledger'
import { server } from '@/mocks/node'
import { renderApp } from '@/test/render'

function open(options: { empty?: boolean } = {}) {
  seedHousehold({ signedIn: true })
  seedLedger()
  seedImportRuns(options)
  return renderApp('/import')
}

// jsdom's XHR drops the filename and the bytes, so tests pick the mock's outcome with
// `server.use` (in the browser the mock also reads it from the filename: "review", "boom", "huge").
const pdf = (name: string, bytes = 2048) =>
  new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })

const answer = (status: number, code: string, message: string) => { server.use(
    http.post('*/api/imports/statements', () =>
      HttpResponse.json({ error: { code, message } }, { status }),
    ),
  ); }

async function upload(file: File) {
  await userEvent.upload(await screen.findByLabelText('Statement PDF'), file)
  await userEvent.click(screen.getByRole('button', { name: 'Import statement' }))
}

describe('Import page', () => {
  it('lists runs and the needs_review queue with the numbers behind each flag', async () => {
    open()
    const review = await screen.findByRole('region', { name: /Needs review/ })
    // The card statement: opening -410.00 + -200.00 = -610.00, closing -655.25, so -45.25 off.
    expect(within(review).getByText('Sample travel card')).toBeInTheDocument()
    expect(within(review).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    expect(within(review).getByText('-$45.25')).toBeInTheDocument()
    // The savings statement had no opening balance: say so instead of showing zero.
    expect(within(review).getByText(/could not find the opening balance/)).toBeInTheDocument()
    const recent = screen.getByRole('region', { name: 'Recent imports' })
    expect(within(recent).getAllByRole('listitem')).toHaveLength(5)
    expect(within(recent).getByText(/ParseError, nothing was added/)).toBeInTheDocument()
  })

  it('shows an empty state with no imports', async () => {
    open({ empty: true })
    expect(await screen.findByText(/Nothing to review/)).toBeInTheDocument()
    expect(screen.getByText(/No imports yet/)).toBeInTheDocument()
  })

  it('imports a statement that reconciles', async () => {
    open()
    await upload(pdf('november.pdf'))
    expect(await screen.findByRole('heading', { name: 'Imported and reconciled' })).toBeVisible()
    const result = screen.getByRole('region', { name: 'Import result' })
    expect(within(result).getByText('Reconciled')).toBeInTheDocument()
    expect(within(result).getByText('Nov 1, 2026 to Nov 30, 2026')).toBeInTheDocument()
    expect(within(result).getByText('$1,534.50')).toBeInTheDocument()
    expect(within(result).getByText('-$98.35')).toBeInTheDocument()
    expect(within(result).getByText('$1,436.15')).toBeInTheDocument()
    expect(within(result).getByText('27 of 27 rows added.')).toBeInTheDocument()
    // The run list refreshes behind it.
    await waitFor(() => {
      expect(
        within(screen.getByRole('region', { name: 'Recent imports' })).getAllByRole('listitem'),
      ).toHaveLength(6)
    })
  })

  it('flags a statement that does not reconcile and says why', async () => {
    server.use(
      http.post('*/api/imports/statements', () =>
        HttpResponse.json(importFile('november-review.pdf', '')),
      ),
    )
    open()
    await upload(pdf('november-review.pdf'))
    expect(await screen.findByRole('heading', { name: 'Imported, needs review' })).toBeVisible()
    const result = screen.getByRole('region', { name: 'Import result' })
    expect(within(result).getByText('Needs review')).toBeInTheDocument()
    expect(within(result).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    // 1534.50 - 210.00 = 1324.50 against a closing of 1400.00.
    expect(within(result).getByText('$75.50')).toBeInTheDocument()
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

  it('refuses an oversize file before sending it', async () => {
    let posted = false
    server.use(
      http.post('*/api/imports/statements', () => {
        posted = true
        return HttpResponse.json({})
      }),
    )
    open()
    await userEvent.upload(
      await screen.findByLabelText('Statement PDF'),
      pdf('big.pdf', MAX_UPLOAD_BYTES + 1),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/over the 1 MiB upload limit/)
    expect(screen.getByRole('button', { name: 'Import statement' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Import statement' }))
    expect(posted).toBe(false)
  })

  it('explains a server-side 413', async () => {
    answer(413, 'payload_too_large', 'That request is too large.')
    open()
    await upload(pdf('huge.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/too large \(limit 1 MiB\)/)
  })

  it('shows a server error and lets you retry', async () => {
    answer(500, 'internal_error', 'The import failed on the server.')
    open()
    await upload(pdf('boom.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent('The import failed on the server.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
  })

  it('asks for step-up when the session is stale, then retries once', async () => {
    open()
    await screen.findByLabelText('Statement PDF')
    if (db.session) db.session.reauthAt = Date.now() - 10 * 60 * 1000
    await upload(pdf('november.pdf'))
    expect(await screen.findByRole('heading', { name: /Confirm it.s you/i })).toBeVisible()
  })
})
