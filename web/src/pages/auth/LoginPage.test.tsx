import { startAuthentication } from '@simplewebauthn/browser'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MOCK_LOGIN_LIMIT, MOCK_TOTP_CODE, SEED, db, seedHousehold } from '@/mocks/db'
import { renderApp } from '@/test/render'
import { fakeAssertion, notAllowed } from '@/test/webauthn'

vi.mock('@simplewebauthn/browser', () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}))

const authenticate = vi.mocked(startAuthentication)

beforeEach(() => {
  authenticate.mockReset()
})

async function openFallback() {
  await userEvent.click(await screen.findByRole('button', { name: 'Use password + code instead' }))
}

async function fillPassword(email: string, password: string, code: string) {
  await userEvent.type(screen.getByLabelText('Email'), email)
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.type(screen.getByLabelText(/Authenticator code|Recovery code/), code)
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('login: passkey', () => {
  it('signs in with a registered passkey and returns to the page you wanted', async () => {
    seedHousehold()
    authenticate.mockResolvedValue(fakeAssertion('seed-cred-0'))
    const { router } = renderApp('/budgets')

    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with passkey' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Budgets' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/budgets')
    expect(authenticate).toHaveBeenCalledWith({
      optionsJSON: expect.objectContaining({ userVerification: 'required' }) as unknown,
    })
    expect(db.session).not.toBeNull()
  })

  it('explains an unknown passkey', async () => {
    seedHousehold()
    authenticate.mockResolvedValue(fakeAssertion('not-registered'))
    renderApp('/login')
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That passkey is not registered here.',
    )
    expect(db.session).toBeNull()
  })

  it('says so plainly when the passkey prompt is closed', async () => {
    seedHousehold()
    authenticate.mockRejectedValue(notAllowed())
    renderApp('/login')
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/prompt closed or timed out/)
  })
})

describe('login: password + code', () => {
  it('signs in with password and TOTP', async () => {
    seedHousehold({ totpEnabled: true })
    const { router } = renderApp('/login')
    await openFallback()
    await fillPassword(SEED.ownerEmail, SEED.ownerPassword, MOCK_TOTP_CODE)
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(authenticate).not.toHaveBeenCalled()
  })

  it('rejects a wrong code with one generic message', async () => {
    seedHousehold({ totpEnabled: true })
    renderApp('/login')
    await openFallback()
    await fillPassword(SEED.ownerEmail, SEED.ownerPassword, '000000')
    expect(await screen.findByRole('alert')).toHaveTextContent('Email, password or code is wrong.')
    expect(db.session).toBeNull()
  })

  it('validates fields before calling the API', async () => {
    seedHousehold({ totpEnabled: true })
    renderApp('/login')
    await openFallback()
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter the email on your account.')).toBeVisible()
    expect(screen.getByText('Enter your password.')).toBeVisible()
    expect(screen.getByLabelText('Authenticator code')).toHaveAttribute('aria-invalid', 'true')
  })

  it('accepts a recovery code once', async () => {
    seedHousehold({ totpEnabled: true })
    const owner = db.users[0]
    const code = owner?.recoveryCodes[0] ?? ''
    const { router } = renderApp('/login')
    await openFallback()
    await userEvent.click(screen.getByRole('button', { name: /Use a recovery code/ }))
    await fillPassword(SEED.ownerEmail, SEED.ownerPassword, code)
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(owner?.recoveryCodes).not.toContain(code)
  })

  it('shows the rate-limit message after too many failures', async () => {
    seedHousehold({ totpEnabled: true })
    db.failedLogins = MOCK_LOGIN_LIMIT
    renderApp('/login')
    await openFallback()
    await fillPassword(SEED.ownerEmail, SEED.ownerPassword, MOCK_TOTP_CODE)
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many attempts/)
  })
})
