import { useState } from 'react'

import { ApiError } from '@/api/client'
import { useAcceptRun, type ReconcileNumbers } from '@/api/imports'
import { FormError } from '@/components/forms/Field'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Button } from '@/components/ui/button'
import { ReauthCancelledError } from '@/stores/reauth'
import { ReconcileFacts } from './Facts'
import { ReasonList } from './ReasonList'

/**
 * Accepting a flagged statement is a deliberate act: the sheet shows why it was flagged and the
 * difference first, says plainly that accepting does not reconcile it, and needs a tap on a
 * full-size confirm button.
 */
export function AcceptButton({
  runId,
  label,
  numbers,
  currency,
  reason,
}: {
  runId: string
  label: string
  numbers: ReconcileNumbers
  currency: string | null
  /** `undefined` when the runs list is the source: it carries no reason. */
  reason: string | null | undefined
}) {
  const [open, setOpen] = useState(false)
  const accept = useAcceptRun()

  const onOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) accept.reset()
  }

  return (
    <>
      <Button
        variant="outline"
        size="lg"
        onClick={() => {
          setOpen(true)
        }}
      >
        Review and accept
      </Button>
      <ResponsiveSheet
        open={open}
        onOpenChange={onOpenChange}
        title="Accept this statement?"
        description={label}
      >
        <div className="flex flex-col gap-4">
          <section aria-label="Why it was flagged" className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Why it was flagged</h3>
            <ReasonList reason={reason} />
          </section>
          <ReconcileFacts numbers={numbers} currency={currency} />
          <p className="text-sm">
            Accepting records that you looked at this statement. It does <strong>not</strong> make
            it reconciled: the difference stays, and the statement is marked Accepted instead.
          </p>
          <FormError
            error={
              accept.error instanceof ReauthCancelledError
                ? new ApiError(0, 'cancelled', 'Accept cancelled.')
                : accept.error
            }
          />
          <div className="flex flex-col gap-2">
            <Button
              size="lg"
              disabled={accept.isPending}
              onClick={() => {
                accept.mutate(runId, {
                  onSuccess: () => {
                    setOpen(false)
                  },
                })
              }}
            >
              {accept.isPending ? 'Accepting…' : 'Accept statement'}
            </Button>
            <Button
              variant="outline"
              size="lg"
              onClick={() => {
                onOpenChange(false)
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      </ResponsiveSheet>
    </>
  )
}
