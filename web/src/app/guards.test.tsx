import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { db, seedHousehold } from '@/mocks/db'
import { renderApp, renderSignedIn } from '@/test/render'

describe('route guard', () => {
  it('sends a fresh instance to /setup', async () => {
    const { router } = renderApp('/transactions')
    expect(await screen.findByRole('heading', { name: 'Claim this server' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/setup')
  })

  it('sends a signed-out user to /login and remembers where they were going', async () => {
    seedHousehold()
    const { router } = renderApp('/budgets')
    expect(await screen.findByRole('button', { name: 'Sign in with passkey' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.state).toEqual({ from: '/budgets' })
  })

  it('lets a signed-in user through to the app', async () => {
    const { router } = renderSignedIn('/budgets')
    expect(await screen.findByRole('heading', { level: 1, name: 'Budgets' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/budgets')
  })

  it('bounces a signed-in user away from /login', async () => {
    const { router } = renderSignedIn('/login')
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
  })

  it('sends /setup to /login once the instance is claimed', async () => {
    seedHousehold()
    const { router } = renderApp('/setup')
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login')
    })
  })

  it('drops to /login when the session dies mid-use', async () => {
    const { router } = renderSignedIn('/settings/household')
    expect(await screen.findByRole('heading', { name: 'People' })).toBeVisible()
    db.session = null
    // Any API call now answers 401; the query client clears state and the guard redirects.
    await userEvent.click(screen.getByRole('link', { name: 'Security' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login')
    })
  })

  it('signs out from Settings', async () => {
    const { router } = renderSignedIn('/settings')
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login')
    })
    expect(db.session).toBeNull()
    expect(await screen.findByRole('button', { name: 'Sign in with passkey' })).toBeVisible()
  })
})
