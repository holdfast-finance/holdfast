import { zodResolver } from '@hookform/resolvers/zod'
import { useEffect, useRef } from 'react'
import { useForm } from 'react-hook-form'

import { Field } from '@/components/forms/Field'
import { Input, NativeSelect } from '@/components/ui/input'
import { filterFormSchema, type FilterFormValues, type ViewFilters } from './filters'

const DEBOUNCE_MS = 300

/**
 * Date range, direction, amount range and the transfers switch. Valid edits apply to the URL
 * after a short pause (no Apply button); invalid ones show their message and wait.
 */
export function FiltersForm({
  filters,
  onApply,
}: {
  filters: ViewFilters
  onApply: (values: FilterFormValues) => void
}) {
  const form = useForm<FilterFormValues>({
    resolver: zodResolver(filterFormSchema),
    mode: 'onChange',
    defaultValues: {
      from: filters.from,
      to: filters.to,
      direction: filters.direction,
      min: filters.min,
      max: filters.max,
      hideTransfers: filters.hideTransfers,
    },
  })
  const apply = useRef(onApply)
  useEffect(() => {
    apply.current = onApply
  })

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const unsubscribe = form.subscribe({
      formState: { values: true },
      callback: ({ values }) => {
        clearTimeout(timer)
        const parsed = filterFormSchema.safeParse(values)
        if (!parsed.success) return
        timer = setTimeout(() => {
          apply.current(parsed.data)
        }, DEBOUNCE_MS)
      },
    })
    return () => {
      clearTimeout(timer)
      unsubscribe()
    }
  }, [form])

  const { errors } = form.formState
  return (
    <form
      className="grid grid-cols-2 gap-x-3 gap-y-4 md:grid-cols-1"
      aria-label="Transaction filters"
      onSubmit={(e) => {
        e.preventDefault()
      }}
      noValidate
    >
      <Field label="From" error={errors.from?.message}>
        {(p) => <Input type="date" {...p} {...form.register('from')} />}
      </Field>
      <Field label="To" error={errors.to?.message}>
        {(p) => <Input type="date" {...p} {...form.register('to')} />}
      </Field>
      <Field label="Direction" className="col-span-2 md:col-span-1">
        {(p) => (
          <NativeSelect {...p} {...form.register('direction')}>
            <option value="">All</option>
            <option value="inflow">Inflow</option>
            <option value="outflow">Outflow</option>
          </NativeSelect>
        )}
      </Field>
      <Field label="Min amount" error={errors.min?.message}>
        {(p) => <Input inputMode="decimal" placeholder="-200" {...p} {...form.register('min')} />}
      </Field>
      <Field label="Max amount" error={errors.max?.message}>
        {(p) => <Input inputMode="decimal" placeholder="-50" {...p} {...form.register('max')} />}
      </Field>
      <p className="col-span-2 -mt-2 text-xs text-muted-foreground md:col-span-1">
        Amounts are signed: outflow is negative, so -200 to -50 means spending between $50 and $200.
      </p>
      <label className="col-span-2 flex min-h-11 cursor-pointer items-center gap-3 md:col-span-1">
        <input
          type="checkbox"
          className="size-5 shrink-0 accent-primary"
          {...form.register('hideTransfers')}
        />
        <span className="text-sm">Hide transfers</span>
      </label>
    </form>
  )
}
