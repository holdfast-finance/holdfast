import { zodResolver } from '@hookform/resolvers/zod'
import { Ban, Check } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'

import {
  SLOT_TOTAL,
  createLinkToken,
  slotCount,
  slotPhrase,
  useExchange,
  useLinkPlan,
  useResolveReservation,
  type LinkPlan,
  type LoginKind,
} from '@/api/connections'
import { Field, FormError } from '@/components/forms/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { AbandonForm } from '@/features/connections/AbandonForm'
import {
  COUNT_SOURCE_LABELS,
  LOGIN_KINDS,
  PRODUCT_LABELS,
  STATUS_LABELS,
  linkErrorMessage,
} from '@/features/connections/copy'
import { openPlaidLink } from '@/features/connections/plaidLink'
import { useIsDesktop } from '@/hooks/useBreakpoint'

type Env = 'sandbox' | 'production'

const COUNT = /^(?:\d|10)$/

function formSchema(ledgerCount: number, production: boolean) {
  return z
    .object({ ownerCount: z.string(), notDuplicate: z.boolean(), phrase: z.string() })
    .superRefine((v, ctx) => {
      if (!production) return
      const owner = v.ownerCount.trim()
      if (!COUNT.test(owner)) {
        ctx.addIssue({
          code: 'custom',
          path: ['ownerCount'],
          message: 'Enter how many Plaid slots you have used, from 0 to 10.',
        })
        return
      }
      if (!v.notDuplicate) {
        ctx.addIssue({
          code: 'custom',
          path: ['notDuplicate'],
          message: 'Confirm this login is not one you already linked.',
        })
      }
      const n = slotCount(ledgerCount, Number(owner))
      if (n >= SLOT_TOTAL) {
        ctx.addIssue({
          code: 'custom',
          path: ['ownerCount'],
          message: 'All 10 slots are used.',
        })
        return
      }
      if (v.phrase.trim() !== slotPhrase(n)) {
        ctx.addIssue({
          code: 'custom',
          path: ['phrase'],
          message: `Type "${slotPhrase(n)}" to continue.`,
        })
      }
    })
}
type Values = z.infer<ReturnType<typeof formSchema>>

type Outcome =
  | { kind: 'linked' }
  | { kind: 'released' }
  /** Link closed and Plaid has not said what happened: the slot is still held. */
  | { kind: 'held'; reservationId: string; reason: 'open' | 'unknown' | 'unreachable' | 'failed' }

type Stage = 'plan' | 'opening' | 'linking' | 'finishing'

function PlanSummary({ plan }: { plan: LinkPlan }) {
  return (
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      <div className="flex flex-col gap-1">
        <dt className="text-muted-foreground">Data requested</dt>
        <dd className="flex flex-wrap gap-1.5">
          {plan.products.map((p) => (
            <Badge key={p} variant="outline">
              {PRODUCT_LABELS[p] ?? p}
            </Badge>
          ))}
          {plan.optional_products.map((p) => (
            <Badge key={p} variant="secondary">
              {PRODUCT_LABELS[p] ?? p} · if offered
            </Badge>
          ))}
        </dd>
      </div>
      <div className="flex flex-col gap-1">
        <dt className="text-muted-foreground">History requested</dt>
        <dd>
          {plan.days_requested === null ? (
            'Whatever the login holds'
          ) : (
            <>
              <span className="font-mono tabular-nums">{plan.days_requested}</span> days
            </>
          )}
        </dd>
      </div>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <dt className="text-muted-foreground">Slots Holdfast has counted</dt>
        <dd>
          <span className="font-mono tabular-nums">
            {plan.ledger_count} of {SLOT_TOTAL}
          </span>{' '}
          <span className="text-muted-foreground">
            (source: {COUNT_SOURCE_LABELS[plan.count_source] ?? plan.count_source})
          </span>
        </dd>
      </div>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <dt className="text-muted-foreground">Already connected</dt>
        <dd>
          {plan.existing_connections.length === 0 ? (
            'Nothing yet.'
          ) : (
            <ul className="flex flex-col gap-1">
              {plan.existing_connections.map((c) => (
                <li key={c.id}>
                  Plaid · {c.env === 'production' ? 'real data' : 'Sandbox'} ·{' '}
                  {STATUS_LABELS[c.status] ?? c.status}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </div>
    </dl>
  )
}

/**
 * The whole link flow in one sheet: plan, slot phrase, step-up, Plaid Link, then resolve.
 * All state lives here, not in the sheet, so the sheet can step aside while Link is open: a modal
 * focus trap and `pointer-events: none` on the page would otherwise lock Plaid's frame out.
 */
export function LinkFlow({ env, onClose }: { env: Env; onClose: () => void }) {
  const isDesktop = useIsDesktop()
  const production = env === 'production'
  const [kind, setKind] = useState<LoginKind>('bank_card')
  const plan = useLinkPlan(kind)
  const [stage, setStage] = useState<Stage>('plan')
  const [error, setError] = useState<unknown>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [showAbandon, setShowAbandon] = useState(false)
  const exchange = useExchange()
  const resolve = useResolveReservation()

  const ledger = plan.data?.ledger_count ?? 0
  const schema = useMemo(() => formSchema(ledger, production), [ledger, production])
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { ownerCount: '', notDuplicate: false, phrase: '' },
  })
  const ownerRaw = useWatch({ control: form.control, name: 'ownerCount' }).trim()
  const owner = COUNT.test(ownerRaw) ? Number(ownerRaw) : 0
  const n = slotCount(ledger, owner)
  const noSlots = production && plan.data !== undefined && n >= SLOT_TOTAL

  const settle = async (reservationId: string) => {
    setStage('finishing')
    try {
      const slot = await resolve.mutateAsync(reservationId)
      if (slot.state === 'linked') setOutcome({ kind: 'linked' })
      else if (slot.state === 'abandoned') setOutcome({ kind: 'released' })
      else
        setOutcome({
          kind: 'held',
          reservationId,
          reason: slot.state === 'unknown' ? 'unknown' : 'open',
        })
    } catch {
      setOutcome({ kind: 'held', reservationId, reason: 'unreachable' })
    }
    setStage('plan')
  }

  const start = async (values: Values) => {
    setError(null)
    setStage('opening')
    let reservationId: string | null = null
    try {
      const token = await createLinkToken({
        login_kind: kind,
        mode: 'new',
        not_a_duplicate: production && values.notDuplicate,
        ...(production
          ? {
              owner_count: Number(values.ownerCount.trim()),
              confirm_phrase: values.phrase.trim(),
            }
          : {}),
      })
      reservationId = token.reservation_id
      const reservation = token.reservation_id
      setStage('linking')
      await openPlaidLink(token.link_token, {
        onSuccess: (publicToken) => {
          setStage('finishing')
          if (!reservation) return
          exchange.mutate(
            { publicToken, reservationId: reservation },
            {
              onSuccess: () => {
                setOutcome({ kind: 'linked' })
                setStage('plan')
              },
              // The public token is gone after this call. Plaid still knows what happened.
              onError: () => {
                void settle(reservation)
              },
            },
          )
        },
        // Closing Link proves nothing: ask the server, which asks Plaid. Never abandon here.
        onExit: () => {
          if (reservation) void settle(reservation)
          else setStage('plan')
        },
      })
    } catch (e) {
      if (reservationId) {
        // The slot is reserved but Link never opened: say so, and let the owner resolve it.
        setOutcome({ kind: 'held', reservationId, reason: 'failed' })
      } else {
        setError(e)
      }
      setStage('plan')
    }
  }

  const sheetOpen = stage !== 'linking'
  const busy = stage !== 'plan'

  return (
    <Sheet
      open={sheetOpen}
      onOpenChange={(next) => {
        if (!next && !busy) onClose()
      }}
    >
      <SheetContent
        side={isDesktop ? 'right' : 'bottom'}
        className="max-h-[92dvh] gap-5 overflow-y-auto p-5 pb-8"
      >
        <SheetHeader className="p-0">
          <SheetTitle>Link a bank</SheetTitle>
          <SheetDescription>
            {production
              ? 'Real data. Each login takes one of your 10 Plaid slots, and removing it never gives the slot back.'
              : 'Sandbox. Fake data, no slot needed.'}
          </SheetDescription>
        </SheetHeader>

        {outcome?.kind === 'linked' && (
          <div className="flex flex-col gap-3" role="status">
            <p className="flex items-center gap-2 font-medium">
              <Check aria-hidden="true" className="size-4 text-success-text" /> Linked.
            </p>
            <p className="text-sm text-muted-foreground">
              The accounts are waiting for you to map them. Nothing syncs until you do.
            </p>
            <Button onClick={onClose}>Done</Button>
          </div>
        )}

        {outcome?.kind === 'released' && (
          <div className="flex flex-col gap-3" role="status">
            <p className="font-medium">No bank was linked.</p>
            <p className="text-sm text-muted-foreground">
              Plaid confirmed nothing was created, so the slot is free again.
            </p>
            <Button onClick={onClose}>Done</Button>
          </div>
        )}

        {outcome?.kind === 'held' && (
          <div className="flex flex-col gap-3">
            <p role="status" className="text-sm">
              {outcome.reason === 'unreachable'
                ? "Link closed, and Plaid couldn't be reached to confirm. Holdfast is keeping the slot held."
                : outcome.reason === 'failed'
                  ? "Plaid Link didn't open, so nothing was linked. Holdfast is holding a slot for it."
                  : 'Link closed before Plaid reported a result. Holdfast is keeping the slot held until it knows.'}
            </p>
            {!showAbandon ? (
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy}
                  onClick={() => {
                    void settle(outcome.reservationId)
                  }}
                >
                  Check with Plaid
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowAbandon(true)
                  }}
                >
                  I did not finish
                </Button>
                <Button variant="ghost" onClick={onClose}>
                  Close
                </Button>
              </div>
            ) : (
              <AbandonForm
                reservationId={outcome.reservationId}
                resolving={busy}
                onResolve={() => {
                  setShowAbandon(false)
                  void settle(outcome.reservationId)
                }}
                onDone={() => {
                  setShowAbandon(false)
                  setOutcome({ kind: 'released' })
                }}
              />
            )}
          </div>
        )}

        {outcome === null && (
          <form
            noValidate
            className="flex flex-col gap-5"
            onSubmit={(event) => {
              void form.handleSubmit(start)(event)
            }}
          >
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-sm font-medium">What are you linking?</legend>
              {LOGIN_KINDS.map((k) => (
                <label
                  key={k.id}
                  className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-border px-3 py-2.5 has-[:checked]:border-primary"
                >
                  <input
                    type="radio"
                    name="login_kind"
                    value={k.id}
                    checked={kind === k.id}
                    disabled={busy}
                    onChange={() => {
                      setKind(k.id)
                    }}
                    className="mt-1 size-4"
                  />
                  <span className="flex flex-col">
                    <span className="text-sm font-medium">{k.label}</span>
                    <span className="text-xs text-muted-foreground">{k.hint}</span>
                  </span>
                </label>
              ))}
            </fieldset>

            {plan.isPending && <Skeleton className="h-32 w-full" />}
            {plan.isError && <FormError error={plan.error} />}
            {plan.data && <PlanSummary plan={plan.data} />}

            {production && plan.data && noSlots && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
              >
                <Ban aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                <span>
                  All 10 Plaid slots are used. Holdfast never asks for an 11th. Import statements
                  instead, or free a slot in your Plaid account first.
                </span>
              </div>
            )}

            {production && plan.data && ledger < SLOT_TOTAL && (
              <div className="flex flex-col gap-4">
                <Field
                  label="Slots you have used, by your own count"
                  hint={`Read it off your slot log. Holdfast counts ${String(ledger)}; it uses whichever number is larger.`}
                  error={form.formState.errors.ownerCount?.message}
                >
                  {(props) => (
                    <Input
                      {...props}
                      inputMode="numeric"
                      autoComplete="off"
                      className="font-mono tabular-nums"
                      {...form.register('ownerCount')}
                    />
                  )}
                </Field>
                <div className="flex flex-col gap-1">
                  <label className="flex min-h-11 items-start gap-3 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1 size-4"
                      {...form.register('notDuplicate')}
                    />
                    <span>This login is not one I have already linked.</span>
                  </label>
                  {form.formState.errors.notDuplicate?.message && (
                    <p className="text-xs text-destructive-text">
                      {form.formState.errors.notDuplicate.message}
                    </p>
                  )}
                </div>
                {!noSlots && (
                  <Field
                    label="Slot phrase"
                    hint={`Type: ${slotPhrase(n)}`}
                    error={form.formState.errors.phrase?.message}
                  >
                    {(props) => (
                      <Input
                        {...props}
                        autoComplete="off"
                        spellCheck={false}
                        {...form.register('phrase')}
                      />
                    )}
                  </Field>
                )}
              </div>
            )}

            {error !== null && (
              <div
                role="alert"
                className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
              >
                {linkErrorMessage(error)}
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={busy || !plan.data || noSlots}>
                {stage === 'opening'
                  ? 'Getting a link ready…'
                  : stage === 'finishing'
                    ? 'Finishing…'
                    : 'Open Plaid Link'}
              </Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>
                Cancel
              </Button>
            </div>
          </form>
        )}
      </SheetContent>
    </Sheet>
  )
}
