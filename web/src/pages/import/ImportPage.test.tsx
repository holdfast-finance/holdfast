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
  await userEvent.upload(await screen.findByLabelText(/statement file/i), file)
  await userEvent.click(screen.getByRole('button', { name: 'Import statement' }))
}

// The shell footer has its own status region, so pick the upload card's live region by its marker.
const uploadStatus = (): HTMLElement => {
  const region = screen
    .getAllByRole('status')
    .find((el) => el.getAttribute('aria-atomic') === 'true')
  if (!region) throw new Error('upload live region not found')
  return region
}
const queue = () => screen.findByRole('region', { name: /Needs review/ })
const result = () => screen.findByRole('region', { name: 'Import result' })

describe('Import page: runs and the review queue', () => {
  it('lists runs and the needs_review queue with the figures behind each flag', async () => {
    open()
    const review = await queue()
    expect(within(review).getByText('Sample travel card')).toBeInTheDocument()
    // opening -380.00 + -200.00 = -580.00 against a closing of -625.25.
    expect(within(review).getByText('-$45.25')).toBeInTheDocument()
    // The savings statement has no opening balance.
    expect(within(review).getAllByText('Not found').length).toBeGreaterThan(0)
    expect(
      within(review).getByText(/From the figures: Holdfast could not find/),
    ).toBeInTheDocument()
    expect(within(review).getByText(/^Opening balance plus/)).toBeInTheDocument()
    const recent = screen.getByRole('region', { name: 'Recent imports' })
    expect(within(recent).getAllByRole('listitem')).toHaveLength(6)
    expect(within(recent).getByText(/ParseError, nothing was added/)).toBeInTheDocument()
    expect(within(recent).getByText(/accepted Oct 4/)).toBeInTheDocument()
  })

  it.each<[string, RegExp[]]>([
    ['balance_mismatch', [/^Opening balance plus/]],
    ['missing_balance', [/^Holdfast could not find/]],
    ['date_out_of_period', [/^Some transactions are dated outside/]],
    ['no_transactions_in_document', [/^This statement had balances but no transactions/]],
    ['unexplained_duplicates', [/^Some rows were skipped as duplicates/]],
    [
      'balance_mismatch, date_out_of_period',
      [/^Opening balance plus/, /^Some transactions are dated outside/],
    ],
    ['unknown', [/^From the figures: Opening balance plus/]],
    ['odd_new_code', [/^From the figures: Opening balance plus/]],
    [
      'date_out_of_period,odd_new_code',
      [/^Some transactions are dated outside/, /^From the figures: Opening balance plus/],
    ],
  ])('shows reason %s in the queue and accept sheet', async (reason, expected) => {
    seedHousehold({ signedIn: true })
    seedLedger()
    seedImportRuns({ empty: true })
    importFile('example-review.pdf', 'review')
    const run = db.importRuns[0]
    if (!run) throw new Error('seed missing')
    run.reason = reason
    renderApp('/import')
    const review = await queue()
    const assertReasons = (scope: HTMLElement) => {
      for (const line of expected) expect(within(scope).getByText(line)).toBeVisible()
      expect(within(scope).queryByText(/^Flagged:/)).not.toBeInTheDocument()
      expect(within(scope).queryAllByText(/^From the figures:/)).toHaveLength(
        expected.filter((line) => line.source.startsWith('^From the figures:')).length,
      )
    }
    assertReasons(review)
    await userEvent.click(within(review).getByRole('button', { name: 'Review and accept' }))
    assertReasons(await screen.findByRole('dialog', { name: 'Accept this statement?' }))
  })

  it('explains when a stored code and the figures give no reason', async () => {
    seedHousehold({ signedIn: true })
    seedLedger()
    seedImportRuns({ empty: true })
    importFile('example-dates.pdf', 'dates')
    const run = db.importRuns[0]
    if (!run) throw new Error('seed missing')
    run.reason = 'unknown'
    renderApp('/import')
    const review = await queue()
    const fallback = 'From the figures: No reason could be determined.'
    expect(within(review).getByText(fallback)).toBeVisible()
    await userEvent.click(within(review).getByRole('button', { name: 'Review and accept' }))
    expect(within(await screen.findByRole('dialog')).getByText(fallback)).toBeVisible()
  })

  it('shows an empty state with no imports', async () => {
    open({ empty: true })
    expect(await screen.findByText('Nothing needs review.')).toBeInTheDocument()
    expect(screen.getByText(/No imports yet/)).toBeInTheDocument()
  })

  it.each([
    [null, 'Every imported statement reconciled.'],
    ['accepted', 'Nothing needs review.'],
    ['pending', 'Nothing needs review.'],
  ])('summarizes a complete queue with extra reconciliation status %s', async (status, message) => {
    seedHousehold({ signedIn: true })
    seedLedger()
    seedImportRuns()
    db.importRuns = db.importRuns.filter(
      (run) => run.reconcile_status === 'reconciled' || run.reconcile_status === status,
    )
    renderApp('/import')
    expect(within(await queue()).getByText(message)).toBeInTheDocument()
  })

  it('limits the empty queue summary to loaded pages and shows the next-page error', async () => {
    let failNext = true
    server.use(
      http.get('*/api/imports/runs', ({ request }) => {
        if (new URL(request.url).searchParams.get('cursor') !== 'older') {
          return HttpResponse.json({
            items: db.importRuns.filter((run) => run.reconcile_status === 'reconciled'),
            next_cursor: 'older',
          })
        }
        if (failNext) {
          return HttpResponse.json(
            {
              error: {
                code: 'server_error',
                message: 'Older imports are temporarily unavailable.',
              },
            },
            { status: 500 },
          )
        }
        return HttpResponse.json({ items: [], next_cursor: null })
      }),
    )
    open()
    const review = within(await queue())
    expect(review.getByText('Nothing needs review in the imports loaded so far.')).toBeVisible()
    await userEvent.click(review.getByRole('button', { name: 'Load more' }))
    expect(await review.findByRole('alert')).toHaveTextContent(
      'Older imports are temporarily unavailable.',
    )
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(review.getByRole('button', { name: 'Retry' })).toBeEnabled()
    const recent = within(screen.getByRole('region', { name: 'Recent imports' }))
    failNext = false
    await userEvent.click(recent.getByRole('button', { name: 'Retry' }))
    expect(await review.findByText('Every imported statement reconciled.')).toBeVisible()
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
    const review = await queue()
    expect(within(review).getByText('This queue covers loaded pages only.')).toBeInTheDocument()
    await userEvent.click(within(review).getByRole('button', { name: 'Load more' }))
    await waitFor(() => {
      expect(within(recent).getAllByRole('listitem')).toHaveLength(26)
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
    expect(within(dialog).getByText(/^Opening balance plus/)).toBeInTheDocument()
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
    expect(db.importRuns.find((r) => r.account_name === 'Sample travel card')?.reason).toBeNull()
  })

  it('cancel leaves the run untouched', async () => {
    const dialog = await openAccept()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      const review = screen.getByRole('region', { name: /Needs review/ })
      expect(within(review).getAllByRole('button', { name: 'Review and accept' })[0]).toHaveFocus()
    })
    expect(db.importRuns.filter((r) => r.reconcile_status === 'needs_review')).toHaveLength(3)
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
  it('offers a keyboard-focusable statement picker and describes household storage', async () => {
    open()
    const picker = await screen.findByLabelText(/statement file/i)
    expect(picker).toHaveAttribute('accept', 'application/pdf,.pdf,.csv,text/csv,.ofx,.qfx')
    expect(screen.getByText(/Stored with your household's documents/)).toBeVisible()
    picker.focus()
    expect(picker).toHaveFocus()
  })

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
      ).toHaveLength(7)
    })
  })

  it('shows the notice when a CSV or OFX went into its own account', async () => {
    respond(() =>
      HttpResponse.json({
        ...importFile('march.csv', 'ok'),
        file_kind: 'csv',
        notice: 'This file went into its own new account. It may repeat transactions.',
      }),
    )
    open()
    await upload(pdf('march.csv'))
    const res = await result()
    expect(await within(res).findByRole('note')).toHaveTextContent(/its own new account/)
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
    expect(uploadStatus()).toHaveTextContent('Imported, needs review')
    expect(within(res).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    await userEvent.click(within(res).getByRole('button', { name: 'Review and accept' }))
    const dialog = await screen.findByRole('dialog', { name: 'Accept this statement?' })
    // The upload response carries the reason, so it is shown here too.
    expect(within(dialog).getByText(/does not equal the closing balance/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept statement' }))
    const heading = await within(res).findByRole('heading', { name: 'Imported and accepted' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(uploadStatus()).toBeEmptyDOMElement()
    expect(within(res).getByText('Accepted')).toBeInTheDocument()
    expect(within(res).queryByRole('button', { name: 'Review and accept' })).not.toBeInTheDocument()
    expect(db.importRuns[0]?.reconcile_status).toBe('accepted')
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

  it('accepts a CSV or OFX export and refuses other file types before sending', async () => {
    let posted = 0
    respond(() => {
      posted += 1
      return HttpResponse.json({})
    })
    open()
    const user = userEvent.setup({ applyAccept: false })
    const picker = await screen.findByLabelText(/statement file/i)
    await user.upload(picker, new File(['x'], 'notes.txt', { type: 'text/plain' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/not a PDF, CSV or OFX\/QFX/)
    for (const file of [
      new File(['x'], 'march.csv', { type: 'text/csv' }),
      new File(['x'], 'export', { type: 'text/csv' }), // the MIME type alone is enough
      new File(['x'], 'bank.QFX', { type: '' }),
      new File(['x'], 'bank.ofx', { type: '' }),
    ]) {
      await user.upload(picker, file)
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.getByRole('button', { name: 'Import statement' })).toBeEnabled()
    }
    await user.upload(
      picker,
      new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'big.csv', { type: 'text/csv' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/over the 10 MiB upload limit/)
    await user.upload(
      picker,
      new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'big.ofx', { type: '' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/over the 5 MiB upload limit/)
    // A PDF may be larger than a text file, up to its own limit.
    await user.upload(
      picker,
      new File([new Uint8Array(MAX_UPLOAD_BYTES)], 'ok.pdf', { type: 'application/pdf' }),
    )
    expect(screen.queryByRole('alert')).toBeNull()
    await user.upload(
      picker,
      new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'bigger.pdf', {
        type: 'application/pdf',
      }),
    )
    expect(screen.queryByRole('alert')).toBeNull()
    await user.upload(
      picker,
      new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], 'huge.pdf', { type: 'application/pdf' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/over the 20 MiB upload limit/)
    expect(posted).toBe(0)
  })

  it('lets a CSV choose an existing account or a new bank or card account', async () => {
    let body = ''
    server.use(
      http.post('*/api/imports/statements', async ({ request }) => {
        body = await request.text()
        return HttpResponse.json(importFile('march.csv', 'ok'))
      }),
    )
    open()
    const user = userEvent.setup()
    const picker = await screen.findByLabelText(/statement file/i)
    await user.upload(picker, new File(['x'], 'march.csv', { type: 'text/csv' }))
    expect(await screen.findByLabelText('Add this file to')).toBeVisible()
    expect(
      await screen.findByRole('option', { name: /Everyday checking ending 1111/ }),
    ).toBeInTheDocument()
    // A new CSV account must say bank or card.
    await user.click(screen.getByRole('radio', { name: 'Card' }))
    await user.click(screen.getByRole('button', { name: 'Import statement' }))
    await screen.findByRole('region', { name: 'Import result' })
    expect(body).toMatch(/name="account"\r\n\r\nnew\r\n/)
    expect(body).toMatch(/name="account_kind"\r\n\r\ncard\r\n/)
  })

  it('says how many added rows may already be in the account from another file', async () => {
    respond(() => {
      const base = importFile('march.csv', 'ok')
      const block = base.blocks[0]
      if (!block) throw new Error('mock has a block')
      return HttpResponse.json({
        ...base,
        blocks: [
          {
            ...block,
            rows_overlap: 2,
            reconcile_status: 'needs_review',
            reason: 'possible_overlap',
          },
        ],
      })
    })
    open()
    await upload(new File(['x'], 'march.csv', { type: 'text/csv' }))
    const res = await result()
    expect(
      await within(res).findByText(/2 of the added rows may already be in this account/),
    ).toBeVisible()
    expect(
      within(res).getByText(/^Some rows may already be in this account from another file/),
    ).toBeVisible()
  })

  it('shows the notice in the result and announces it', async () => {
    respond(() => HttpResponse.json(importFile('march.csv', 'ok')))
    open()
    const user = userEvent.setup()
    await user.upload(
      await screen.findByLabelText(/statement file/i),
      new File(['x'], 'march.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: 'Import statement' }))
    const res = await result()
    expect(await within(res).findByRole('note')).toHaveTextContent(/own new account/)
    expect(uploadStatus()).toHaveTextContent(/own new account/)
  })

  it('answers a 413 with the limits for each kind of file', async () => {
    server.use(http.post('*/api/imports/statements', () => new HttpResponse(null, { status: 413 })))
    open()
    await upload(pdf('x.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /over the upload limit \(20 MiB for PDF, 10 MiB for CSV, 5 MiB for OFX and QFX\)/,
    )
  })

  it('names the file input by its visible text and ties it to the description', async () => {
    open()
    const input = await screen.findByLabelText(/choose a statement file/i)
    const describedBy = input.getAttribute('aria-describedby') ?? ''
    const desc = document.getElementById(describedBy.split(' ')[0] ?? '')
    expect(desc).toHaveTextContent(/PDF up to 20 MiB\. CSV up to 10 MiB, OFX\/QFX up to 5 MiB/)
  })

  it('refuses an oversize file before sending it', async () => {
    let posted = false
    respond(() => {
      posted = true
      return HttpResponse.json({})
    })
    open()
    await userEvent.upload(
      await screen.findByLabelText(/statement file/i),
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
