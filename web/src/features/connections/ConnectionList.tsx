import { useState } from 'react'

import {
  useConnections,
  useResolveReservation,
  type Connection,
  type OpenReservation,
} from '@/api/connections'
import { FormError } from '@/components/forms/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { AbandonForm } from '@/features/connections/AbandonForm'
import { SLOT_STATE_LABELS, STATUS_LABELS, linkErrorMessage } from '@/features/connections/copy'
import { useIsDesktop } from '@/hooks/useBreakpoint'

const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function ResolveError({ error }: { error: unknown }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
    >
      {linkErrorMessage(error)}
    </div>
  )
}

function ConnectionRow({ connection }: { connection: Connection }) {
  return (
    <li className="flex flex-col gap-1.5 py-3" data-connection={connection.id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Plaid</span>
        <Badge variant="outline">
          {connection.env === 'production' ? 'Real data' : 'Sandbox · fake data'}
        </Badge>
        <Badge variant="secondary">{STATUS_LABELS[connection.status] ?? connection.status}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        {connection.last_synced_at
          ? `Last synced ${DATE.format(new Date(connection.last_synced_at))}`
          : 'Not synced yet'}
        {' · '}
        <span className="font-mono tabular-nums">{connection.pending_accounts}</span> to map,{' '}
        <span className="font-mono tabular-nums">{connection.mapped_accounts}</span> mapped,{' '}
        <span className="font-mono tabular-nums">{connection.ignored_accounts}</span> ignored
      </p>
      {connection.slot_flag && (
        <p className="text-xs text-warning-text">
          Plaid counted this login as a second Item. It used an extra slot.
        </p>
      )}
      {connection.accounts.length > 0 && (
        <ul aria-label="Accounts" className="text-sm">
          {connection.accounts.map((a) => (
            <li key={a.id}>
              {a.name}
              {a.mask && <span className="font-mono"> ····{a.mask}</span>}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function ReservationRow({
  reservation,
  onRelease,
}: {
  reservation: OpenReservation
  onRelease: () => void
}) {
  const resolve = useResolveReservation()
  return (
    <li className="flex flex-col gap-2 py-3" data-reservation={reservation.id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Unfinished link</span>
        <Badge variant="outline">{SLOT_STATE_LABELS[reservation.state]}</Badge>
        <span className="text-sm text-muted-foreground">
          started {DATE.format(new Date(reservation.created_at))}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        Holdfast is holding a slot until Plaid says what happened.
      </p>
      {resolve.error && <ResolveError error={resolve.error} />}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={resolve.isPending}
          onClick={() => {
            resolve.mutate(reservation.id)
          }}
        >
          {resolve.isPending ? 'Asking Plaid…' : 'Check with Plaid'}
        </Button>
        <Button variant="ghost" onClick={onRelease}>
          I did not finish
        </Button>
      </div>
    </li>
  )
}

/** The household's connections, and (owner only) any link still waiting to be settled. */
export function ConnectionList({ isOwner }: { isOwner: boolean }) {
  const { data, isPending, error } = useConnections()
  const [releasing, setReleasing] = useState<string | null>(null)
  const resolve = useResolveReservation()
  const isDesktop = useIsDesktop()

  if (isPending) return <Skeleton className="h-20 w-full" />
  if (error) return <FormError error={error} />
  const reservations = isOwner ? data.open_reservations : []

  return (
    <div className="flex flex-col gap-2">
      {data.connections.length === 0 ? (
        <p className="text-sm text-muted-foreground">No bank is connected yet.</p>
      ) : (
        <ul aria-label="Connections" className="flex flex-col divide-y divide-border">
          {data.connections.map((c) => (
            <ConnectionRow key={c.id} connection={c} />
          ))}
        </ul>
      )}
      {reservations.length > 0 && (
        <ul aria-label="Unfinished links" className="flex flex-col divide-y divide-border">
          {reservations.map((r) => (
            <ReservationRow
              key={r.id}
              reservation={r}
              onRelease={() => {
                setReleasing(r.id)
              }}
            />
          ))}
        </ul>
      )}
      <Sheet
        open={releasing !== null}
        onOpenChange={(next) => {
          if (!next) setReleasing(null)
        }}
      >
        <SheetContent
          side={isDesktop ? 'right' : 'bottom'}
          className="max-h-[90dvh] gap-5 overflow-y-auto p-5 pb-8"
        >
          <SheetHeader className="p-0">
            <SheetTitle>Release this slot</SheetTitle>
            <SheetDescription>Only for a link that finished without a bank.</SheetDescription>
          </SheetHeader>
          {releasing && (
            <AbandonForm
              reservationId={releasing}
              resolving={resolve.isPending}
              onDone={() => {
                setReleasing(null)
              }}
              onResolve={() => {
                resolve.mutate(releasing, {
                  onSuccess: () => {
                    setReleasing(null)
                  },
                })
              }}
            />
          )}
          {releasing && resolve.error && <ResolveError error={resolve.error} />}
        </SheetContent>
      </Sheet>
    </div>
  )
}
