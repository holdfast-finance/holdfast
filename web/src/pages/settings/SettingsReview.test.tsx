import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { db } from '@/mocks/db'
import { auditLabel } from '@/pages/settings/labels'
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

describe('adding a credential needs a step-up once you have one', () => {
  it('prompts before adding a second passkey, then registers it', async () => {
    authenticate.mockResolvedValue(fakeAssertion('seed-cred-0'))
    register.mockResolvedValue(fakeRegistration('cred-phone'))
    renderSignedIn('/settings/security', { passkeys: 1, staleReauth: true })

    await userEvent.click(await screen.findByRole('button', { name: 'Add passkey' }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Add a passkey' }))
    await userEvent.click(sheet.getByRole('button', { name: 'Create passkey' }))

    const prompt = await screen.findByRole('dialog', { name: "Confirm it's you" })
    expect(prompt).toHaveTextContent("Sensitive action. One check, then you're back to it.")
    expect(register).not.toHaveBeenCalled()
    await userEvent.click(within(prompt).getByRole('button', { name: 'Confirm with passkey' }))
    await waitFor(() => {
      expect(db.users[0]?.credentials).toHaveLength(2)
    })
  })

  it('prompts before starting authenticator setup', async () => {
    renderSignedIn('/settings/security', { passkeys: 1, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Set up authenticator' }))
    expect(await screen.findByRole('dialog', { name: "Confirm it's you" })).toBeVisible()
    expect(db.users[0]?.totpPending).toBe(false)
  })

  it('focuses the code field when switching the prompt to a code', async () => {
    renderSignedIn('/settings/security', { passkeys: 2, totpEnabled: true, staleReauth: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove passkey Phone 1' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Use an authenticator code instead' }),
    )
    expect(await screen.findByLabelText('Authenticator code')).toHaveFocus()
  })
})

describe('Household: remove confirms in place', () => {
  async function removeButton() {
    renderSignedIn('/settings/household')
    db.people.push({ id: 'person-x', display_name: 'Pat Example', filing_role: 'dependent' })
    return screen.findByRole('button', { name: 'Remove Pat Example' })
  }

  it('arms on the first press and keeps focus on the same button', async () => {
    const button = await removeButton()
    await userEvent.click(button)
    expect(button).toHaveAccessibleName('Confirm removing Pat Example')
    expect(button).toHaveTextContent('Yes, remove')
    expect(button).toHaveFocus()
    await userEvent.click(button)
    await waitFor(() => {
      expect(db.people.map((p) => p.display_name)).not.toContain('Pat Example')
    })
  })

  it('disarms on Escape', async () => {
    const button = await removeButton()
    await userEvent.click(button)
    await userEvent.keyboard('{Escape}')
    expect(button).toHaveAccessibleName('Remove Pat Example')
    expect(db.people.map((p) => p.display_name)).toContain('Pat Example')
  })

  it('disarms when focus moves away', async () => {
    const button = await removeButton()
    await userEvent.click(button)
    await userEvent.tab()
    expect(button).toHaveAccessibleName('Remove Pat Example')
  })

  it('never offers "Primary" as a role, and shows the owner role as fixed', async () => {
    renderSignedIn('/settings/household')
    await userEvent.click(await screen.findByRole('button', { name: 'Add person' }))
    const add = within(await screen.findByRole('dialog', { name: 'Add a person' }))
    const select = add.getByLabelText<HTMLSelectElement>('Filing role')
    expect(Array.from(select.options, (o) => o.value)).toEqual(['spouse', 'dependent'])
  })

  it('uses a 16px font on the invite link field (no iOS zoom)', async () => {
    authenticate.mockResolvedValue(fakeAssertion('seed-cred-0'))
    renderSignedIn('/settings/household')
    db.people.push({ id: 'person-y', display_name: 'Pat Example', filing_role: 'spouse' })
    await userEvent.click(await screen.findByRole('button', { name: 'Invite Pat Example' }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Invite Pat Example' }))
    await userEvent.type(sheet.getByLabelText('Their email'), 'pat@example.com')
    await userEvent.click(sheet.getByRole('button', { name: 'Create invite link' }))
    expect(await screen.findByLabelText('Invite link')).toHaveClass('text-base')
  })
})

describe('audit list', () => {
  it('shows a readable action and the time, with the raw code as a tooltip', async () => {
    renderSignedIn('/settings/security')
    db.audit.unshift({
      id: 'audit-x',
      created_at: '2026-03-04T15:06:00Z',
      action: 'auth.passkey.registered',
      actor_display_name: 'Alex Example',
      ip: '100.64.0.1',
      outcome: 'success',
    })
    const item = await screen.findByText('Added a passkey')
    expect(item).toHaveAttribute('title', 'auth.passkey.registered')
    const time = item.parentElement?.querySelector('time')
    expect(time?.getAttribute('datetime')).toBe('2026-03-04T15:06:00Z')
    // Time of day, not just the date.
    expect(time?.textContent).toMatch(/\d{1,2}:\d{2}/)
  })

  it('falls back to a readable label for unknown codes', () => {
    expect(auditLabel('agent.run_started')).toBe('Agent run started')
  })
})
