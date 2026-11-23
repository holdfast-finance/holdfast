import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ApiError } from '@/api/client'
import { abandonErrorMessage } from '@/features/connections/copy'
import { OAUTH_STORAGE_KEY } from '@/features/connections/oauthSession'
import { db, seedHousehold } from '@/mocks/db'
import { server } from '@/mocks/node'
import { renderApp } from '@/test/render'

interface LinkConfig {
  token: string
  onSuccess: (publicToken: string) => void
  onExit: () => void
  onEvent?: (eventName: string) => void
  receivedRedirectUri?: string
}
let link: LinkConfig | null = null

beforeEach(() => {
  link = null
  ;(window as unknown as { Plaid?: unknown }).Plaid = {
    create: (config: LinkConfig) => {
      link = config
      return { open: () => undefined, destroy: () => undefined }
    },
  }
})
afterEach(() => {
  delete (window as unknown as { Plaid?: unknown }).Plaid
  window.sessionStorage.clear()
  window.history.pushState({}, '', '/')
})

const RESERVATION = '55555555-5555-4555-8555-555555555555'

function seedReserved(conflict = false) {
  db.connections.reservations = [
    {
      id: RESERVATION,
      state: 'reserved',
      env: 'sandbox',
      created_at: '2026-11-21T10:00:00Z',
      outcome: 'open',
    },
  ]
  db.connections.abandonConflict = conflict
}

async function openLinkSheet() {
  await userEvent.click(await screen.findByRole('button', { name: 'Run check' }))
  const button = await screen.findByRole('button', { name: 'Link a bank' })
  await waitFor(() => {
    expect(button).toBeEnabled()
  })
  await userEvent.click(button)
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText(/Slots Holdfast has counted/)
  return dialog
}

async function startLink() {
  await openLinkSheet()
  await userEvent.click(await screen.findByRole('button', { name: 'Open Plaid Link' }))
  await waitFor(() => {
    expect(link).not.toBeNull()
  })
}

describe('Link focus and cache hygiene', () => {
  it('does not hand focus back to the page while Plaid Link is opening', async () => {
    seedHousehold({ signedIn: true })
    renderApp('/settings/connections')
    await startLink()
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Link a bank' })).not.toHaveFocus()
  })

  it('keeps the exchange, and so the public token, out of the mutation cache', async () => {
    seedHousehold({ signedIn: true })
    const { client } = renderApp('/settings/connections')
    await startLink()
    act(() => {
      link?.onSuccess('public-sandbox-mock')
    })
    expect(await screen.findByText('Linked.')).toBeVisible()
    await waitFor(() => {
      const held = client
        .getMutationCache()
        .getAll()
        .filter((m) => JSON.stringify(m.state.variables ?? null).includes('public-sandbox-mock'))
      expect(held).toHaveLength(0)
    })
  })
})

describe('Release sheet', () => {
  it('focuses the phrase, shows a failed check, and makes checking the primary action after a 409', async () => {
    seedHousehold({ signedIn: true })
    seedReserved(true)
    server.use(
      http.post('*/api/connections/plaid/reservations/:id/resolve', () =>
        HttpResponse.json(
          { error: { code: 'plaid_unavailable', message: 'Plaid could not be reached.' } },
          { status: 502 },
        ),
      ),
    )
    renderApp('/settings/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'I did not finish' }))
    const dialog = await screen.findByRole('dialog')
    const phrase = within(dialog).getByLabelText('Confirmation phrase')
    expect(phrase).toHaveFocus()
    await userEvent.type(phrase, 'I did not finish this link')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Release this slot' }))
    const check = await within(dialog).findByRole('button', { name: 'Check with Plaid' })
    expect(check).toHaveAttribute('data-variant', 'default')
    expect(within(dialog).getByRole('button', { name: 'Release this slot' })).not.toHaveAttribute(
      'data-variant',
      'default',
    )
    await userEvent.click(check)
    expect(await within(dialog).findByText(/couldn't be reached/i)).toBeVisible()
  })

  it('maps plaid_error and any unknown code to fixed copy, never the server message', () => {
    const leaky = (code: string) => new ApiError(409, code, 'Plaid refused the request (SECRET_X).')
    expect(abandonErrorMessage(leaky('plaid_error'))).not.toContain('SECRET_X')
    expect(abandonErrorMessage(leaky('plaid_error'))).toMatch(/Plaid refused/)
    expect(abandonErrorMessage(leaky('something_new'))).not.toContain('SECRET_X')
  })
})

describe('OAuth resume', () => {
  it('keeps the Link token in sessionStorage only while a bank OAuth is open', async () => {
    seedHousehold({ signedIn: true })
    renderApp('/settings/connections')
    await startLink()
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
    act(() => {
      link?.onEvent?.('OPEN_OAUTH')
    })
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toContain('link-mock-token')
    act(() => {
      link?.onExit()
    })
    await screen.findByText(/keeping the slot held/)
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
  })

  it('clears it on success too', async () => {
    seedHousehold({ signedIn: true })
    renderApp('/settings/connections')
    await startLink()
    act(() => {
      link?.onEvent?.('OPEN_OAUTH')
      link?.onSuccess('public-sandbox-mock')
    })
    expect(await screen.findByText('Linked.')).toBeVisible()
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
  })

  it('re-opens Link on the return page, strips the params, and exchanges', async () => {
    seedHousehold({ signedIn: true })
    seedReserved()
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    window.history.pushState({}, '', '/plaid/oauth-return?oauth_state_id=abc123')
    const { router } = renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    expect(link?.token).toBe('link-mock-token-0001')
    expect(link?.receivedRedirectUri).toContain('oauth_state_id=abc123')
    expect(router.state.location.search).toBe('')
    act(() => {
      link?.onSuccess('public-sandbox-mock')
    })
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/settings/connections')
    })
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
    expect(db.connections.connections).toHaveLength(1)
  })

  it('sends the user back to start again when the token is gone', async () => {
    seedHousehold({ signedIn: true })
    renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    expect(await screen.findByText(/start the link again/i)).toBeVisible()
    expect(link).toBeNull()
    expect(screen.getByRole('link', { name: 'Back to Connections' })).toBeVisible()
  })
})
