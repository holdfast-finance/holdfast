import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { seedHousehold } from '@/mocks/db'
import { CATEGORY_IDS, seedLedger } from '@/mocks/ledger'
import { server } from '@/mocks/node'
import { mockViewport } from '@/test/matchMedia'
import { renderApp } from '@/test/render'

/** Every request the page made, so tests can assert what the API was asked. */
let requests: { method: string; url: URL; body: string }[] = []
beforeEach(() => {
  requests = []
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url)
    if (url.pathname.startsWith('/api/transactions')) {
      void request
        .clone()
        .text()
        .then((body) => {
          requests.push({ method: request.method, url, body })
        })
    }
  })
})
afterEach(() => {
  server.events.removeAllListeners()
})

function open(path = '/transactions') {
  seedHousehold({ signedIn: true })
  seedLedger()
  return renderApp(path)
}

/** Several rows can share a merchant; the newest (the hand-written fixture row) comes first. */
const row = async (name: RegExp) =>
  (await screen.findAllByRole('button', { name }))[0] as HTMLElement
const firstRow = (name: RegExp) => screen.getAllByRole('button', { name })[0] as HTMLElement
const lastList = () => requests.filter((r) => r.url.pathname === '/api/transactions').at(-1)?.url

async function openSheet(name: RegExp) {
  await userEvent.click(await row(name))
  return screen.findByRole('dialog')
}

describe('categorising transactions', () => {
  beforeEach(() => {
    mockViewport(375)
  })

  it('shows each row category, where it came from, transfers and uncategorised rows', async () => {
    open()
    const payroll = await row(/Payroll deposit/)
    expect(within(payroll).getByText('Paycheck')).toBeInTheDocument()
    expect(within(payroll).getByText(/set by rule/)).toBeInTheDocument()
    const harbor = await row(/Harbor Grocers/)
    expect(within(harbor).getByText(/set by you/)).toBeInTheDocument()
    expect(within(await row(/Lantern Coffee/)).getByText('Needs category')).toBeInTheDocument()
    // Rows from the bank carry no "you" or "rule" mark.
    const bank = await row(/Brightside Bakery/)
    expect(within(bank).getByText('Dining out')).toBeInTheDocument()
    expect(within(bank).queryByText(/set by/)).not.toBeInTheDocument()
  })

  it('sets a category from the sheet and shows it on the row', async () => {
    open()
    const sheet = await openSheet(/Lantern Coffee/)
    // Hand back is for rows the person categorised; this one has no category yet.
    expect(within(sheet).queryByRole('button', { name: 'Hand back to automation' })).toBeNull()
    const save = within(sheet).getByRole('button', { name: 'Save category' })
    expect(save).toBeDisabled()
    // Hidden categories aren't offered.
    expect(within(sheet).queryByRole('radio', { name: 'Retired category' })).toBeNull()
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Dining out' }))
    await userEvent.click(save)

    await waitFor(() => {
      expect(firstRow(/Lantern Coffee/)).toHaveTextContent('Dining out')
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    const put = requests.find((r) => r.method === 'PUT')
    expect(put?.url.pathname).toMatch(/\/category$/)
    expect(JSON.parse(put?.body ?? '{}')).toEqual({ category_id: CATEGORY_IDS.dining })
  })

  it('narrows the picker with a search', async () => {
    open()
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.type(
      within(sheet).getByRole('searchbox', { name: 'Search categories' }),
      'hard',
    )
    expect(within(sheet).getByRole('radio', { name: 'Hardware' })).toBeInTheDocument()
    expect(within(sheet).queryByRole('radio', { name: 'Groceries' })).toBeNull()
  })

  it('hands a category the person set back to automation', async () => {
    open()
    const sheet = await openSheet(/Harbor Grocers/)
    expect(within(sheet).getByText('Source')).toBeInTheDocument()
    expect(within(sheet).getByText('You')).toBeInTheDocument()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Hand back to automation' }))
    await waitFor(() => {
      expect(requests.some((r) => r.method === 'POST' && r.url.pathname.endsWith('/release'))).toBe(
        true,
      )
    })
    // The category stays; only the "you" mark goes.
    await waitFor(() => {
      const harbor = firstRow(/Harbor Grocers/)
      expect(harbor).toHaveTextContent('Groceries')
      expect(harbor).not.toHaveTextContent(/set by you/)
    })
  })

  it('gives read-only rows the reason and no actions', async () => {
    open()
    const sheet = await openSheet(/Fernwood Bookshop/)
    expect(within(sheet).getByRole('note')).toHaveTextContent(/belongs to someone else/)
    expect(within(sheet).queryByRole('button', { name: 'Save category' })).toBeNull()
    expect(within(sheet).queryByRole('button', { name: 'Hand back to automation' })).toBeNull()
    expect(within(sheet).queryAllByRole('radio')).toHaveLength(0)
  })

  it('puts the row back and says so when the server refuses', async () => {
    server.use(
      http.put('/api/transactions/:id/category', () =>
        HttpResponse.json(
          { error: { code: 'conflict', message: 'That transaction changed. Try again.' } },
          { status: 409 },
        ),
      ),
    )
    open()
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Fun' }))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Save category' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      /Couldn't save the category for Lantern Coffee\. It's back how it was/,
    )
    expect(alert).toHaveTextContent('That transaction changed. Try again.')
    await waitFor(() => {
      expect(firstRow(/Lantern Coffee/)).toHaveTextContent('Needs category')
    })
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss message' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('works through only the rows that need a category', async () => {
    const { router } = open()
    await row(/Lantern Coffee/)
    await userEvent.click(screen.getByRole('button', { name: 'Needs category' }))
    await waitFor(() => {
      expect(lastList()?.searchParams.get('uncategorized')).toBe('true')
    })
    expect(router.state.location.search).toContain('uncategorized=1')
    expect(
      screen.getByRole('button', { name: 'Needs category', pressed: true }),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Payroll deposit/ })).not.toBeInTheDocument()
    })
    expect(await row(/Lantern Coffee/)).toBeInTheDocument()

    // Categorising a row takes it out of the view once the list refreshes.
    const before = screen.getAllByRole('button', { name: /Lantern Coffee/ }).length
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Fun' }))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Save category' }))
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /Lantern Coffee/ })).toHaveLength(before - 1)
    })
  })
})

describe('category filters', () => {
  beforeEach(() => {
    mockViewport(1280)
  })

  it('filters by a group, sending the group and its children', async () => {
    const { router } = open()
    const rail = await screen.findByRole('complementary', { name: 'Accounts and filters' })
    await userEvent.selectOptions(await within(rail).findByLabelText('Category'), 'Food')
    await waitFor(() => {
      expect(lastList()?.searchParams.getAll('category_id').sort()).toEqual(
        [CATEGORY_IDS.food, CATEGORY_IDS.groceries, CATEGORY_IDS.dining].sort(),
      )
    })
    expect(router.state.location.search).toContain(`category=${CATEGORY_IDS.food}`)
    expect(await row(/Harbor Grocers/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Payroll deposit/ })).not.toBeInTheDocument()
    })
  })

  it('filters by who set the category', async () => {
    open()
    const rail = await screen.findByRole('complementary', { name: 'Accounts and filters' })
    await userEvent.selectOptions(await within(rail).findByLabelText('Set by'), 'A rule')
    await waitFor(() => {
      expect(lastList()?.searchParams.get('category_source')).toBe('rule')
    })
    expect(await row(/Payroll deposit/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Harbor Grocers/ })).not.toBeInTheDocument()
    })
  })

  it('shows only transfers, and refuses hide and only together', async () => {
    open()
    const rail = await screen.findByRole('complementary', { name: 'Accounts and filters' })
    await userEvent.click(await within(rail).findByRole('checkbox', { name: 'Only transfers' }))
    await waitFor(() => {
      expect(lastList()?.searchParams.get('transfer')).toBe('only')
    })
    await userEvent.click(within(rail).getByRole('checkbox', { name: 'Hide transfers' }))
    expect(await within(rail).findByText(/not both/)).toBeInTheDocument()
  })

  it('keeps the category and source out of the needs-category view', async () => {
    open(`/transactions?uncategorized=1&category=${CATEGORY_IDS.food}&source=user`)
    await row(/Lantern Coffee/)
    const params = lastList()?.searchParams
    expect(params?.get('uncategorized')).toBe('true')
    expect(params?.has('category_id')).toBe(false)
    expect(params?.has('category_source')).toBe(false)
  })
})
