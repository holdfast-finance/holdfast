/*
 * Hand-written types for the Phase 1 API contract (docs/phases/PHASE-1-contract.md).
 *
 * TODO(openapi): replace with types generated from the backend's OpenAPI document once the
 * auth-api workstream ships it (CLAUDE.md "Style"):
 *   pnpm dlx openapi-typescript http://localhost:8000/api/openapi.json -o src/api/schema.d.ts
 *
 * Fields the contract leaves open are resolved here and listed in the Phase 1 web report so the
 * backend matches them exactly. User JSON never carries password hashes, TOTP secrets or public keys.
 */
import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from '@simplewebauthn/browser'

export type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
}

export type FilingRole = 'primary' | 'spouse' | 'dependent'
export type UserRole = 'owner' | 'member'

export const FILING_ROLES: readonly FilingRole[] = ['primary', 'spouse', 'dependent']

/** `{"error": {"code": "...", "message": "..."}}` on every non-2xx response. */
export interface ErrorBody {
  error: { code: string; message: string }
}

/**
 * Stable error codes the web client branches on (`error.code` in the error body). The auth-api
 * workstream must emit exactly these strings with these statuses. Anything else is shown to the
 * user by its `message`, so new codes are safe to add; renaming one of these is a breaking change.
 */
export const ERROR_CODES = {
  /** 401: no session, or it expired or was revoked. The client drops cached data and goes to /login. */
  unauthenticated: 'unauthenticated',
  /** 403: the action needs a fresh step-up (reauth_at older than 5 minutes). The client prompts and retries once. */
  reauthRequired: 'reauth_required',
  /** 403: signed in, but not allowed (e.g. a member calling an owner-only route). */
  forbidden: 'forbidden',
  /** 403: a non-GET `/api/*` request arrived without `X-Holdfast-CSRF: 1`. */
  csrfFailed: 'csrf_failed',
  /** 403: `POST /setup` with a wrong or expired setup token. */
  invalidSetupToken: 'invalid_setup_token',
  /** 409: `POST /setup` after a household already exists. */
  setupComplete: 'setup_complete',
  /** 401: password login with an unknown email, wrong password, or wrong second factor (one code, no oracle). */
  invalidCredentials: 'invalid_credentials',
  /** 400: TOTP enable/step-up with a wrong code. */
  invalidCode: 'invalid_code',
  /** 404: invite token unknown, used or older than 24h. */
  invalidInvite: 'invalid_invite',
  /** 400: WebAuthn registration or assertion failed verification (bad challenge, unknown credential). */
  passkeyVerificationFailed: 'passkey_verification_failed',
  /** 409: removing the last passkey while TOTP is off would lock the user out. */
  lastCredential: 'last_credential',
  /** 409: e.g. an email that already belongs to a user, or deleting a person who has a user account. */
  conflict: 'conflict',
  /** 404: the referenced row does not exist in this household. */
  notFound: 'not_found',
  /** 429: login/assertion rate limit or lockout. `message` says when to retry. */
  rateLimited: 'rate_limited',
  /** 422: request body failed validation. `message` is human readable. */
  validationError: 'validation_error',
} as const

export type KnownErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]

/** Codes the client synthesizes itself (never sent by the API). */
export const CLIENT_ERROR_CODES = {
  networkError: 'network_error',
  serverError: 'server_error',
  httpError: 'http_error',
} as const

export interface Household {
  id: string
  name: string
}

export interface UserPerson {
  id: string
  display_name: string
  filing_role: FilingRole
}

export interface User {
  id: string
  email: string
  role: UserRole
  person: UserPerson | null
  /** Web addition: drives the Security page (TOTP on/off). */
  totp_enabled: boolean
  /** Web addition: whether a password is set (never the hash). */
  has_password: boolean
}

// GET /api/setup/status
export interface SetupStatus {
  needs_setup: boolean
}

// POST /api/setup
export interface SetupRequest {
  setup_token: string
  household_name: string
  display_name: string
  email: string
}
export interface SessionResponse {
  user: User
  household: Household
}

// GET /api/auth/me
export interface MeResponse {
  user: User
  household: Household
  reauth_fresh: boolean
}

// POST /api/auth/passkey/register/options
export interface PasskeyRegisterOptionsRequest {
  nickname?: string
}
// POST /api/auth/passkey/register/verify
export interface PasskeyRegisterVerifyRequest {
  credential: RegistrationResponseJSON
  nickname?: string
}
export interface PasskeyRegisterVerifyResponse {
  credential: { id: string; nickname: string; created_at: string }
}

// POST /api/auth/passkey/login/verify and /api/auth/reauth/passkey/verify
export interface PasskeyAssertionRequest {
  credential: AuthenticationResponseJSON
}
export interface UserResponse {
  user: User
}

// POST /api/auth/password/login: exactly one of totp_code / recovery_code.
export type PasswordLoginRequest =
  | { email: string; password: string; totp_code: string }
  | { email: string; password: string; recovery_code: string }

// GET /api/auth/passkeys
export interface Passkey {
  id: string
  nickname: string
  created_at: string
  last_used_at: string | null
}
export interface PasskeyList {
  items: Passkey[]
}

// POST /api/auth/totp/setup | /enable
export interface TotpSetupResponse {
  otpauth_uri: string
  secret: string
}
export interface TotpEnableRequest {
  code: string
}
export interface TotpEnableResponse {
  recovery_codes: string[]
}

// POST /api/auth/password
export interface SetPasswordRequest {
  password: string
}

// /api/household/people
export interface Person {
  id: string
  display_name: string
  filing_role: FilingRole
  /** Web addition: true once the person has a user account. */
  has_account: boolean
}
export interface PeopleList {
  items: Person[]
}
export interface PersonCreate {
  display_name: string
  filing_role: FilingRole
}
export type PersonUpdate = Partial<PersonCreate>

// POST /api/household/people/{id}/invite
export interface InviteRequest {
  email: string
}
export interface InviteResponse {
  invite_url: string
}

// POST /api/invites/accept
export interface InviteAcceptRequest {
  token: string
}

// GET /api/audit?limit&before
export interface AuditEntry {
  id: string
  created_at: string
  /** Dotted event name, e.g. `auth.login.passkey`, `household.person.created`. */
  action: string
  actor_display_name: string | null
  ip: string | null
  outcome: 'success' | 'failure'
}
export interface AuditPage {
  items: AuditEntry[]
  /** Opaque cursor: pass `before=<next_before>` to load the next (older) page; null at the end. */
  next_before: string | null
}

// PATCH /api/household (owner)
export interface HouseholdUpdate {
  name: string
}

/**
 * POST /api/auth/reauth/totp `{code}` → 204. Web addition: the contract says reauth_at is
 * refreshed by "a passkey/TOTP step-up" but only lists the passkey endpoints. Users who signed in
 * with password + TOTP and have no passkey on this device need this one.
 */
export interface TotpReauthRequest {
  code: string
}
