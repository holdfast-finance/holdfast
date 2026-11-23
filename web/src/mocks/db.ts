import type { ImportRun } from '@/api/imports'
import type { Account, Transaction } from '@/api/ledger'
import type { AuditEntry, FilingRole, UserRole } from '@/api/contracts'
import { defaultConnectionsScenario, type ConnectionsScenario } from '@/mocks/connections'
import { defaultPlaidScenario, type PlaidScenario } from '@/mocks/plaid'

/*
 * In-memory state behind the MSW handlers. Mirrors the shape of the Phase 1 tables closely enough
 * to exercise every web flow, and nothing more. All data is synthetic.
 *
 * The session is a module variable instead of a cookie: Node's fetch (vitest) keeps no cookie jar,
 * and the browser worker can't see HttpOnly cookies anyway.
 */

/** Setup token the mock accepts (the real one is printed once in the API log). */
export const MOCK_SETUP_TOKEN = 'mock-setup-token'
/** The only TOTP code the mock accepts. */
export const MOCK_TOTP_CODE = '123456'
/** Failed password logins before the mock answers 429 rate_limited. */
export const MOCK_LOGIN_LIMIT = 5

export interface MockPerson {
  id: string
  display_name: string
  filing_role: FilingRole
}

export interface MockCredential {
  id: string
  /** WebAuthn credential id (base64url), as sent by the browser. */
  credentialId: string
  nickname: string
  created_at: string
  last_used_at: string | null
}

export interface MockUser {
  id: string
  personId: string
  email: string
  role: UserRole
  password: string | null
  totpEnabled: boolean
  totpPending: boolean
  recoveryCodes: string[]
  credentials: MockCredential[]
}

export interface MockInvite {
  token: string
  personId: string
  email: string
  used: boolean
}

export interface MockSession {
  userId: string
  reauthAt: number
}

export interface MockState {
  household: { id: string; name: string } | null
  people: MockPerson[]
  users: MockUser[]
  invites: MockInvite[]
  audit: AuditEntry[]
  /** Imported accounts and transactions (see `./ledger.ts`). */
  accounts: Account[]
  transactions: Transaction[]
  /** Statement import runs and the filenames already imported (see `./imports.ts`). */
  importRuns: ImportRun[]
  importedFiles: string[]
  /** Plaid setup scenario (see `./plaid.ts`). */
  plaid: PlaidScenario
  /** Plaid Link scenario: ledger, connections, reservations (see ./connections.ts). */
  connections: ConnectionsScenario
  session: MockSession | null
  challenges: Set<string>
  failedLogins: number
  seq: number
}

function emptyState(): MockState {
  return {
    household: null,
    people: [],
    users: [],
    invites: [],
    audit: [],
    accounts: [],
    transactions: [],
    importRuns: [],
    importedFiles: [],
    plaid: defaultPlaidScenario(),
    connections: defaultConnectionsScenario(),
    session: null,
    challenges: new Set(),
    failedLogins: 0,
    seq: 0,
  }
}

export const db: MockState = emptyState()

export function resetDb(): void {
  Object.assign(db, emptyState())
}

export function nextId(prefix: string): string {
  db.seq += 1
  return `${prefix}-${String(db.seq).padStart(4, '0')}`
}

export function now(): string {
  return new Date().toISOString()
}

export function randomToken(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export function audit(action: string, outcome: AuditEntry['outcome'] = 'success'): void {
  const user = db.session ? db.users.find((u) => u.id === db.session?.userId) : undefined
  const person = user ? db.people.find((p) => p.id === user.personId) : undefined
  db.audit.unshift({
    id: nextId('audit'),
    created_at: now(),
    action,
    actor_display_name: person?.display_name ?? null,
    ip: '100.64.0.1',
    outcome,
  })
}

export function generateRecoveryCodes(): string[] {
  return Array.from(
    { length: 10 },
    (_, i) => `rcv${String(i).padStart(2, '0')}-${randomToken().slice(0, 8)}`,
  )
}

// --- Seeds for tests and dev -------------------------------------------------------------------

export interface SeedOptions {
  /** Start signed in as the owner. */
  signedIn?: boolean
  /** Sign in as the member instead. */
  as?: 'owner' | 'member'
  totpEnabled?: boolean
  passkeys?: number
  withMember?: boolean
  /** Session reauth is stale, so sensitive actions answer reauth_required. */
  staleReauth?: boolean
}

export const SEED = {
  householdName: 'Example Household',
  ownerName: 'Alex Example',
  ownerEmail: 'alex@example.com',
  ownerPassword: 'correct horse battery staple',
  memberName: 'Sam Example',
  memberEmail: 'sam@example.com',
} as const

/** A household that finished setup: owner (+ optional member) with passkeys. */
export function seedHousehold(options: SeedOptions = {}): void {
  resetDb()
  const passkeys = options.passkeys ?? 1
  db.household = { id: nextId('hh'), name: SEED.householdName }
  const owner: MockPerson = {
    id: nextId('person'),
    display_name: SEED.ownerName,
    filing_role: 'primary',
  }
  db.people.push(owner)
  db.users.push({
    id: nextId('user'),
    personId: owner.id,
    email: SEED.ownerEmail,
    role: 'owner',
    password: SEED.ownerPassword,
    totpEnabled: options.totpEnabled ?? false,
    totpPending: false,
    recoveryCodes: options.totpEnabled ? generateRecoveryCodes() : [],
    credentials: Array.from({ length: passkeys }, (_, i) => ({
      id: nextId('cred'),
      credentialId: `seed-cred-${String(i)}`,
      nickname: i === 0 ? 'Laptop' : `Phone ${String(i)}`,
      created_at: '2026-01-02T03:04:05Z',
      last_used_at: null,
    })),
  })
  if (options.withMember || options.as === 'member') {
    const member: MockPerson = {
      id: nextId('person'),
      display_name: SEED.memberName,
      filing_role: 'spouse',
    }
    db.people.push(member)
    db.users.push({
      id: nextId('user'),
      personId: member.id,
      email: SEED.memberEmail,
      role: 'member',
      password: null,
      totpEnabled: false,
      totpPending: false,
      recoveryCodes: [],
      credentials: [
        {
          id: nextId('cred'),
          credentialId: 'seed-cred-member',
          nickname: 'Phone',
          created_at: '2026-01-02T03:04:05Z',
          last_used_at: null,
        },
      ],
    })
  }
  if (options.signedIn || options.as) {
    const role = options.as ?? 'owner'
    const user = db.users.find((u) => u.role === role)
    if (user) {
      db.session = {
        userId: user.id,
        reauthAt: options.staleReauth ? 0 : Date.now(),
      }
    }
  }
}

/** Make the current session's step-up stale (sensitive actions will answer reauth_required). */
export function expireReauth(): void {
  if (db.session) db.session.reauthAt = 0
}

export function currentUser(): MockUser | undefined {
  const session = db.session
  return session ? db.users.find((u) => u.id === session.userId) : undefined
}
