import { zodResolver } from '@hookform/resolvers/zod'
import { useMemo, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'

import type { Account, CategoryNode, Transaction } from '@/api/ledger'
import { Money } from '@/components/Money'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { indexCategories, pickerGroups, READ_ONLY_REASON, sourcePhrase } from './categories'
import { formatDay } from './format'

const pickSchema = z.object({ categoryId: z.string().min(1, 'Pick a category') })
type PickValues = z.infer<typeof pickSchema>

/**
 * One transaction: what it is, where its category came from, and (when the person may edit it)
 * the picker and "Hand back to automation". Read-only rows get the reason and nothing to press.
 */
export function TransactionSheet({
  transaction,
  account,
  categories,
  onClose,
  onSetCategory,
  onRelease,
}: {
  transaction: Transaction | null
  account: Account | undefined
  categories: CategoryNode[]
  onClose: () => void
  onSetCategory: (t: Transaction, categoryId: string) => void
  onRelease: (t: Transaction) => void
}) {
  return (
    <ResponsiveSheet
      open={transaction !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Transaction"
      description={transaction ? transaction.description : undefined}
    >
      {transaction && (
        <SheetBody
          // A different row, or its category changing underneath, restarts the form.
          key={`${transaction.id}:${transaction.category_id ?? ''}`}
          t={transaction}
          account={account}
          categories={categories}
          onSetCategory={onSetCategory}
          onRelease={onRelease}
        />
      )}
    </ResponsiveSheet>
  )
}

function SheetBody({
  t,
  account,
  categories,
  onSetCategory,
  onRelease,
}: {
  t: Transaction
  account: Account | undefined
  categories: CategoryNode[]
  onSetCategory: (t: Transaction, categoryId: string) => void
  onRelease: (t: Transaction) => void
}) {
  const index = useMemo(() => indexCategories(categories), [categories])
  const current = t.category_id ? index.get(t.category_id) : undefined
  const [search, setSearch] = useState('')
  const form = useForm<PickValues>({
    resolver: zodResolver(pickSchema),
    defaultValues: { categoryId: t.category_id ?? '' },
  })
  const chosen = useWatch({ control: form.control, name: 'categoryId' })
  const groups = pickerGroups(categories, search)

  return (
    <div className="flex flex-col gap-5">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Amount</dt>
        <dd>
          <Money amount={t.amount} currency={t.currency} showPlus />
        </dd>
        <dt className="text-muted-foreground">Date</dt>
        <dd>{formatDay(t.posted_date)}</dd>
        <dt className="text-muted-foreground">Account</dt>
        <dd>{account ? account.name : 'Unknown account'}</dd>
        <dt className="text-muted-foreground">Category</dt>
        <dd>
          {t.is_transfer
            ? 'Transfer'
            : current
              ? `${current.group ? `${current.group} / ` : ''}${current.name}`
              : 'Needs category'}
        </dd>
        {t.category_id && (
          <>
            <dt className="text-muted-foreground">Set by</dt>
            <dd>{sourcePhrase(t.category_source)}</dd>
          </>
        )}
      </dl>

      {t.has_splits && (
        <p className="text-sm text-muted-foreground">
          This transaction is split across categories. Splits can't be edited here yet.
        </p>
      )}

      {!t.can_edit ? (
        <p role="note" className="rounded-card border border-border bg-muted p-3 text-sm">
          {READ_ONLY_REASON}
        </p>
      ) : (
        <>
          {t.category_source === 'user' && (
            <div className="flex flex-col gap-1">
              <Button
                variant="outline"
                size="lg"
                onClick={() => {
                  onRelease(t)
                }}
              >
                Hand back to automation
              </Button>
              <p className="text-xs text-muted-foreground">
                Keeps the category for now. A rule or your bank can change it later.
              </p>
            </div>
          )}
          <form
            aria-label="Choose a category"
            className="flex flex-col gap-4"
            noValidate
            onSubmit={(e) => {
              void form.handleSubmit((v) => {
                onSetCategory(t, v.categoryId)
              })(e)
            }}
          >
            <Input
              type="search"
              aria-label="Search categories"
              placeholder="Search categories"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
              }}
            />
            <div className="flex max-h-[40dvh] flex-col gap-3 overflow-y-auto pr-1">
              {groups.length === 0 && (
                <p className="text-sm text-muted-foreground">No category matches that.</p>
              )}
              {groups.map((g) => (
                <fieldset key={g.id} className="flex flex-col">
                  <legend className="pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    {g.label}
                  </legend>
                  {g.options.map((o) => (
                    <label
                      key={o.id}
                      className={cn(
                        'flex min-h-11 cursor-pointer items-center gap-3 rounded-card px-2 text-sm hover:bg-muted',
                        chosen === o.id && 'bg-muted font-medium',
                      )}
                    >
                      <input
                        type="radio"
                        value={o.id}
                        className="size-5 shrink-0 accent-primary"
                        {...form.register('categoryId')}
                      />
                      {o.name}
                      {o.id === t.category_id && (
                        <span className="ml-auto text-xs text-muted-foreground">Current</span>
                      )}
                    </label>
                  ))}
                </fieldset>
              ))}
            </div>
            {form.formState.errors.categoryId && (
              <p role="alert" className="text-sm text-destructive-text">
                {form.formState.errors.categoryId.message}
              </p>
            )}
            <Button type="submit" size="lg" disabled={!chosen || chosen === t.category_id}>
              Save category
            </Button>
          </form>
        </>
      )}
    </div>
  )
}
