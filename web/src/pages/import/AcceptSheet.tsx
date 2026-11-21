import { useRef, useState, type RefObject } from 'react'

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
  fromRun = false,
  fallbackFocus,
  onAccepted,
}: {
  runId: string
  label: string
  numbers: ReconcileNumbers
  currency: string | null
  reason: string | null
  fromRun?: boolean
  fallbackFocus: RefObject<HTMLElement | null>
  onAccepted: (runId: string) => void
}) {
  const trigger = useRef<HTMLButtonElement>(null)
  const accepted = useRef(false)
  const [open, setOpen] = useState(false)
  const accept = useAcceptRun((acceptedRunId) => {
    accepted.current = true
    setOpen(false)
    onAccepted(acceptedRunId)
  })

  const onOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) accept.reset()
  }

  return (
    <>
      <Button
        ref={trigger}
        variant="outline"
        size="lg"
        onClick={() => {
          accepted.current = false
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
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          const target = accepted.current ? fallbackFocus.current : trigger.current
          target?.focus()
        }}
      >
        <div className="flex flex-col gap-4">
          <section aria-label="Why it was flagged" className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Why it was flagged</h3>
            <ReasonList reason={reason} numbers={numbers} fromRun={fromRun} />
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
                accept.mutate(runId)
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
