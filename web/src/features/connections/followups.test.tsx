import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useLogout } from '@/api/auth'
import { ApiError } from '@/api/client'
import { queryKeys } from '@/app/queryClient'
import { abandonErrorMessage, resolveErrorMessage } from '@/features/connections/copy'
import { OAUTH_STORAGE_KEY } from '@/features/connections/oauthSession'
import { db, expireReauth, seedHousehold } from '@/mocks/db'
import { server } from '@/mocks/node'
import { useReauthStore } from '@/stores/reauth'
import { createTestQueryClient, renderApp } from '@/test/render'

interface LinkConfig {
  token: string
  onSuccess: (publicToken: string) => void
  onExit: () => void
  onEvent?: (eventName: string) => void
  receivedRedirectUri?: string
}
let link: LinkConfig | null = null
let destroyed = 0

beforeEach(() => {
  link = null
  destroyed = 0
  ;(window as unknown as { Plaid?: unknown }).Plaid = {
    create: (config: LinkConfig) => {
      link = config
      return {
        open: () => undefined,
        destroy: () => {
          destroyed += 1
        },
      }
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

describe('P9b follow-ups', () => {
  it('logout clears a stored OAuth Link token', async () => {
    seedHousehold({ signedIn: true })
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    const { result } = renderHook(() => useLogout(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
      ),
    })
    await act(async () => {
      await result.current.mutateAsync()
    })
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
  })

  it('shows fixed copy for an unmapped resolve code, never the server message', () => {
    const leaky = new ApiError(500, 'something_new', 'Internal SECRET_X detail.')
    expect(resolveErrorMessage(leaky)).not.toContain('SECRET_X')
    expect(resolveErrorMessage(new ApiError(502, 'plaid_unavailable', 'x'))).toMatch(/reached/)
    expect(resolveErrorMessage(new ApiError(502, 'plaid_error', 'SECRET_X'))).toBe(
      'Plaid refused the request. Nothing was changed. Try again later.',
    )
  })

  it("clears a stale resolve error from the sheet's own resolve when it reopens", async () => {
    seedHousehold({ signedIn: true })
    seedReserved(true)
    server.use(
      http.post('*/api/connections/plaid/reservations/:id/resolve', () =>
        HttpResponse.json(
          { error: { code: 'plaid_unavailable', message: 'down' } },
          { status: 502 },
        ),
      ),
    )
    renderApp('/settings/connections')
    await userEvent.click(await screen.findByRole('button', { name: 'I did not finish' }))
    let dialog = await screen.findByRole('dialog')
    await userEvent.type(
      within(dialog).getByLabelText('Confirmation phrase'),
      'I did not finish this link',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Release this slot' }))
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Check with Plaid' }))
    expect(await within(dialog).findByText(/reached/)).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    await userEvent.click(screen.getByRole('button', { name: 'I did not finish' }))
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('re-saves the token when a second OPEN_OAUTH hop happens on the return page', async () => {
    seedHousehold({ signedIn: true })
    seedReserved()
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    window.sessionStorage.removeItem(OAUTH_STORAGE_KEY)
    act(() => {
      link?.onEvent?.('OPEN_OAUTH')
    })
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toContain('link-mock-token-0001')
  })

  it('exchanges without a step-up prompt even when reauth is stale', async () => {
    seedHousehold({ signedIn: true })
    seedReserved()
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    const { router } = renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    expireReauth()
    act(() => {
      link?.onSuccess('public-sandbox-mock')
    })
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/settings/connections')
    })
    expect(useReauthStore.getState().pending).toBeNull()
    expect(db.connections.connections).toHaveLength(1)
    await waitFor(() => {
      expect(document.getElementById('page-title')).toHaveFocus()
    })
  })

  it('destroys Link and stops re-saving the token once the page unmounts', async () => {
    seedHousehold({ signedIn: true })
    seedReserved()
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    const { router } = renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    await act(async () => {
      await router.navigate('/settings/security')
    })
    await waitFor(() => {
      expect(destroyed).toBeGreaterThan(0)
    })
    window.sessionStorage.removeItem(OAUTH_STORAGE_KEY)
    act(() => {
      link?.onEvent?.('OPEN_OAUTH')
    })
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
  })

  it('says it is confirming you while a step-up prompt is open', async () => {
    seedHousehold({ signedIn: true })
    seedReserved()
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    renderApp('/plaid/oauth-return?oauth_state_id=abc123')
    expect(await screen.findByText('Opening Plaid Link again…')).toBeVisible()
    let request: Promise<void> = Promise.resolve()
    act(() => {
      request = useReauthStore.getState().request()
    })
    expect(await screen.findByText("Confirming it's you…")).toBeVisible()
    act(() => {
      useReauthStore.getState().cancel()
    })
    await expect(request).rejects.toThrow()
  })
})

describe('Session end', () => {
  it('logout cancels a pending step-up prompt', async () => {
    seedHousehold({ signedIn: true })
    const settled = expect(useReauthStore.getState().request()).rejects.toThrow()
    const { result } = renderHook(() => useLogout(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
      ),
    })
    await act(async () => {
      await result.current.mutateAsync()
    })
    await settled
    expect(useReauthStore.getState().pending).toBeNull()
  })

  it('a 401 clears the OAuth token and cancels a pending step-up prompt', async () => {
    const client = createTestQueryClient()
    client.setQueryData(queryKeys.me, { user: { id: 'x' } })
    window.sessionStorage.setItem(
      OAUTH_STORAGE_KEY,
      JSON.stringify({ token: 'link-mock-token-0001', reservationId: RESERVATION }),
    )
    const settled = expect(useReauthStore.getState().request()).rejects.toThrow()
    await client
      .query({
        queryKey: ['household', 'gone'],
        queryFn: () => Promise.reject(new ApiError(401, 'unauthenticated', 'no session')),
      })
      .catch(() => undefined)
    await settled
    expect(window.sessionStorage.getItem(OAUTH_STORAGE_KEY)).toBeNull()
    expect(useReauthStore.getState().pending).toBeNull()
  })
})
