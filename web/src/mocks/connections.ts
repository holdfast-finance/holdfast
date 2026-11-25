import type { Connection, LinkPlan, LoginKind, OpenReservation, SlotOut } from '@/api/connections'
import { SLOT_TOTAL, slotPhrase } from '@/api/connections'

/*
 * Plaid Link scenario for the mock API (docs/phases/PHASE-2-contract.md 5.2). Tests set fields on
 * `db.connections` to reach each state. Everything is synthetic: the "tokens" are fixed strings.
 */

export const MOCK_LINK_TOKEN = 'link-mock-token-0001'

export interface MockReservation {
  id: string
  state: OpenReservation['state']
  env: OpenReservation['env']
  created_at: string
  /** What Plaid reports when asked: an Item was made, nothing was, or Link is still open. */
  outcome: 'item' | 'nothing' | 'open'
  connectionId?: string
}

export interface ConnectionsScenario {
  /** Slots the Holdfast ledger has counted. */
  ledgerCount: number
  connections: Connection[]
  reservations: MockReservation[]
  /** Plaid cannot be reached: abandon answers 502, resolve leaves the state as it was. */
  plaidDown: boolean
  /** Abandon answers 409 conflict: Link did create an Item. */
  abandonConflict: boolean
}

export function defaultConnectionsScenario(): ConnectionsScenario {
  return {
    ledgerCount: 0,
    connections: [],
    reservations: [],
    plaidDown: false,
    abandonConflict: false,
  }
}

type PlanProducts = Pick<LinkPlan, 'products' | 'optional_products' | 'days_requested'>

const PRODUCTS: Record<LoginKind, PlanProducts> = {
  bank_card: {
    products: ['transactions'],
    optional_products: ['liabilities'],
    days_requested: 730,
  },
  retirement_hsa: { products: ['investments'], optional_products: [], days_requested: null },
  brokerage: { products: ['investments'], optional_products: [], days_requested: null },
}

export function linkPlan(s: ConnectionsScenario, kind: LoginKind, production: boolean): LinkPlan {
  return {
    ...PRODUCTS[kind],
    ledger_count: s.ledgerCount,
    count_source: 'owner_slot_log',
    existing_connections: s.connections.map((c) => ({
      id: c.id,
      provider: c.provider,
      env: c.env,
      status: c.status,
      created_at: c.created_at,
    })),
    phrase: production && s.ledgerCount < SLOT_TOTAL ? slotPhrase(s.ledgerCount) : null,
  }
}

export function mockConnection(id: string, overrides: Partial<Connection> = {}): Connection {
  return {
    id,
    provider: 'plaid',
    env: 'sandbox',
    status: 'pending_setup',
    created_at: '2026-11-20T12:00:00Z',
    last_synced_at: null,
    pending_accounts: 2,
    mapped_accounts: 0,
    ignored_accounts: 0,
    slot_flag: null,
    accounts: [],
    ...overrides,
  }
}

interface Refusal {
  code: 'no_slots_left' | 'confirm_required'
  message: string
}

/** The refusal the real link-token route gives for a production link, or null when it passes. */
export function linkTokenRefusal(
  s: ConnectionsScenario,
  body: Record<string, unknown>,
): Refusal | null {
  const none: Refusal = { code: 'no_slots_left', message: 'All 10 Plaid Item slots are used.' }
  if (s.ledgerCount >= SLOT_TOTAL) return none
  const owner = body.owner_count
  if (typeof owner !== 'number' || body.not_a_duplicate !== true) {
    return {
      code: 'confirm_required',
      message: "Enter your own Item count and confirm this isn't a duplicate login.",
    }
  }
  const n = Math.max(s.ledgerCount, owner)
  if (n >= SLOT_TOTAL) return none
  if (body.confirm_phrase !== slotPhrase(n)) {
    return { code: 'confirm_required', message: 'Type the slot phrase exactly to continue.' }
  }
  return null
}

/** The reservation as the API reports its slot. */
export function slotOut(s: ConnectionsScenario, r: MockReservation): SlotOut {
  const connection = s.connections.find((c) => c.id === r.connectionId)
  return {
    reservation_id: r.id,
    state: r.state,
    flag: null,
    connection_id: connection?.id ?? null,
  }
}

/** Plaid made an Item: record the connection once (idempotent), spend the slot. */
export function linkReservation(s: ConnectionsScenario, r: MockReservation): Connection {
  const existing = s.connections.find((c) => c.id === r.connectionId)
  if (existing) return existing
  const connection = mockConnection(crypto.randomUUID(), { env: r.env })
  s.connections.push(connection)
  r.connectionId = connection.id
  r.state = 'linked'
  if (r.env === 'production') s.ledgerCount += 1
  return connection
}
