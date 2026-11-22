import { useId } from 'react'

import { NEW_ACCOUNT, type ImportAccount, type UploadTarget } from '@/api/imports'

/*
 * Where a CSV or OFX/QFX goes (it has no account of its own to match): an account already
 * imported, or a new one. A CSV also says whether it is a bank account or a card; an OFX file
 * states that itself. 44px touch targets, one column at 375px.
 */

const FIELD =
  'min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm focus-visible:ring-3 focus-visible:ring-ring'

export function AccountPicker({
  accounts,
  loading,
  isCsv,
  value,
  onChange,
  disabled,
}: {
  accounts: ImportAccount[]
  loading: boolean
  isCsv: boolean
  value: UploadTarget
  onChange: (target: UploadTarget) => void
  disabled: boolean
}) {
  const selectId = useId()
  const hintId = useId()
  const kinds = [
    { value: 'bank', label: 'Bank account' },
    { value: 'card', label: 'Card' },
  ] as const
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={selectId} className="text-sm font-medium">
          Add this file to
        </label>
        <select
          id={selectId}
          className={FIELD}
          value={value.account}
          disabled={disabled}
          aria-describedby={hintId}
          onChange={(e) => {
            const chosen = accounts.find((a) => a.id === e.target.value)
            // An existing account fixes the kind, and the server checks the two agree.
            onChange({ account: e.target.value, accountKind: chosen?.type ?? value.accountKind })
          }}
        >
          <option value={NEW_ACCOUNT}>New account</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
              {a.mask ? ` ending ${a.mask}` : ''} ({a.type === 'card' ? 'card' : 'bank'})
            </option>
          ))}
        </select>
        <p id={hintId} className="text-xs text-muted-foreground">
          {loading
            ? 'Loading your accounts…'
            : 'Pick the account from your statements this file belongs to, so rows already imported are not repeated.'}
        </p>
      </div>
      {isCsv && value.account === NEW_ACCOUNT && (
        <fieldset className="flex flex-col gap-1.5" disabled={disabled}>
          <legend className="text-sm font-medium">The new account is a</legend>
          <div className="grid grid-cols-2 gap-2">
            {kinds.map((k) => (
              <label
                key={k.value}
                className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-input px-3 text-sm has-[:checked]:border-primary has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring"
              >
                <input
                  type="radio"
                  name="account-kind"
                  value={k.value}
                  checked={value.accountKind === k.value}
                  onChange={() => {
                    onChange({ ...value, accountKind: k.value })
                  }}
                />
                {k.label}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </div>
  )
}
