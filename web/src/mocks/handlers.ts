import { http, HttpResponse, type HttpResponseResolver } from 'msw'

import type {
  FilingRole,
  KnownErrorCode,
  MeResponse,
  Passkey,
  Person,
  SessionResponse,
  User,
} from '@/api/types'
import { ERROR_CODES, FILING_ROLES } from '@/api/types'
import { CSRF_HEADER } from '@/api/client'
import {
  MOCK_LOGIN_LIMIT,
  MOCK_SETUP_TOKEN,
  MOCK_TOTP_CODE,
  audit,
  currentUser,
  db,
  generateRecoveryCodes,
  nextId,
  now,
  randomToken,
  type MockCredential,
  type MockUser,
} from '@/mocks/db'

/*
 * MSW handlers mirroring docs/phases/PHASE-1-contract.md "API contract", backed by `./db.ts`.
 * Used by vitest (msw/node) and by `pnpm dev` when VITE_MOCK_API=1 (msw/browser). Never bundled
 * into production builds: main.tsx imports the worker only behind `import.meta.env.DEV`.
 *
 * The mock enforces the same rules the real API must: CSRF header on every non-GET, session
 * required, owner-only routes, the 5-minute reauth window, the last-credential rule, and a
 * login rate limit.
 */

const REAUTH_WINDOW_MS = 5 * 60 * 1000

const STATUS: Record<KnownErrorCode, number> = {
  unauthenticated: 401,
  reauth_required: 403,
  forbidden: 403,
  csrf_failed: 403,
  invalid_setup_token: 403,
  setup_complete: 409,
  invalid_credentials: 401,
  invalid_code: 400,
  invalid_invite: 404,
  passkey_verification_failed: 400,
  last_credential: 409,
  conflict: 409,
  not_found: 404,
  rate_limited: 429,
  validation_error: 422,
  payload_too_large: 413,
}

export function apiError(code: KnownErrorCode, message: string) {
  return HttpResponse.json({ error: { code, message } }, { status: STATUS[code] })
}

const path = (p: string) => `*/api${p}`

// --- Serializers (never include passwords, TOTP secrets or public keys) ------------------------

function userJson(user: MockUser): User {
  const person = db.people.find((p) => p.id === user.personId) ?? null
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    person: person && {
      id: person.id,
      display_name: person.display_name,
      filing_role: person.filing_role,
    },
    totp_enabled: user.totpEnabled,
    has_password: user.password !== null,
  }
}

function sessionJson(user: MockUser): SessionResponse {
  if (!db.household) throw new Error('mock: no household')
  return { user: userJson(user), household: { ...db.household } }
}

function personJson(id: string): Person | undefined {
  const person = db.people.find((p) => p.id === id)
  if (!person) return undefined
  return { ...person, has_account: db.users.some((u) => u.personId === id) }
}

function passkeyJson(c: MockCredential): Passkey {
  return { id: c.id, nickname: c.nickname, created_at: c.created_at, last_used_at: c.last_used_at }
}

// --- Guards -----------------------------------------------------------------------------------

type Resolver = HttpResponseResolver<Record<string, string | readonly string[]>, never>
type Ctx = Parameters<Resolver>[0]
type Guarded = (ctx: Ctx, user: MockUser) => ReturnType<Resolver>

function csrfOk(request: Request): boolean {
  return request.method === 'GET' || request.headers.get(CSRF_HEADER) === '1'
}

/** Wrap a public route: CSRF only. */
function publicRoute(fn: (ctx: Ctx) => ReturnType<Resolver>): Resolver {
  return (ctx) => {
    if (!csrfOk(ctx.request)) return apiError(ERROR_CODES.csrfFailed, 'Missing CSRF header.')
    return fn(ctx)
  }
}

interface GuardOptions {
  owner?: boolean
  reauth?: boolean
  /**
   * Step-up only once the user already has a way in (a passkey or TOTP). Adding a credential to
   * an account that has none (setup, invite accept, the missing-passkey banner) stays exempt.
   */
  reauthIfCredentialed?: boolean
}

function authed(fn: Guarded, options: GuardOptions = {}): Resolver {
  return (ctx) => {
    if (!csrfOk(ctx.request)) return apiError(ERROR_CODES.csrfFailed, 'Missing CSRF header.')
    const user = currentUser()
    if (!user || !db.session) return apiError(ERROR_CODES.unauthenticated, 'Sign in first.')
    if (options.owner && user.role !== 'owner') {
      return apiError(ERROR_CODES.forbidden, 'Only the household owner can do that.')
    }
    const credentialed = user.credentials.length > 0 || user.totpEnabled
    const needsReauth = options.reauth || (options.reauthIfCredentialed && credentialed)
    if (needsReauth && Date.now() - db.session.reauthAt > REAUTH_WINDOW_MS) {
      return apiError(ERROR_CODES.reauthRequired, 'Confirm it is you to continue.')
    }
    return fn(ctx, user)
  }
}

async function readJson(request: Request): Promise<Record<string, unknown>> {
  try {
    const body: unknown = await request.json()
    return typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function str(body: Record<string, unknown>, key: string): string {
  const value = body[key]
  return typeof value === 'string' ? value.trim() : ''
}

function isFilingRole(value: unknown): value is FilingRole {
  return typeof value === 'string' && (FILING_ROLES as readonly string[]).includes(value)
}

function startSession(user: MockUser): void {
  db.session = { userId: user.id, reauthAt: Date.now() }
}

function newChallenge(): string {
  const challenge = randomToken()
  db.challenges.add(challenge)
  return challenge
}

function credentialIdOf(body: Record<string, unknown>): string {
  const credential = body.credential
  if (typeof credential === 'object' && credential !== null && 'id' in credential) {
    const id = credential.id
    if (typeof id === 'string') return id
  }
  return ''
}

function findByCredential(
  credentialId: string,
): { user: MockUser; cred: MockCredential } | undefined {
  for (const user of db.users) {
    const cred = user.credentials.find((c) => c.credentialId === credentialId)
    if (cred) return { user, cred }
  }
  return undefined
}

function rpId(request: Request): string {
  return new URL(request.url).hostname
}

// --- Handlers ---------------------------------------------------------------------------------

export const handlers = [
  http.get(path('/health'), () =>
    HttpResponse.json({ status: 'ok', version: '0.0.0-mock', checks: { db: 'ok', redis: 'ok' } }),
  ),

  // Setup
  // Setup stays "needed" until the owner holds a credential, so a reload before the passkey step
  // can't strand the instance (the real API does the same).
  http.get(path('/setup/status'), () => {
    const owner = db.users.find((u) => u.role === 'owner')
    const ownerHasCredential = owner !== undefined && owner.credentials.length > 0
    return HttpResponse.json({ needs_setup: db.household === null || !ownerHasCredential })
  }),

  http.post(
    path('/setup'),
    publicRoute(async ({ request }) => {
      const body = await readJson(request)
      const existingOwner = db.users.find((u) => u.role === 'owner')
      if (db.household && existingOwner && existingOwner.credentials.length > 0)
        return apiError(ERROR_CODES.setupComplete, 'This instance is already set up.')
      if (str(body, 'setup_token') !== MOCK_SETUP_TOKEN) {
        audit('setup.attempt', 'failure')
        return apiError(ERROR_CODES.invalidSetupToken, 'That setup token is wrong or expired.')
      }
      // Resume: setup was claimed but the owner never registered a credential. The token holder
      // gets a fresh session for the existing owner (no second household), as the real API does.
      if (db.household && existingOwner) {
        startSession(existingOwner)
        audit('setup.resume')
        return HttpResponse.json(sessionJson(existingOwner), { status: 200 })
      }
      const householdName = str(body, 'household_name')
      const displayName = str(body, 'display_name')
      const email = str(body, 'email').toLowerCase()
      if (!householdName || !displayName || !email.includes('@')) {
        return apiError(
          ERROR_CODES.validationError,
          'Household name, your name and email are required.',
        )
      }
      db.household = { id: nextId('hh'), name: householdName }
      const person = {
        id: nextId('person'),
        display_name: displayName,
        filing_role: 'primary' as const,
      }
      db.people.push(person)
      const user: MockUser = {
        id: nextId('user'),
        personId: person.id,
        email,
        role: 'owner',
        password: null,
        totpEnabled: false,
        totpPending: false,
        recoveryCodes: [],
        credentials: [],
      }
      db.users.push(user)
      startSession(user)
      audit('setup.complete')
      return HttpResponse.json(sessionJson(user), { status: 201 })
    }),
  ),

  // Passkey registration (setup, invite accept, Security → Add)
  http.post(
    path('/auth/passkey/register/options'),
    authed(
      ({ request }, user) =>
        HttpResponse.json({
          challenge: newChallenge(),
          rp: { id: rpId(request), name: 'Holdfast' },
          user: { id: btoa(user.id).replace(/=+$/, ''), name: user.email, displayName: user.email },
          pubKeyCredParams: [
            { type: 'public-key', alg: -7 },
            { type: 'public-key', alg: -257 },
          ],
          timeout: 60000,
          attestation: 'none',
          excludeCredentials: user.credentials.map((c) => ({
            id: c.credentialId,
            type: 'public-key',
          })),
          authenticatorSelection: { residentKey: 'required', userVerification: 'required' },
        }),
      { reauthIfCredentialed: true },
    ),
  ),

  http.post(
    path('/auth/passkey/register/verify'),
    authed(
      async ({ request }, user) => {
        const body = await readJson(request)
        const credentialId = credentialIdOf(body)
        if (!credentialId || findByCredential(credentialId)) {
          return apiError(
            ERROR_CODES.passkeyVerificationFailed,
            'That passkey could not be verified.',
          )
        }
        const cred: MockCredential = {
          id: nextId('cred'),
          credentialId,
          nickname: str(body, 'nickname') || 'Passkey',
          created_at: now(),
          last_used_at: null,
        }
        user.credentials.push(cred)
        audit('auth.passkey.registered')
        return HttpResponse.json({
          credential: { id: cred.id, nickname: cred.nickname, created_at: cred.created_at },
        })
      },
      { reauthIfCredentialed: true },
    ),
  ),

  // Passkey login
  http.post(
    path('/auth/passkey/login/options'),
    publicRoute(({ request }) =>
      HttpResponse.json({
        challenge: newChallenge(),
        rpId: rpId(request),
        timeout: 60000,
        userVerification: 'required',
        allowCredentials: [],
      }),
    ),
  ),

  http.post(
    path('/auth/passkey/login/verify'),
    publicRoute(async ({ request }) => {
      const found = findByCredential(credentialIdOf(await readJson(request)))
      if (!found) {
        audit('auth.login.passkey', 'failure')
        return apiError(
          ERROR_CODES.passkeyVerificationFailed,
          'That passkey is not registered here.',
        )
      }
      found.cred.last_used_at = now()
      startSession(found.user)
      audit('auth.login.passkey')
      return HttpResponse.json({ user: userJson(found.user) })
    }),
  ),

  // Password + TOTP fallback
  http.post(
    path('/auth/password/login'),
    publicRoute(async ({ request }) => {
      if (db.failedLogins >= MOCK_LOGIN_LIMIT) {
        return apiError(ERROR_CODES.rateLimited, 'Too many attempts. Try again in 15 minutes.')
      }
      const body = await readJson(request)
      const user = db.users.find((u) => u.email === str(body, 'email').toLowerCase())
      const totp = str(body, 'totp_code')
      const recovery = str(body, 'recovery_code')
      let secondFactor = false
      if (user?.totpEnabled) {
        if (totp) secondFactor = totp === MOCK_TOTP_CODE
        else if (recovery && user.recoveryCodes.includes(recovery)) {
          secondFactor = true
          user.recoveryCodes = user.recoveryCodes.filter((c) => c !== recovery)
        }
      }
      if (
        !user ||
        user.password === null ||
        user.password !== str(body, 'password') ||
        !secondFactor
      ) {
        db.failedLogins += 1
        audit('auth.login.password', 'failure')
        return apiError(ERROR_CODES.invalidCredentials, 'Email, password or code is wrong.')
      }
      db.failedLogins = 0
      startSession(user)
      audit('auth.login.password')
      return HttpResponse.json({ user: userJson(user) })
    }),
  ),

  http.post(
    path('/auth/logout'),
    publicRoute(() => {
      if (db.session) audit('auth.logout')
      db.session = null
      return new HttpResponse(null, { status: 204 })
    }),
  ),

  http.get(
    path('/auth/me'),
    authed((_ctx, user) => {
      const session = db.session
      const body: MeResponse = {
        ...sessionJson(user),
        reauth_fresh: session !== null && Date.now() - session.reauthAt <= REAUTH_WINDOW_MS,
      }
      return HttpResponse.json(body)
    }),
  ),

  // Step-up
  http.post(
    path('/auth/reauth/passkey/options'),
    authed(({ request }, user) =>
      HttpResponse.json({
        challenge: newChallenge(),
        rpId: rpId(request),
        timeout: 60000,
        userVerification: 'required',
        allowCredentials: user.credentials.map((c) => ({ id: c.credentialId, type: 'public-key' })),
      }),
    ),
  ),

  http.post(
    path('/auth/reauth/passkey/verify'),
    authed(async ({ request }, user) => {
      const found = findByCredential(credentialIdOf(await readJson(request)))
      if (found?.user.id !== user.id || !db.session) {
        return apiError(ERROR_CODES.passkeyVerificationFailed, 'That passkey is not yours.')
      }
      db.session.reauthAt = Date.now()
      audit('auth.reauth.passkey')
      return new HttpResponse(null, { status: 204 })
    }),
  ),

  http.post(
    path('/auth/reauth/totp'),
    authed(async ({ request }, user) => {
      const code = str(await readJson(request), 'code')
      if (!user.totpEnabled || code !== MOCK_TOTP_CODE || !db.session) {
        return apiError(ERROR_CODES.invalidCode, 'That code did not match.')
      }
      db.session.reauthAt = Date.now()
      audit('auth.reauth.totp')
      return new HttpResponse(null, { status: 204 })
    }),
  ),

  // TOTP
  http.post(
    path('/auth/totp/setup'),
    authed(
      (_ctx, user) => {
        user.totpPending = true
        const secret = 'JBSWY3DPEHPK3PXP'
        const label = encodeURIComponent(`Holdfast:${user.email}`)
        return HttpResponse.json({
          secret,
          otpauth_uri: `otpauth://totp/${label}?secret=${secret}&issuer=Holdfast&algorithm=SHA1&digits=6&period=30`,
        })
      },
      { reauthIfCredentialed: true },
    ),
  ),

  http.post(
    path('/auth/totp/enable'),
    authed(
      async ({ request }, user) => {
        const code = str(await readJson(request), 'code')
        if (!user.totpPending || code !== MOCK_TOTP_CODE) {
          return apiError(
            ERROR_CODES.invalidCode,
            'That code did not match. Check the time on your device.',
          )
        }
        user.totpPending = false
        user.totpEnabled = true
        user.recoveryCodes = generateRecoveryCodes()
        audit('auth.totp.enabled')
        return HttpResponse.json({ recovery_codes: [...user.recoveryCodes] })
      },
      { reauthIfCredentialed: true },
    ),
  ),

  http.post(
    path('/auth/totp/disable'),
    authed(
      (_ctx, user) => {
        if (user.credentials.length === 0) {
          return apiError(ERROR_CODES.lastCredential, 'Add a passkey before turning off codes.')
        }
        user.totpEnabled = false
        user.recoveryCodes = []
        audit('auth.totp.disabled')
        return new HttpResponse(null, { status: 204 })
      },
      { reauth: true },
    ),
  ),

  http.post(
    path('/auth/password'),
    authed(
      async ({ request }, user) => {
        const password = str(await readJson(request), 'password')
        if (password.length < 12) {
          return apiError(ERROR_CODES.validationError, 'Use at least 12 characters.')
        }
        user.password = password
        audit('auth.password.set')
        return new HttpResponse(null, { status: 204 })
      },
      { reauth: true },
    ),
  ),

  // Passkeys
  http.get(
    path('/auth/passkeys'),
    authed((_ctx, user) => HttpResponse.json({ items: user.credentials.map(passkeyJson) })),
  ),

  http.delete(
    path('/auth/passkeys/:id'),
    authed(
      ({ params }, user) => {
        const cred = user.credentials.find((c) => c.id === params.id)
        if (!cred) return apiError(ERROR_CODES.notFound, 'No such passkey.')
        if (user.credentials.length === 1 && !user.totpEnabled) {
          return apiError(
            ERROR_CODES.lastCredential,
            'This is your only way in. Add another passkey or turn on codes first.',
          )
        }
        user.credentials = user.credentials.filter((c) => c.id !== cred.id)
        audit('auth.passkey.removed')
        return new HttpResponse(null, { status: 204 })
      },
      { reauth: true },
    ),
  ),

  // Household
  http.get(
    path('/household'),
    authed(() =>
      db.household
        ? HttpResponse.json({ ...db.household })
        : apiError(ERROR_CODES.notFound, 'No household.'),
    ),
  ),

  http.patch(
    path('/household'),
    authed(
      async ({ request }) => {
        const name = str(await readJson(request), 'name')
        if (!name || !db.household)
          return apiError(ERROR_CODES.validationError, 'Give the household a name.')
        db.household.name = name
        audit('household.renamed')
        return HttpResponse.json({ ...db.household })
      },
      { owner: true },
    ),
  ),

  http.get(
    path('/household/people'),
    authed(() =>
      HttpResponse.json({
        items: db.people.map((p) => personJson(p.id)).filter((p) => p !== undefined),
      }),
    ),
  ),

  http.post(
    path('/household/people'),
    authed(
      async ({ request }) => {
        const body = await readJson(request)
        const displayName = str(body, 'display_name')
        if (!displayName || !isFilingRole(body.filing_role)) {
          return apiError(ERROR_CODES.validationError, 'A name and a filing role are required.')
        }
        const person = {
          id: nextId('person'),
          display_name: displayName,
          filing_role: body.filing_role,
        }
        db.people.push(person)
        audit('household.person.created')
        return HttpResponse.json(personJson(person.id), { status: 201 })
      },
      { owner: true },
    ),
  ),

  http.patch(
    path('/household/people/:id'),
    authed(
      async ({ request, params }) => {
        const person = db.people.find((p) => p.id === params.id)
        if (!person) return apiError(ERROR_CODES.notFound, 'No such person.')
        const body = await readJson(request)
        if ('display_name' in body) {
          const displayName = str(body, 'display_name')
          if (!displayName) return apiError(ERROR_CODES.validationError, 'A name is required.')
          person.display_name = displayName
        }
        if ('filing_role' in body) {
          if (!isFilingRole(body.filing_role))
            return apiError(ERROR_CODES.validationError, 'Unknown filing role.')
          person.filing_role = body.filing_role
        }
        audit('household.person.updated')
        return HttpResponse.json(personJson(person.id))
      },
      { owner: true },
    ),
  ),

  http.delete(
    path('/household/people/:id'),
    authed(
      ({ params }) => {
        const person = db.people.find((p) => p.id === params.id)
        if (!person) return apiError(ERROR_CODES.notFound, 'No such person.')
        if (db.users.some((u) => u.personId === person.id)) {
          return apiError(
            ERROR_CODES.conflict,
            'This person has an account. Remove their access first.',
          )
        }
        db.people = db.people.filter((p) => p.id !== person.id)
        audit('household.person.deleted')
        return new HttpResponse(null, { status: 204 })
      },
      { owner: true },
    ),
  ),

  http.post(
    path('/household/people/:id/invite'),
    authed(
      async ({ request, params }) => {
        const person = db.people.find((p) => p.id === params.id)
        if (!person) return apiError(ERROR_CODES.notFound, 'No such person.')
        if (db.users.some((u) => u.personId === person.id)) {
          return apiError(ERROR_CODES.conflict, 'This person already has an account.')
        }
        const email = str(await readJson(request), 'email').toLowerCase()
        if (!email.includes('@'))
          return apiError(ERROR_CODES.validationError, 'Enter a valid email.')
        if (db.users.some((u) => u.email === email)) {
          return apiError(ERROR_CODES.conflict, 'That email already has an account.')
        }
        const token = randomToken()
        db.invites.push({ token, personId: person.id, email, used: false })
        audit('household.invite.created')
        // Token in the fragment: it never reaches a server log or a Referer header.
        return HttpResponse.json({ invite_url: `${new URL(request.url).origin}/invite#${token}` })
      },
      { owner: true, reauth: true },
    ),
  ),

  http.post(
    path('/invites/accept'),
    publicRoute(async ({ request }) => {
      const token = str(await readJson(request), 'token')
      const invite = db.invites.find((i) => i.token === token && !i.used)
      if (!invite)
        return apiError(ERROR_CODES.invalidInvite, 'This invite link is used, expired or wrong.')
      invite.used = true
      const user: MockUser = {
        id: nextId('user'),
        personId: invite.personId,
        email: invite.email,
        role: 'member',
        password: null,
        totpEnabled: false,
        totpPending: false,
        recoveryCodes: [],
        credentials: [],
      }
      db.users.push(user)
      startSession(user)
      audit('household.invite.accepted')
      return HttpResponse.json(sessionJson(user))
    }),
  ),

  // Audit
  http.get(
    path('/audit'),
    authed(
      ({ request }) => {
        const url = new URL(request.url)
        const limit = Math.min(
          Number.parseInt(url.searchParams.get('limit') ?? '50', 10) || 50,
          200,
        )
        const before = url.searchParams.get('before')
        const start = before ? db.audit.findIndex((a) => a.id === before) + 1 : 0
        const items = db.audit.slice(start, start + limit)
        const last = items.at(-1)
        const hasMore = start + limit < db.audit.length
        return HttpResponse.json({ items, next_before: hasMore && last ? last.id : null })
      },
      { owner: true },
    ),
  ),
]
