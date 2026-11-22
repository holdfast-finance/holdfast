import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { seedHousehold } from '@/mocks/db'
import { CATEGORY_IDS, seedLedger } from '@/mocks/ledger'
import { server } from '@/mocks/node'
import { mockViewport } from '@/test/matchMedia'
import { renderApp } from '@/test/render'

function open(path = '/transactions') {
  seedHousehold({ signedIn: true })
  seedLedger()
  return renderApp(path)
}

/** Several rows share a merchant; the newest (the hand-written fixture row) comes first. */
const row = async (name: RegExp) =>
  (await screen.findAllByRole('button', { name }))[0] as HTMLElement

async function openSheet(name: RegExp) {
  await userEvent.click(await row(name))
  return screen.findByRole('dialog')
}

describe('focus, announcements and edge states', () => {
  beforeEach(() => {
    mockViewport(375)
  })

  it('returns focus to the row after Escape', async () => {
    open()
    const opener = await row(/Lantern Coffee/)
    await openSheet(/Lantern Coffee/)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(opener).toHaveFocus()
    })
  })

  it('returns focus to the row after saving, and announces it', async () => {
    open()
    const opener = await row(/Lantern Coffee/)
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Groceries' }))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Save category' }))
    await waitFor(() => {
      expect(opener).toHaveFocus()
    })
    await waitFor(() => {
      expect(screen.getByText('Groceries set for Lantern Coffee')).toBeInTheDocument()
    })
  })

  it('falls back to the list when the row has left the view', async () => {
    open('/transactions?uncategorized=1')
    await row(/Lantern Coffee/)
    const before = screen.getAllByRole('button', { name: /Lantern Coffee/ }).length
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Fun' }))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Save category' }))
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /Lantern Coffee/ })).toHaveLength(before - 1)
    })
    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Transaction list' })).toHaveFocus()
    })
  })

  it('words a failed hand-back plainly', async () => {
    server.use(
      http.post('/api/transactions/:id/category/release', () =>
        HttpResponse.json({ error: { code: 'conflict', message: 'Nope.' } }, { status: 409 }),
      ),
    )
    open()
    const sheet = await openSheet(/Harbor Grocers/)
    await userEvent.click(within(sheet).getByRole('button', { name: 'Hand back to automation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Couldn't hand Harbor Grocers back to automation. It's back how it was.",
    )
  })

  it('shows read-only uncategorised rows without the warning chip', async () => {
    open()
    const fern = await row(/Fernwood Bookshop/)
    expect(within(fern).getByText('Uncategorised')).toBeInTheDocument()
    expect(within(fern).queryByText('Needs category')).not.toBeInTheDocument()
    expect(fern).toHaveAttribute('aria-haspopup', 'dialog')
  })

  it('clears a selection that a search hides', async () => {
    open()
    const sheet = await openSheet(/Lantern Coffee/)
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Groceries' }))
    const save = within(sheet).getByRole('button', { name: 'Save category' })
    expect(save).toBeEnabled()
    await userEvent.type(within(sheet).getByRole('searchbox', { name: 'Search categories' }), 'zzz')
    expect(within(sheet).getByRole('status')).toHaveTextContent('No category matches that.')
    expect(save).toBeDisabled()
  })

  it('says when categories fail to load, and offers a retry', async () => {
    server.use(
      http.get('/api/categories', () =>
        HttpResponse.json({ error: { code: 'internal', message: 'Boom' } }, { status: 500 }),
      ),
    )
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent(/Couldn't load categories/)
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('does not count the needs-category view in the Filters badge', async () => {
    open('/transactions?uncategorized=1')
    await row(/Lantern Coffee/)
    expect(screen.getByRole('button', { name: /^Filters/ })).toHaveTextContent(/^Filters$/)
  })

  it('leaves the needs-category view when a category is picked in the filters', async () => {
    const { router } = open('/transactions?uncategorized=1')
    await row(/Lantern Coffee/)
    await userEvent.click(screen.getByRole('button', { name: /^Filters/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(await within(dialog).findByLabelText('Category'), 'Groceries')
    await waitFor(() => {
      expect(router.state.location.search).not.toContain('uncategorized')
      expect(router.state.location.search).toContain(`category=${CATEGORY_IDS.groceries}`)
    })
  })
})
