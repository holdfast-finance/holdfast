import { zodResolver } from '@hookform/resolvers/zod'
import { AlertCircle } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { ABANDON_PHRASE, useAbandonReservation, type SlotOut } from '@/api/connections'
import { Field } from '@/components/forms/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { abandonErrorMessage, isAbandonConflict } from '@/features/connections/copy'

const schema = z.object({
  phrase: z.string().refine((v) => v.trim() === ABANDON_PHRASE, {
    message: `Type "${ABANDON_PHRASE}" to continue.`,
  }),
})
type Values = z.infer<typeof schema>

/**
 * Release a reservation the owner says made no bank connection. Step-up and the exact phrase are
 * required. The server asks Plaid first; if Plaid can't answer (502) the slot stays held and this
 * can be retried, and if Link did make something (409) the right move is to resolve instead.
 */
export function AbandonForm({
  reservationId,
  onDone,
  onResolve,
  resolving,
}: {
  reservationId: string
  onDone: (slot: SlotOut) => void
  onResolve: () => void
  resolving?: boolean
}) {
  const abandon = useAbandonReservation()
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { phrase: '' } })
  const error = form.formState.errors.phrase?.message
  return (
    <form
      noValidate
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        void form.handleSubmit((values) => {
          abandon.mutate(
            { reservationId, confirmPhrase: values.phrase.trim() },
            { onSuccess: onDone },
          )
        })(event)
      }}
    >
      <p className="text-sm text-muted-foreground">
        Release this slot only if you saw Link close without linking anything. Holdfast asks Plaid
        first.
      </p>
      <Field label="Confirmation phrase" hint={`Type: ${ABANDON_PHRASE}`} error={error}>
        {(props) => (
          <Input {...props} autoComplete="off" spellCheck={false} {...form.register('phrase')} />
        )}
      </Field>
      {abandon.error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
        >
          <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{abandonErrorMessage(abandon.error)}</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="destructive" disabled={abandon.isPending}>
          {abandon.isPending
            ? 'Asking Plaid…'
            : abandon.error && !isAbandonConflict(abandon.error)
              ? 'Try again'
              : 'Release this slot'}
        </Button>
        {isAbandonConflict(abandon.error) && (
          <Button type="button" disabled={resolving} onClick={onResolve}>
            Check with Plaid
          </Button>
        )}
      </div>
    </form>
  )
}
