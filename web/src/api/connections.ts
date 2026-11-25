import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, readData } from '@/api/client'
import type { components } from '@/api/schema'
import { queryKeys } from '@/app/queryClient'
import { withReauth } from '@/stores/reauth'

type Schemas = components['schemas']

export type ConnectionsOut = Schemas['ConnectionsOut']
export type Connection = Schemas['ConnectionOut']
export type OpenReservation = Schemas['OpenReservationOut']
export type LinkPlan = Schemas['LinkPlanOut']
export type LoginKind = Schemas['LinkPlanIn']['login_kind']
export type SlotOut = Schemas['SlotOut']
export type LinkTokenBody = Schemas['LinkTokenIn']

/** The exact phrase that ends an unfinished link (backend `ABANDON_PHRASE`). */
export const ABANDON_PHRASE = 'I did not finish this link'
export const SLOT_TOTAL = 10

/** The phrase for taking slot `count + 1` of 10 (backend `slot_phrase`). */
export function slotPhrase(count: number): string {
  return `use slot ${String(count + 1)} of ${String(SLOT_TOTAL)}`
}

/**
 * The slot count the phrase is built from: the larger of Holdfast's ledger and the number the
 * owner reads off their own slot log. Either one alone can under-count (ADR 0034).
 */
export function slotCount(ledgerCount: number, ownerCount: number): number {
  return Math.max(ledgerCount, ownerCount)
}

export function useConnections() {
  return useQuery({
    queryKey: queryKeys.connections,
    queryFn: ({ signal }) => readData(api.GET('/api/connections', { signal })),
  })
}

/** A dry call: no Plaid request, no reservation. */
export function useLinkPlan(loginKind: LoginKind | null) {
  return useQuery({
    queryKey: [...queryKeys.connections, 'link-plan', loginKind] as const,
    queryFn: ({ signal }) =>
      readData(
        api.POST('/api/connections/plaid/link-plan', {
          body: { login_kind: loginKind ?? 'bank_card', mode: 'new' },
          signal,
        }),
      ),
    enabled: loginKind !== null,
    staleTime: 0,
    gcTime: 0,
  })
}

/** Needs a fresh step-up; `withReauth` prompts and retries once. */
export function createLinkToken(body: LinkTokenBody) {
  return withReauth(() => readData(api.POST('/api/connections/plaid/link-token', { body })))
}

export function useExchange() {
  const client = useQueryClient()
  return useMutation({
    // The public token is the mutation's variable: don't let the cache keep it.
    gcTime: 0,
    mutationFn: (args: { publicToken: string; reservationId: string }) =>
      // Step-up is enforced when the link token is created, not here.
      readData(
        api.POST('/api/connections/plaid/exchange', {
          body: { public_token: args.publicToken, reservation_id: args.reservationId },
        }),
      ),
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.connections }),
  })
}

/** Ask Plaid what became of a reservation. Never decides anything by itself in the browser. */
export function useResolveReservation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (reservationId: string) =>
      readData(
        api.POST('/api/connections/plaid/reservations/{reservation_id}/resolve', {
          params: { path: { reservation_id: reservationId } },
        }),
      ),
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.connections }),
  })
}

export function useAbandonReservation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (args: { reservationId: string; confirmPhrase: string }) =>
      withReauth(() =>
        readData(
          api.POST('/api/connections/plaid/reservations/{reservation_id}/abandon', {
            params: { path: { reservation_id: args.reservationId } },
            body: { confirm_phrase: args.confirmPhrase },
          }),
        ),
      ),
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.connections }),
  })
}
