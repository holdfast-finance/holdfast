import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MOCK_TOTP_CODE, db } from '@/mocks/db'
import { renderSignedIn } from '@/test/render'
import { fakeAssertion, fakeRegistration } from '@/test/webauthn'

vi.mock('@simplewebauthn/browser', () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}))

const authenticate = vi.mocked(startAuthentication)
const register = vi.mocked(startRegistration)

beforeEach(() => {
  authenticate.mockReset()
  register.mockReset()
})

function owner() {
  const user = db.users[0]
  if (!user) throw new Error('no seeded owner')
  return user
}

describe('Settings → Security: TOTP', () => {
  it('enables TOTP with a locally rendered QR and shows recovery codes once', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    renderSignedIn('/settings/security')

    await userEvent.click(await screen.findByRole('button', { name: 'Set up authenticator' }))

    // The QR is an inline SVG drawn on this page, not an <img> pointing at a QR service.
    const qr = await screen.findByRole('img', { name: 'QR code for your authenticator app' })
    expect(qr.tagName.toLowerCase()).toBe('svg')
    expect(qr.querySelector('path')?.getAttribute('d')).toMatch(/^M\d+ \d+h1v1h-1z/)
    expect(document.querySelector('img[src^="http"]')).toBeNull()
    expect(screen.getByTestId('totp-secret')).toHaveTextContent('JBSWY3DPEHPK3PXP')

    // A wrong code is rejected by the API.
    await userEvent.type(screen.getByLabelText('Code from your app'), '000000')
    await userEvent.click(screen.getByRole('button', { name: 'Turn on codes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That code did not match.')

    await userEvent.clear(screen.getByLabelText('Code from your app'))
    await userEvent.type(screen.getByLabelText('Code from your app'), MOCK_TOTP_CODE)
    await userEvent.click(screen.getByRole('button', { name: 'Turn on codes' }))

    const codes = within(await screen.findByRole('list', { name: 'Recovery codes' })).getAllByRole(
      'listitem',
    )
    expect(codes).toHaveLength(10)
    expect(codes.map((li) => li.textContent)).toEqual(owner().recoveryCodes)
    expect(owner().totpEnabled).toBe(true)
    // The form the user submitted is gone: focus lands on the codes' title.
    expect(screen.getByRole('heading', { name: 'Save your recovery codes' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Copy codes' }))
    expect(writeText).toHaveBeenCalledWith(owner().recoveryCodes.join('\n'))

    // Done waits for the "stored" checkbox.
    const done = screen.getByRole('button', { name: 'Done' })
    expect(done).toBeDisabled()
    await userEvent.click(
      screen.getByRole('checkbox', { name: "I've stored these somewhere safe" }),
    )
    expect(done).toBeEnabled()

    // Dismissed means gone: they are never shown again.
    await userEvent.click(done)
    expect(screen.queryByRole('list', { name: 'Recovery codes' })).not.toBeInTheDocument()
    expect(await screen.findByText('On')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Turn off' })).toBeEnabled()
    // Focus returns to the section instead of falling to <body>.
    expect(screen.getByRole('heading', { name: 'Authenticator codes' })).toHaveFocus()
  })

  it('validates the code format before calling the API', async () => {
    renderSignedIn('/settings/security')
    await userEvent.click(await screen.findByRole('button', { name: 'Set up authenticator' }))
    await userEvent.type(await screen.findByLabelText('Code from your app'), '12')
    await userEvent.click(screen.getByRole('button', { name: 'Turn on codes' }))
    expect(await screen.findByText('Enter the 6-digit code from your authenticator.')).toBeVisible()
    expect(owner().totpEnabled).toBe(false)
  })
})

describe('Settings → Security: passkeys', () => {
  it('blocks removing the only passkey when TOTP is off', async () => {
    renderSignedIn('/settings/security', { passkeys: 1 })
    const remove = await screen.findByRole('button', { name: 'Remove passkey Laptop' })
    expect(remove).toBeDisabled()
    expect(remove).toHaveAccessibleDescription(/Only one way in/)
  })

  it('allows removing the only passkey once TOTP is on', async () => {
    renderSignedIn('/settings/security', { passkeys: 1, totpEnabled: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Laptop' }))
    await waitFor(() => {
      expect(owner().credentials).toHaveLength(0)
    })
    expect(await screen.findByText(/No passkeys yet/)).toBeVisible()
  })

  it('removes one of several passkeys', async () => {
    renderSignedIn('/settings/security', { passkeys: 2 })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Phone 1' }))
    await waitFor(() => {
      expect(screen.queryByText('Phone 1')).not.toBeInTheDocument()
    })
    expect(owner().credentials.map((c) => c.nickname)).toEqual(['Laptop'])
    // Back to one passkey and no TOTP: the rule kicks in again.
    expect(screen.getByRole('button', { name: 'Remove passkey Laptop' })).toBeDisabled()
  })

  it('shows the API last_credential error if the server refuses', async () => {
    // The UI thinks TOTP is on (stale), the server knows better.
    renderSignedIn('/settings/security', { passkeys: 1, totpEnabled: true })
    await screen.findByRole('button', { name: 'Remove passkey Laptop' })
    owner().totpEnabled = false
    await userEvent.click(screen.getByRole('button', { name: 'Remove passkey Laptop' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/only way in/)
    expect(owner().credentials).toHaveLength(1)
  })

  it('asks for a step-up when reauth is stale, and does nothing if cancelled', async () => {
    renderSignedIn('/settings/security', { passkeys: 2, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Phone 1' }))
    expect(await screen.findByRole('dialog', { name: "Confirm it's you" })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(owner().credentials).toHaveLength(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('completes the step-up with a passkey and retries the removal', async () => {
    authenticate.mockResolvedValue(fakeAssertion('seed-cred-0'))
    renderSignedIn('/settings/security', { passkeys: 2, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Phone 1' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm with passkey' }))
    await waitFor(() => {
      expect(owner().credentials).toHaveLength(1)
    })
  })

  it('completes the step-up with a TOTP code', async () => {
    renderSignedIn('/settings/security', { passkeys: 2, totpEnabled: true, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Phone 1' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Use an authenticator code instead' }),
    )
    const dialog = within(await screen.findByRole('dialog'))
    await userEvent.type(dialog.getByLabelText('Authenticator code'), MOCK_TOTP_CODE)
    await userEvent.click(dialog.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => {
      expect(owner().credentials).toHaveLength(1)
    })
  })

  it('adds a passkey', async () => {
    register.mockResolvedValue(fakeRegistration('cred-phone'))
    renderSignedIn('/settings/security')
    await userEvent.click(await screen.findByRole('button', { name: 'Add passkey' }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Add a passkey' }))
    await userEvent.type(sheet.getByLabelText('Device name (optional)'), 'Phone')
    await userEvent.click(sheet.getByRole('button', { name: 'Create passkey' }))
    expect(await screen.findByRole('button', { name: 'Remove passkey Phone' })).toBeEnabled()
    expect(owner().credentials.map((c) => c.nickname)).toEqual(['Laptop', 'Phone'])
  })
})

describe('Settings → Security: password and sessions', () => {
  it('changes the password', async () => {
    renderSignedIn('/settings/security')
    await userEvent.type(await screen.findByLabelText('New password'), 'a long passphrase here')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'a long passphrase here')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password saved.')).toBeVisible()
    expect(owner().password).toBe('a long passphrase here')
  })

  it('checks length and confirmation', async () => {
    renderSignedIn('/settings/security')
    await userEvent.type(await screen.findByLabelText('New password'), 'short')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'other')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText(/at least 12 characters/)).toBeVisible()
  })

  it('says the session list is on the way, without a phase number', async () => {
    renderSignedIn('/settings/security')
    expect(await screen.findByRole('heading', { name: 'Active sessions' })).toBeVisible()
    expect(screen.getByText(/Session list is on the way/)).toBeVisible()
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })
})
