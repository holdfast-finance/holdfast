import { startRegistration } from '@simplewebauthn/browser'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MOCK_SETUP_TOKEN, db, seedHousehold } from '@/mocks/db'
import { renderApp } from '@/test/render'
import { fakeRegistration, notAllowed } from '@/test/webauthn'

vi.mock('@simplewebauthn/browser', () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}))

const register = vi.mocked(startRegistration)

beforeEach(() => {
  register.mockReset()
  register.mockResolvedValue(fakeRegistration('cred-new-laptop'))
})

async function enterToken(token: string) {
  await userEvent.type(await screen.findByLabelText('Setup token'), token)
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

async function enterAbout() {
  await userEvent.type(await screen.findByLabelText('Household name'), 'Example Household')
  await userEvent.type(screen.getByLabelText('Your name'), 'Alex Example')
  await userEvent.type(screen.getByLabelText('Your email'), 'alex@example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Create household' }))
}

describe('first-run setup wizard', () => {
  it('walks token → household → passkey → second person → dashboard', async () => {
    const { router } = renderApp('/')
    expect(await screen.findByText('Step 1 of 4')).toBeVisible()

    await enterToken(MOCK_SETUP_TOKEN)
    expect(await screen.findByText('Step 2 of 4')).toBeVisible()
    await enterAbout()

    expect(await screen.findByRole('heading', { name: 'Create your passkey' })).toBeVisible()
    await userEvent.type(screen.getByLabelText('Device name (optional)'), 'Laptop')
    await userEvent.click(screen.getByRole('button', { name: 'Create passkey' }))

    expect(await screen.findByRole('heading', { name: 'Add a second person' })).toBeVisible()
    expect(register).toHaveBeenCalledWith({
      optionsJSON: expect.objectContaining({
        authenticatorSelection: { residentKey: 'required', userVerification: 'required' },
      }) as unknown,
    })
    await userEvent.type(screen.getByLabelText('Their name'), 'Sam Example')
    await userEvent.selectOptions(screen.getByLabelText('Role'), 'spouse')
    await userEvent.click(screen.getByRole('button', { name: 'Add person' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(await screen.findByText('$[NET WORTH]')).toBeInTheDocument()

    expect(db.household?.name).toBe('Example Household')
    expect(db.people.map((p) => [p.display_name, p.filing_role])).toEqual([
      ['Alex Example', 'primary'],
      ['Sam Example', 'spouse'],
    ])
    expect(db.users[0]?.role).toBe('owner')
    expect(db.users[0]?.credentials.map((c) => c.nickname)).toEqual(['Laptop'])
  })

  it('sends a wrong setup token back to step 1 with the server message', async () => {
    renderApp('/setup')
    await enterToken('not-the-token')
    await enterAbout()
    expect(await screen.findByText('Step 1 of 4')).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('That setup token is wrong or expired.')
    expect(db.household).toBeNull()
  })

  it('lets you skip the second person', async () => {
    const { router } = renderApp('/setup')
    await enterToken(MOCK_SETUP_TOKEN)
    await enterAbout()
    await userEvent.click(await screen.findByRole('button', { name: 'Create passkey' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Skip for now' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(db.people).toHaveLength(1)
  })

  it('keeps you on the passkey step if the prompt is closed', async () => {
    register.mockRejectedValueOnce(notAllowed())
    renderApp('/setup')
    await enterToken(MOCK_SETUP_TOKEN)
    await enterAbout()
    await userEvent.click(await screen.findByRole('button', { name: 'Create passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/prompt closed/)
    // Retry works.
    await userEvent.click(screen.getByRole('button', { name: 'Create passkey' }))
    expect(await screen.findByRole('heading', { name: 'Add a second person' })).toBeVisible()
  })
})

describe('invite accept', () => {
  it('accepts the invite, registers a passkey and lands in the app', async () => {
    seedHousehold({ signedIn: false })
    const person = {
      id: 'person-invitee',
      display_name: 'Sam Example',
      filing_role: 'spouse' as const,
    }
    db.people.push(person)
    db.invites.push({ token: 'tok123', personId: person.id, email: 'sam@example.com', used: false })

    const { router } = renderApp('/invite/tok123')
    await userEvent.click(await screen.findByRole('button', { name: 'Accept invite' }))
    expect(await screen.findByRole('heading', { name: 'Welcome, Sam Example' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Create passkey' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    const user = db.users.find((u) => u.personId === person.id)
    expect(user?.role).toBe('member')
    expect(user?.credentials).toHaveLength(1)
  })

  it('explains a used or expired link', async () => {
    seedHousehold()
    renderApp('/invite/nope')
    await userEvent.click(await screen.findByRole('button', { name: 'Accept invite' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/used, expired or wrong/)
  })
})
