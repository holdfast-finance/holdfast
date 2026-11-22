import type { Account, AccountType } from '@/api/ledger'

export const TYPE_ORDER: AccountType[] = ['depository', 'credit', 'investment', 'loan', 'other']

export const TYPE_LABEL: Record<AccountType, string> = {
  depository: 'Cash',
  credit: 'Credit cards',
  investment: 'Investments',
  loan: 'Loans',
  other: 'Other',
}

/** Calendar dates arrive as "YYYY-MM-DD". Format them as UTC so no timezone can shift the day. */
function utc(date: string): Date {
  const [y = 0, m = 1, d = 1] = date.split('-').map((part) => Number.parseInt(part, 10))
  return new Date(Date.UTC(y, m - 1, d))
}

export function formatDay(date: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'UTC',
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(utc(date))
}

export function formatShortDate(date: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'UTC',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(utc(date))
}

export function formatSpan(account: Account): string | null {
  const { earliest_posted_date: from, latest_posted_date: to } = account
  if (!from || !to) return null
  return from === to ? formatShortDate(from) : `${formatShortDate(from)} to ${formatShortDate(to)}`
}

/** "First Example Bank ··0000", whichever parts exist. */
export function accountSubtitle(account: Account): string {
  return [account.institution_name, account.mask ? `··${account.mask}` : null]
    .filter(Boolean)
    .join(' ')
}

export function groupAccounts(accounts: Account[]): { type: AccountType; items: Account[] }[] {
  return TYPE_ORDER.map((type) => ({
    type,
    items: accounts.filter((a) => a.type === type),
  })).filter((g) => g.items.length > 0)
}
