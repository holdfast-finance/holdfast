import type { FilingRole } from '@/api/types'

export const FILING_ROLE_LABELS: Record<FilingRole, string> = {
  primary: 'Primary',
  spouse: 'Spouse / partner',
  dependent: 'Dependent',
}

/** Locale date for created/last-used columns. */
export function formatDate(iso: string | null): string {
  if (!iso) return 'Never'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}
