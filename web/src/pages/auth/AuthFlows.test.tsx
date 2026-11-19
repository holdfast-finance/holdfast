import { startRegistration } from '@simplewebauthn/browser'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MOCK_SETUP_TOKEN, db, seedHousehold } from '@/mocks/db'
import { renderApp, renderSignedIn } from '@/test/render'
import { fakeRegistration } from '@/test/webauthn'

vi.mock('@simplewebauthn/browser', () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}))

const register = vi.mocked(startRegistration)

beforeEach(() => {
  register.mockReset()
  register.mockResolvedValue(fakeRegistration('cred-new'))
})

describe('setup can resume', () => {
  it('sends a signed-in owner with no passkey back to the passkey step', async () => {
    // Reloaded during setup after the household was created, before the passkey.
    renderApp('/setup', { seed: { signedIn: true, passkeys: 0 } })
    expect(await screen.findByRole('heading', { name: 'Create your passkey' })).toBeVisible()
    expect(screen.getByText('Step 3 of 4')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Create passkey' }))
    expect(await screen.findByRole('heading', { name: 'Add a second person' })).toBeVisible()
    expect(db.users[0]?.credentials).toHaveLength(1)
  })

  it('keeps reporting needs_setup until the owner has a credential', async () => {
    seedHousehold({ passkeys: 0 })
    const res = await fetch(new URL('/api/setup/status', window.location.origin))
    expect(await res.json()).toEqual({ needs_setup: true })
    seedHousehold({ passkeys: 1 })
    const done = await fetch(new URL('/api/setup/status', window.location.origin))
    expect(await done.json()).toEqual({ needs_setup: false })
  })

  it('sends a signed-in user who already has a passkey into the app', async () => {
    const { router } = renderApp('/setup', { seed: { signedIn: true, passkeys: 1 } })
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
  })
})

describe('focus follows the wizard', () => {
  it('moves focus to the new step title and announces the step', async () => {
    renderApp('/setup')
    // The step counter is a status region, so the change is announced.
    expect(await screen.findByText('Step 1 of 4')).toHaveAttribute('role', 'status')
    // The first render never steals focus.
    expect(screen.getByRole('heading', { name: 'Claim this server' })).not.toHaveFocus()

    await userEvent.type(screen.getByLabelText('Setup token'), MOCK_SETUP_TOKEN)
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const title = await screen.findByRole('heading', { name: 'Your household' })
    expect(title).toHaveFocus()
    expect(screen.getByText('Step 2 of 4')).toHaveAttribute('role', 'status')
  })

  it('shows the setup log command as code', async () => {
    renderApp('/setup')
    const code = await screen.findByText('docker compose logs api')
    expect(code.tagName.toLowerCase()).toBe('code')
  })
})

describe('passkeys need a secure context', () => {
  afterEach(() => {
    vi.stubGlobal('isSecureContext', true)
  })

  it('explains HTTPS on the login page and disables the passkey button', async () => {
    vi.stubGlobal('isSecureContext', false)
    seedHousehold()
    renderApp('/login')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Passkeys need HTTPS. Open Holdfast at its https:// address.',
    )
    expect(screen.getByRole('button', { name: 'Sign in with passkey' })).toBeDisabled()
    // The password + code fallback still works over plain http.
    expect(screen.getByRole('button', { name: 'Use password + code instead' })).toBeEnabled()
  })

  it('explains HTTPS on the setup passkey step', async () => {
    vi.stubGlobal('isSecureContext', false)
    renderApp('/setup', { seed: { signedIn: true, passkeys: 0 } })
    expect(await screen.findByRole('alert')).toHaveTextContent(/Passkeys need HTTPS/)
    expect(screen.getByRole('button', { name: 'Create passkey' })).toBeDisabled()
  })
})

describe('login fallback toggle', () => {
  it('stays mounted and reports its state', async () => {
    seedHousehold()
    renderApp('/login')
    const toggle = await screen.findByRole('button', { name: 'Use password + code instead' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    expect(toggle).toBeInTheDocument()
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveAttribute('aria-controls', 'password-fallback')
    expect(document.getElementById('password-fallback')).not.toBeNull()
    expect(screen.getByLabelText('Email')).toBeVisible()
  })
})

describe('invite link in the fragment', () => {
  function seedInvite() {
    seedHousehold({ signedIn: false })
    const person = {
      id: 'person-invitee',
      display_name: 'Sam Example',
      filing_role: 'spouse' as const,
    }
    db.people.push(person)
    db.invites.push({ token: 'tok123', personId: person.id, email: 'sam@example.com', used: false })
  }

  it('accepts /invite#<token>', async () => {
    seedInvite()
    renderApp('/invite#tok123')
    await userEvent.click(await screen.findByRole('button', { name: 'Accept invite' }))
    const title = await screen.findByRole('heading', { name: 'Welcome, Sam Example' })
    expect(title).toHaveFocus()
    expect(screen.getByText("You're in Example Household. Add a passkey to finish.")).toBeVisible()
    expect(db.invites[0]?.used).toBe(true)
  })

  it('still accepts the older /invite/<token> links', async () => {
    seedInvite()
    renderApp('/invite/tok123')
    await userEvent.click(await screen.findByRole('button', { name: 'Accept invite' }))
    expect(await screen.findByRole('heading', { name: 'Welcome, Sam Example' })).toBeVisible()
  })

  it('disables Accept when there is no token at all', async () => {
    seedInvite()
    renderApp('/invite')
    expect(await screen.findByRole('button', { name: 'Accept invite' })).toBeDisabled()
  })

  it('gives the sign-in link a 44px target', async () => {
    seedInvite()
    renderApp('/invite#tok123')
    expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveClass('min-h-11')
  })
})

describe('first passkey is exempt from step-up', () => {
  it('adds the first passkey from Security without a prompt, even with a stale session', async () => {
    renderSignedIn('/settings/security', { passkeys: 0, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Add passkey' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Create passkey' }))
    await waitFor(() => {
      expect(db.users[0]?.credentials).toHaveLength(1)
    })
    expect(screen.queryByRole('dialog', { name: "Confirm it's you" })).not.toBeInTheDocument()
  })
})
