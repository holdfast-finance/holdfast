import type { components } from '@/api/schema'

type Schemas = components['schemas']

export type AuditEntry = Schemas['AuditEntry']
export type FilingRole = Schemas['PersonOut']['filing_role']
export type MeResponse = Schemas['MeOut']
export type Passkey = Schemas['PasskeyOut']
export type PasswordLoginRequest = Schemas['PasswordLoginIn']
export type Person = Schemas['PersonOut']
export type SessionResponse = Schemas['SessionOut']
export type User = Schemas['UserOut']
export type UserRole = User['role']

// Exhaustive on purpose: adding or removing a filing role in the schema fails typecheck.
const FILING_ROLE = {
  primary: 'primary',
  spouse: 'spouse',
  dependent: 'dependent',
} as const satisfies Record<FilingRole, FilingRole>

export const FILING_ROLES: readonly FilingRole[] = [
  FILING_ROLE.primary,
  FILING_ROLE.spouse,
  FILING_ROLE.dependent,
]

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
  /** 413: request body over 1 MiB (Caddy and the API both enforce it). */
  payloadTooLarge: 'payload_too_large',
} as const

export type KnownErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]

/** Codes the client synthesizes itself (never sent by the API). */
export const CLIENT_ERROR_CODES = {
  networkError: 'network_error',
  serverError: 'server_error',
  httpError: 'http_error',
} as const
