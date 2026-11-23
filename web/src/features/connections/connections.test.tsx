import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { db, seedHousehold, type SeedOptions } from '@/mocks/db'
import { mockConnection } from '@/mocks/connections'
import { server } from '@/mocks/node'
import { renderApp } from '@/test/render'

/*
 * A stand-in for Plaid's Link script: it records the config Holdfast passes, and the test plays
 * the part of the user (finishing Link or closing it).
 */
interface LinkConfig {
  token: string
  onSuccess: (publicToken: string) => void
  onExit: () => void
}
let link: LinkConfig | null = null

function installPlaid() {
  link = null
  ;(window as unknown as { Plaid?: unknown }).Plaid = {
    create: (config: LinkConfig) => {
      link = config
      return {
        open: () => undefined,
        destroy: () => undefined,
      }
    },
  }
}

let calls: string[] = []
const record = ({ request }: { request: Request }) => {
  calls.push(`${request.method} ${new URL(request.url).pathname}`)
}

beforeEach(() => {
  installPlaid()
  calls = []
  server.events.on('request:start', record)
})
afterEach(() => {
  server.events.removeListener('request:start', record)
  delete (window as unknown as { Plaid?: unknown }).Plaid
})

type Scenario = (s: typeof db) => void

function openConnections(
  options: { env?: 'sandbox' | 'production'; seed?: SeedOptions; shape?: Scenario } = {},
) {
  seedHousehold({ signedIn: true, ...options.seed })
  db.plaid.env = options.env ?? 'sandbox'
  options.shape?.(db)
  renderApp('/settings/connections')
  return options
}

/** The Link button enables once the checklist reports ready, which takes one live check. */
async function openLinkSheet() {
  await userEvent.click(await screen.findByRole('button', { name: 'Run check' }))
  const button = await screen.findByRole('button', { name: 'Link a bank' })
  await waitFor(() => {
    expect(button).toBeEnabled()
  })
  await userEvent.click(button)
  return screen.findByRole('dialog')
}

const startButton = () => screen.findByRole('button', { name: 'Open Plaid Link' })

async function fillProduction(dialog: HTMLElement, owner: string, phrase: string) {
  const d = within(dialog)
  await d.findByText(/Slots Holdfast has counted/)
  await userEvent.type(d.getByLabelText(/Slots you have used/), owner)
  await userEvent.click(d.getByRole('checkbox'))
  if (phrase) await userEvent.type(d.getByLabelText('Slot phrase'), phrase)
}

describe('Settings → Connections: linking', () => {
  it('requires the slot phrase on a production link and builds it from the larger count', async () => {
    openConnections({ env: 'production', shape: (s) => (s.connections.ledgerCount = 2) })
    const dialog = await openLinkSheet()
    // Ledger says 2, the owner says 4: the phrase is for slot 5, not slot 3.
    await fillProduction(dialog, '4', '')
    expect(within(dialog).getByText('Type: use slot 5 of 10')).toBeVisible()

    await userEvent.click(await startButton())
    expect(await within(dialog).findByText('Type "use slot 5 of 10" to continue.')).toBeVisible()
    expect(calls).not.toContain('POST /api/connections/plaid/link-token')

    await userEvent.type(within(dialog).getByLabelText('Slot phrase'), 'use slot 3 of 10')
    await userEvent.click(await startButton())
    expect(await within(dialog).findByText('Type "use slot 5 of 10" to continue.')).toBeVisible()
    expect(calls).not.toContain('POST /api/connections/plaid/link-token')
    expect(link).toBeNull()

    await userEvent.clear(within(dialog).getByLabelText('Slot phrase'))
    await userEvent.type(within(dialog).getByLabelText('Slot phrase'), 'use slot 5 of 10')
    await userEvent.click(await startButton())
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    expect(calls).toContain('POST /api/connections/plaid/link-token')
  })

  it('shows what will be asked of Plaid, the count and its source, and existing connections', async () => {
    openConnections({
      env: 'production',
      shape: (s) => {
        s.connections.ledgerCount = 1
        s.connections.connections = [
          mockConnection('11111111-1111-4111-8111-111111111111', { env: 'production' }),
        ]
      },
    })
    const dialog = await openLinkSheet()
    const d = within(dialog)
    expect(await d.findByText('Transactions')).toBeVisible()
    expect(d.getByText(/Liabilities.*if offered/)).toBeVisible()
    expect(d.getByText('730')).toBeVisible()
    expect(d.getByText('1 of 10')).toBeVisible()
    expect(d.getByText(/your own slot log/)).toBeVisible()
    expect(d.getByText(/Plaid · real data · Accounts to map/)).toBeVisible()
  })

  it('blocks the 11th link when the ledger has all ten', async () => {
    openConnections({ env: 'production', shape: (s) => (s.connections.ledgerCount = 10) })
    const dialog = await openLinkSheet()
    expect(await within(dialog).findByText(/Holdfast never asks for an 11th/)).toBeVisible()
    expect(within(dialog).queryByLabelText('Slot phrase')).not.toBeInTheDocument()
    expect(await startButton()).toBeDisabled()
  })

  it("blocks it when the owner's own count says ten, even if the ledger says fewer", async () => {
    openConnections({ env: 'production', shape: (s) => (s.connections.ledgerCount = 3) })
    const dialog = await openLinkSheet()
    await fillProduction(dialog, '10', '')
    expect(await within(dialog).findByText(/Holdfast never asks for an 11th/)).toBeVisible()
    expect(await startButton()).toBeDisabled()
    expect(calls).not.toContain('POST /api/connections/plaid/link-token')
  })

  it('needs no phrase in Sandbox and links through exchange', async () => {
    openConnections()
    const dialog = await openLinkSheet()
    await within(dialog).findByText(/Slots Holdfast has counted/)
    expect(within(dialog).queryByLabelText('Slot phrase')).not.toBeInTheDocument()
    await userEvent.click(await startButton())
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    act(() => {
      link?.onSuccess('public-sandbox-mock')
    })
    expect(await screen.findByText('Linked.')).toBeVisible()
    expect(db.connections.connections).toHaveLength(1)
  })

  it('does not abandon when Link closes: it asks Plaid and keeps the slot held', async () => {
    openConnections()
    const dialog = await openLinkSheet()
    await within(dialog).findByText(/Slots Holdfast has counted/)
    await userEvent.click(await startButton())
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    act(() => {
      link?.onExit()
    })
    expect(await screen.findByText(/keeping the slot held until it knows/)).toBeVisible()
    expect(calls.some((c) => c.endsWith('/resolve'))).toBe(true)
    expect(calls.some((c) => c.endsWith('/abandon'))).toBe(false)
    expect(db.connections.reservations[0]?.state).toBe('reserved')
  })

  it('reports a slot as free only when Plaid says nothing was made', async () => {
    openConnections()
    const dialog = await openLinkSheet()
    await within(dialog).findByText(/Slots Holdfast has counted/)
    await userEvent.click(await startButton())
    await waitFor(() => {
      expect(link).not.toBeNull()
    })
    const reservation = db.connections.reservations[0]
    if (reservation) reservation.outcome = 'nothing'
    act(() => {
      link?.onExit()
    })
    expect(await screen.findByText('No bank was linked.')).toBeVisible()
    expect(calls.some((c) => c.endsWith('/abandon'))).toBe(false)
  })
})

describe('Settings → Connections: unfinished links', () => {
  const open = (s: typeof db) => {
    s.connections.reservations = [
      {
        id: '22222222-2222-4222-8222-222222222222',
        state: 'reserved',
        env: 'sandbox',
        created_at: '2026-11-21T10:00:00Z',
        outcome: 'open',
      },
    ]
  }

  async function startAbandon() {
    await userEvent.click(await screen.findByRole('button', { name: 'I did not finish' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(
      within(dialog).getByLabelText('Confirmation phrase'),
      'I did not finish this link',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Release this slot' }))
    return dialog
  }

  it('releases the slot with the phrase', async () => {
    openConnections({ shape: open })
    await startAbandon()
    await waitFor(() => {
      expect(db.connections.reservations[0]?.state).toBe('abandoned')
    })
  })

  it('needs the exact phrase', async () => {
    openConnections({ shape: open })
    await userEvent.click(await screen.findByRole('button', { name: 'I did not finish' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Confirmation phrase'), 'nope')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Release this slot' }))
    expect(await within(dialog).findByText(/to continue/)).toBeVisible()
    expect(calls.some((c) => c.endsWith('/abandon'))).toBe(false)
  })

  it('explains a 502 from Plaid and lets the owner try again', async () => {
    openConnections({
      shape: (s) => {
        open(s)
        s.connections.plaidDown = true
      },
    })
    const dialog = await startAbandon()
    expect(await within(dialog).findByText(/kept the slot held/)).toBeVisible()
    expect(db.connections.reservations[0]?.state).toBe('reserved')
    db.connections.plaidDown = false
    await userEvent.click(within(dialog).getByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(db.connections.reservations[0]?.state).toBe('abandoned')
    })
  })

  it('explains a 409 and offers to check with Plaid instead', async () => {
    openConnections({
      shape: (s) => {
        open(s)
        s.connections.abandonConflict = true
      },
    })
    const dialog = await startAbandon()
    expect(await within(dialog).findByText(/Check with Plaid instead of releasing/)).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'Check with Plaid' })).toBeVisible()
    expect(db.connections.reservations[0]?.state).toBe('reserved')
  })
})

describe('Settings → Connections: members', () => {
  it('sees the connections but none of the owner-only actions', async () => {
    seedHousehold({ as: 'member' })
    db.connections.connections = [
      mockConnection('33333333-3333-4333-8333-333333333333', { env: 'production' }),
    ]
    db.connections.reservations = [
      {
        id: '44444444-4444-4444-8444-444444444444',
        state: 'reserved',
        env: 'production',
        created_at: '2026-11-21T10:00:00Z',
        outcome: 'open',
      },
    ]
    renderApp('/settings/connections')
    expect(await screen.findByRole('list', { name: 'Connections' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Link a bank' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run check' })).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Unfinished links' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'I did not finish' })).not.toBeInTheDocument()
  })
})
