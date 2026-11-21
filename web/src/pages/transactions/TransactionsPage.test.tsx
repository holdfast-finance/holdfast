import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { seedHousehold } from '@/mocks/db'
import { LEDGER_IDS, seedLedger, type SeedLedgerOptions } from '@/mocks/ledger'
import { server } from '@/mocks/node'
import { mockViewport } from '@/test/matchMedia'
import { renderApp } from '@/test/render'

/** Every /api/transactions and /api/transactions/summary request the page made. */
let requests: URL[] = []
const record = (request: Request) => {
  const url = new URL(request.url)
  if (url.pathname.startsWith('/api/transactions')) requests.push(url)
}
beforeEach(() => {
  requests = []
  server.events.on('request:start', ({ request }) => {
    record(request)
  })
})
afterEach(() => {
  server.events.removeAllListeners()
})

function open(path = '/transactions', ledger: SeedLedgerOptions = {}) {
  seedHousehold({ signedIn: true })
  seedLedger(ledger)
  return renderApp(path)
}

const listRequests = () => requests.filter((u) => u.pathname === '/api/transactions')
const lastList = () => listRequests().at(-1)

const rowsIn = () => within(screen.getByRole('main')).queryAllByRole('listitem')

async function firstRows() {
  return waitFor(() => {
    const rows = rowsIn()
    expect(rows.length).toBeGreaterThan(0)
    return rows
  })
}

describe('Transactions page, desktop', () => {
  beforeEach(() => {
    mockViewport(1280)
  })

  it('lists accounts by type and leaves hidden accounts out until asked', async () => {
    open()
    const rail = await screen.findByRole('complementary', { name: 'Accounts and filters' })
    expect(await within(rail).findByText('Everyday checking')).toBeInTheDocument()
    expect(within(rail).getByText('Cash')).toBeInTheDocument()
    expect(within(rail).getByText('Credit cards')).toBeInTheDocument()
    expect(within(rail).getByText('First Example Bank ··0000')).toBeInTheDocument()
    expect(within(rail).queryByText('Old closed account')).not.toBeInTheDocument()
    // Imported accounts have no balance yet: show nothing, not zero.
    expect(within(rail).getAllByText(/^Balance/)).toHaveLength(1)

    await userEvent.click(within(rail).getByRole('checkbox', { name: /Show hidden accounts/ }))
    expect(await within(rail).findByText('Old closed account')).toBeInTheDocument()
  })

  it('sends only visible accounts by default', async () => {
    open()
    await firstRows()
    const ids = lastList()?.searchParams.getAll('account_id') ?? []
    expect(ids).toContain(LEDGER_IDS.checking)
    expect(ids).not.toContain(LEDGER_IDS.hidden)
  })

  it('groups rows by day under date headers and badges pending, transfer and review rows', async () => {
    open('/transactions?from=2026-11-18&to=2026-11-20')
    const heading = await screen.findByRole('heading', { level: 3, name: 'Fri, Nov 20, 2026' })
    expect(heading).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Thu, Nov 19, 2026' })).toBeInTheDocument()
    const rows = within(
      await screen.findByRole('region', { name: 'Fri, Nov 20, 2026' }),
    ).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(screen.getByText('Pending')).toBeInTheDocument()
    expect(screen.getAllByText('Transfer').length).toBeGreaterThan(0)
    expect(screen.getByText('Needs review')).toBeInTheDocument()
  })

  it('renders amounts exactly from their strings, with a sign on both directions', async () => {
    open('/transactions?from=2026-11-18&to=2026-11-20')
    const row = (await screen.findByText('Harbor Grocers')).closest('li')
    if (!row) throw new Error('row missing')
    expect(within(row).getByText('-$0.10')).toHaveClass('money')
    const pay = screen.getByText('Payroll deposit Example Co').closest('li')
    if (!pay) throw new Error('row missing')
    expect(within(pay).getByText('+$3,200.00')).toHaveClass('money')
    expect(within(pay).getByText('Inflow')).toBeInTheDocument()
    // A value far past 2^53 keeps every digit, which a float cannot do.
    expect(screen.getByText('-$1,234,567.89')).toBeInTheDocument()
  })

  it('shows a summary per currency and never adds currencies together', async () => {
    open('/transactions?from=2026-11-17&to=2026-11-20')
    const usd = await screen.findByRole('group', { name: 'USD totals' })
    const eur = screen.getByRole('group', { name: 'EUR totals' })
    expect(within(eur).getByText('+€12.30')).toBeInTheDocument()
    expect(within(eur).getByText('-€7.80')).toBeInTheDocument()
    expect(within(eur).getByText('+€4.50')).toBeInTheDocument()
    expect(within(usd).getByText('+$3,700.00')).toBeInTheDocument()
    // 0.10 + 0.20 + 500.00 + 23.45 + 1234567.89 on the outflow side, exact to the cent.
    expect(within(usd).getByText('-$1,235,091.64')).toBeInTheDocument()
  })

  it('summarises 0.10 and 0.20 as exactly 0.30', async () => {
    open('/transactions?dir=outflow&from=2026-11-20&to=2026-11-20')
    const usd = await screen.findByRole('group', { name: 'USD totals' })
    // A float sum would show 0.30000000000000004; decimals give -$0.30 for outflow and net.
    await waitFor(() => {
      expect(within(usd).getAllByText('-$0.30')).toHaveLength(2)
    })
    expect(within(usd).getByText('2')).toBeInTheDocument()
  })

  it('puts search, direction, amounts and transfers in the URL and in the request', async () => {
    const { router } = open()
    await firstRows()

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search descriptions' }), 'bakery')
    await waitFor(() => {
      expect(router.state.location.search).toContain('q=bakery')
    })
    await waitFor(() => {
      expect(lastList()?.searchParams.get('q')).toBe('bakery')
    })

    await userEvent.selectOptions(screen.getByLabelText('Direction'), 'outflow')
    await waitFor(() => {
      expect(router.state.location.search).toContain('dir=outflow')
      expect(lastList()?.searchParams.get('direction')).toBe('outflow')
    })

    await userEvent.type(screen.getByLabelText('Min amount'), '-50.25')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Hide transfers' }))
    await waitFor(() => {
      expect(router.state.location.search).toContain('min=-50.25')
      expect(router.state.location.search).toContain('transfers=hide')
      const q = lastList()?.searchParams
      expect(q?.get('min_amount')).toBe('-50.25')
      expect(q?.get('exclude_transfers')).toBe('true')
    })
  })

  it('applies a date range from the form', async () => {
    const { router } = open()
    await firstRows()
    await userEvent.type(screen.getByLabelText('From'), '2026-11-19')
    await waitFor(() => {
      expect(router.state.location.search).toContain('from=2026-11-19')
      expect(lastList()?.searchParams.get('date_from')).toBe('2026-11-19')
    })
  })

  it('refuses a malformed amount and an inverted range instead of sending them', async () => {
    const { router } = open()
    await firstRows()
    await userEvent.type(screen.getByLabelText('Min amount'), '12abc')
    expect(await screen.findByText(/Use a plain amount/)).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 450))
    expect(router.state.location.search).not.toContain('min=')

    await userEvent.clear(screen.getByLabelText('Min amount'))
    await userEvent.type(screen.getByLabelText('Min amount'), '10')
    await userEvent.type(screen.getByLabelText('Max amount'), '5')
    expect(await screen.findByText('Max is below min')).toBeInTheDocument()
  })

  it('restores a shared view from the URL', async () => {
    open('/transactions?q=bakery&dir=outflow&min=-100&transfers=hide')
    expect(await screen.findByRole('searchbox', { name: 'Search descriptions' })).toHaveValue(
      'bakery',
    )
    expect(screen.getByLabelText('Direction')).toHaveValue('outflow')
    expect(screen.getByLabelText('Min amount')).toHaveValue('-100')
    expect(screen.getByRole('checkbox', { name: 'Hide transfers' })).toBeChecked()
    await firstRows()
    const q = lastList()?.searchParams
    expect(q?.get('q')).toBe('bakery')
    expect(q?.get('direction')).toBe('outflow')
    expect(q?.get('min_amount')).toBe('-100')
  })

  it('selects an account and clears everything with Clear all', async () => {
    const { router } = open()
    const rail = await screen.findByRole('complementary', { name: 'Accounts and filters' })
    await userEvent.click(await within(rail).findByRole('checkbox', { name: /Sample travel card/ }))
    await waitFor(() => {
      expect(router.state.location.search).toBe(`?account=${LEDGER_IDS.card}`)
    })
    await waitFor(() => {
      expect(lastList()?.searchParams.getAll('account_id')).toEqual([LEDGER_IDS.card])
    })
    expect(await screen.findByText('Fernwood Bookshop')).toBeInTheDocument()
    expect(screen.queryByText('Payroll deposit Example Co')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    await waitFor(() => {
      expect(router.state.location.search).toBe('')
    })
    expect(await screen.findByText('Payroll deposit Example Co')).toBeInTheDocument()
  })

  it('loads the next page on demand and appends it', async () => {
    open()
    await firstRows()
    expect(rowsIn()).toHaveLength(50)
    expect(listRequests()[0]?.searchParams.get('cursor')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => {
      expect(rowsIn()).toHaveLength(69)
    })
    expect(listRequests().at(-1)?.searchParams.get('cursor')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('says how to import when nothing has been imported', async () => {
    open('/transactions', { empty: true })
    expect(await screen.findByText('No transactions yet.')).toBeInTheDocument()
    expect(screen.getByText(/Import a statement PDF/)).toBeInTheDocument()
  })

  it('offers Clear all when filters match nothing', async () => {
    const { router } = open('/transactions?q=zzzz-nothing')
    expect(await screen.findByText('No transactions match these filters.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear all filters' }))
    await waitFor(() => {
      expect(router.state.location.search).toBe('')
    })
    expect((await firstRows()).length).toBeGreaterThan(0)
  })

  it('shows an error with Retry that recovers', async () => {
    let fail = true
    server.use(
      http.get('*/api/transactions', () =>
        fail
          ? HttpResponse.json(
              { error: { code: 'not_found', message: 'Try again.' } },
              { status: 500 },
            )
          : HttpResponse.json({ items: [], next_cursor: null }),
      ),
    )
    open()
    expect(await screen.findByText("Couldn't load transactions.")).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      expect(screen.queryByText("Couldn't load transactions.")).not.toBeInTheDocument()
    })
  })
})

describe('Transactions page, 375px', () => {
  it('moves accounts and filters into sheets and keeps rows as a list', async () => {
    mockViewport(375)
    const { router } = open()
    await firstRows()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()

    const accounts = screen.getByRole('button', { name: /Accounts/ })
    const filters = screen.getByRole('button', { name: /Filters/ })
    // 44px touch targets below md (button size default is h-11).
    expect(accounts.className).toContain('h-11')
    expect(filters.className).toContain('h-11')

    await userEvent.click(filters)
    const dialog = await screen.findByRole('dialog', { name: 'Filters' })
    await userEvent.selectOptions(within(dialog).getByLabelText('Direction'), 'inflow')
    await waitFor(() => {
      expect(router.state.location.search).toContain('dir=inflow')
    })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    expect(screen.getByRole('button', { name: /Filters/ })).toHaveTextContent('1')

    await userEvent.click(screen.getByRole('button', { name: /Accounts/ }))
    const sheet = await screen.findByRole('dialog', { name: 'Accounts' })
    expect(within(sheet).getByRole('checkbox', { name: /Everyday checking/ })).toBeInTheDocument()
  })
})
