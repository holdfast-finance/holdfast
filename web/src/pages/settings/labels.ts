import { FILING_ROLES, type FilingRole } from '@/api/types'

export const FILING_ROLE_LABELS: Record<FilingRole, string> = {
  primary: 'Primary',
  spouse: 'Spouse / partner',
  dependent: 'Dependent',
}

/** Roles you can give someone. "Primary" belongs to the owner from setup and is never picked. */
export type AssignableFilingRole = Exclude<FilingRole, 'primary'>
export const ASSIGNABLE_FILING_ROLES: readonly AssignableFilingRole[] = FILING_ROLES.filter(
  (r): r is AssignableFilingRole => r !== 'primary',
)

/** Locale date for created/last-used columns. */
export function formatDate(iso: string | null): string {
  if (!iso) return 'Never'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Locale date and time, for the audit list. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

const AUDIT_LABELS: Record<string, string> = {
  'setup.complete': 'Set up this server',
  'setup.attempt': 'Setup attempt',
  'auth.login.passkey': 'Signed in with a passkey',
  'auth.login.password': 'Signed in with password + code',
  'auth.login.recovery': 'Signed in with a recovery code',
  'auth.logout': 'Signed out',
  'auth.reauth.passkey': 'Confirmed with a passkey',
  'auth.reauth.totp': 'Confirmed with a code',
  'auth.passkey.registered': 'Added a passkey',
  'auth.passkey.removed': 'Removed a passkey',
  'auth.totp.enabled': 'Turned on authenticator codes',
  'auth.totp.disabled': 'Turned off authenticator codes',
  'auth.password.set': 'Set a password',
  'auth.password.changed': 'Changed the password',
  'household.renamed': 'Renamed the household',
  'household.person.created': 'Added a person',
  'household.person.updated': 'Edited a person',
  'household.person.deleted': 'Removed a person',
  'household.invite.created': 'Created an invite link',
  'household.invite.accepted': 'Accepted an invite',
}

/** Human label for an audit action code. Unknown codes get a readable fallback. */
export function auditLabel(action: string): string {
  const known = AUDIT_LABELS[action]
  if (known) return known
  const words = action.replace(/[._]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : action
}
