import { Money } from '@/components/Money'
import { Skeleton } from '@/components/ui/skeleton'
import type { Account } from '@/api/ledger'
import { accountSubtitle, formatSpan, groupAccounts, TYPE_LABEL } from './format'

const rowClass =
  'flex min-h-11 cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-muted has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring'

export function AccountsPanel({
  accounts,
  selected,
  showHidden,
  onSelect,
  onShowHidden,
}: {
  accounts: Account[]
  selected: string[]
  showHidden: boolean
  onSelect: (ids: string[]) => void
  onShowHidden: (show: boolean) => void
}) {
  const hiddenCount = accounts.filter((a) => a.is_hidden).length
  const shown = showHidden ? accounts : accounts.filter((a) => !a.is_hidden)

  const toggle = (id: string) => {
    onSelect(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id])
  }

  return (
    <div className="flex flex-col gap-4">
      <label className={rowClass}>
        <input
          type="checkbox"
          className="mt-0.5 size-5 shrink-0 accent-primary"
          checked={selected.length === 0}
          onChange={() => {
            onSelect([])
          }}
        />
        <span className="text-sm font-medium">All accounts</span>
      </label>
      {groupAccounts(shown).map((group) => (
        <fieldset key={group.type} className="flex flex-col gap-1">
          <legend className="px-2 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {TYPE_LABEL[group.type]}
          </legend>
          {group.items.map((account) => {
            const span = formatSpan(account)
            const subtitle = accountSubtitle(account)
            return (
              <label key={account.id} className={rowClass}>
                <input
                  type="checkbox"
                  className="mt-0.5 size-5 shrink-0 accent-primary"
                  checked={selected.includes(account.id)}
                  onChange={() => {
                    toggle(account.id)
                  }}
                />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium">{account.name}</span>
                    <span className="money shrink-0 text-xs text-muted-foreground">
                      {account.transaction_count}
                      <span className="sr-only"> transactions</span>
                    </span>
                  </span>
                  {subtitle && (
                    <span className="truncate text-xs text-muted-foreground">{subtitle}</span>
                  )}
                  {span && <span className="text-xs text-muted-foreground">{span}</span>}
                  {account.latest_balance !== null && (
                    <span className="text-xs text-muted-foreground">
                      Balance <Money amount={account.latest_balance} currency={account.currency} />
                    </span>
                  )}
                </span>
              </label>
            )
          })}
        </fieldset>
      ))}
      {hiddenCount > 0 && (
        <label className={rowClass}>
          <input
            type="checkbox"
            className="mt-0.5 size-5 shrink-0 accent-primary"
            checked={showHidden}
            onChange={(e) => {
              onShowHidden(e.target.checked)
            }}
          />
          <span className="text-sm text-muted-foreground">
            Show hidden accounts ({hiddenCount})
          </span>
        </label>
      )}
    </div>
  )
}

export function AccountsSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden="true">
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-full" />
    </div>
  )
}
