import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockViewport } from '@/test/matchMedia'
import { renderSignedIn } from '@/test/render'

const COPY = 'Add a passkey so you can sign in again.'

describe('<MissingPasskeyBanner>', () => {
  it('shows across the shell when the user has zero passkeys', async () => {
    renderSignedIn('/', { passkeys: 0 })
    expect(await screen.findByText(COPY)).toBeInTheDocument()
  })

  it('shows on desktop too', async () => {
    mockViewport(1440)
    renderSignedIn('/transactions', { passkeys: 0 })
    expect(await screen.findByText(COPY)).toBeInTheDocument()
  })

  it('stays hidden when the user has a passkey', async () => {
    renderSignedIn('/', { passkeys: 1 })
    await screen.findByTestId('mobile-tabbar')
    // Let the passkeys query settle before asserting absence.
    await waitFor(() => {
      expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })
  })

  it('links to Settings → Security, where it gets out of the way', async () => {
    const { router } = renderSignedIn('/', { passkeys: 0 })
    await userEvent.click(await screen.findByRole('link', { name: 'Add a passkey' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/settings/security')
    })
    await screen.findByRole('heading', { name: 'Passkeys' })
    expect(screen.queryByText(COPY)).not.toBeInTheDocument()
  })

  it('dismisses for the rest of the tab session', async () => {
    const first = renderSignedIn('/', { passkeys: 0 })
    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss for this session' }))
    expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    expect(window.sessionStorage.getItem('holdfast.missingPasskeyBanner.dismissed')).toBe('1')

    // A reload in the same tab keeps it dismissed.
    first.unmount()
    renderSignedIn('/', { passkeys: 0 })
    await screen.findByTestId('mobile-tabbar')
    await waitFor(() => {
      expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })
  })
})
