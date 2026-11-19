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

/** Error codes the web client branches on. Anything else is shown by its `message`. */
export type KnownErrorCode =
  | 'unauthenticated'
  | 'reauth_required'
  | 'forbidden'
  | 'invalid_setup_token'
  | 'setup_complete'
  | 'invalid_credentials'
  | 'invalid_code'
  | 'invalid_invite'
  | 'passkey_verification_failed'
  | 'last_credential'
  | 'rate_limited'
  | 'validation_error'

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
