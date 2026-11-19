import { startAuthentication } from '@simplewebauthn/browser'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SEED, db } from '@/mocks/db'
import { renderSignedIn } from '@/test/render'
import { fakeAssertion } from '@/test/webauthn'

vi.mock('@simplewebauthn/browser', () => ({
  startRegistration: vi.fn(),
  startAuthentication: vi.fn(),
}))

const authenticate = vi.mocked(startAuthentication)

beforeEach(() => {
  authenticate.mockReset()
})

async function peopleList() {
  return within(await screen.findByRole('list', { name: 'People' }))
}

describe('Settings → Household', () => {
  it('lists people with their roles and sign-in state', async () => {
    renderSignedIn('/settings/household', { withMember: true })
    const list = await peopleList()
    expect(list.getByText(SEED.ownerName)).toBeVisible()
    expect(list.getByText('(you)')).toBeVisible()
    expect(list.getByText(SEED.memberName)).toBeVisible()
    expect(list.getAllByText('Has sign-in')).toHaveLength(2)
    expect(screen.getByText(SEED.householdName)).toBeVisible()
  })

  it('adds a person', async () => {
    renderSignedIn('/settings/household')
    await userEvent.click(await screen.findByRole('button', { name: 'Add person' }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Add a person' }))
    await userEvent.type(sheet.getByLabelText('Name'), 'Jamie Example')
    await userEvent.selectOptions(sheet.getByLabelText('Filing role'), 'dependent')
    await userEvent.click(sheet.getByRole('button', { name: 'Add person' }))

    const list = await peopleList()
    expect(await list.findByText('Jamie Example')).toBeVisible()
    expect(list.getByText('Dependent')).toBeVisible()
    expect(db.people.at(-1)).toMatchObject({
      display_name: 'Jamie Example',
      filing_role: 'dependent',
    })
  })

  it('validates the add form', async () => {
    renderSignedIn('/settings/household')
    await userEvent.click(await screen.findByRole('button', { name: 'Add person' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(sheet.getByRole('button', { name: 'Add person' }))
    expect(await sheet.findByText('Give them a name.')).toBeVisible()
    expect(db.people).toHaveLength(1)
  })

  it('edits a person', async () => {
    renderSignedIn('/settings/household', { withMember: true })
    await userEvent.click(await screen.findByRole('button', { name: `Edit ${SEED.memberName}` }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Edit person' }))
    const name = sheet.getByLabelText('Name')
    expect(name).toHaveValue(SEED.memberName)
    await userEvent.clear(name)
    await userEvent.type(name, 'Sam Renamed')
    await userEvent.click(sheet.getByRole('button', { name: 'Save changes' }))

    expect(await (await peopleList()).findByText('Sam Renamed')).toBeVisible()
    expect(db.people[1]?.display_name).toBe('Sam Renamed')
  })

  it('renames the household', async () => {
    renderSignedIn('/settings/household')
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }))
    const field = screen.getByLabelText('Household name')
    await userEvent.clear(field)
    await userEvent.type(field, 'The Example Crew')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('The Example Crew')).toBeVisible()
  })

  it('removes a person without an account after confirming', async () => {
    renderSignedIn('/settings/household')
    db.people.push({ id: 'person-x', display_name: 'Pat Example', filing_role: 'dependent' })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Pat Example' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm removing Pat Example' }))
    await waitFor(() => {
      expect(screen.queryByText('Pat Example')).not.toBeInTheDocument()
    })
    expect(db.people).toHaveLength(1)
  })

  it('creates a one-time invite link after a step-up, with a copy button', async () => {
    renderSignedIn('/settings/household', { staleReauth: true })
    db.people.push({ id: 'person-x', display_name: 'Pat Example', filing_role: 'spouse' })
    authenticate.mockResolvedValue(fakeAssertion('seed-cred-0'))

    await userEvent.click(await screen.findByRole('button', { name: 'Invite Pat Example' }))
    const sheet = within(await screen.findByRole('dialog', { name: 'Invite Pat Example' }))
    await userEvent.type(sheet.getByLabelText('Their email'), 'pat@example.com')
    await userEvent.click(sheet.getByRole('button', { name: 'Create invite link' }))

    // The API wants a fresh step-up: the prompt opens, the passkey confirms, the call retries.
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm with passkey' }))
    const link = await screen.findByLabelText('Invite link')
    const token = db.invites[0]?.token ?? 'missing'
    // Token in the fragment: never sent to a server, so never in a log or Referer.
    expect(link).toHaveValue(`http://localhost:3000/invite#${token}`)

    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await userEvent.click(screen.getByRole('button', { name: 'Copy invite link' }))
    expect(writeText).toHaveBeenCalledWith(`http://localhost:3000/invite#${token}`)
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeVisible()
    // Announced from a polite status region next to the button.
    expect(screen.getAllByRole('status').some((el) => el.textContent === 'Copied')).toBe(true)
  })

  it('shows members the list without owner actions', async () => {
    renderSignedIn('/settings/household', { as: 'member' })
    await peopleList()
    expect(screen.getByText(/Only the owner can change this list/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Add person' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Edit / })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename' })).not.toBeInTheDocument()
  })
})
