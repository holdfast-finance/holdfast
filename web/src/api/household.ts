import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  AuditPage,
  Household,
  HouseholdUpdate,
  InviteRequest,
  InviteResponse,
  PeopleList,
  Person,
  PersonCreate,
  PersonUpdate,
} from '@/api/types'
import { queryKeys } from '@/app/queryClient'
import { withReauth } from '@/stores/reauth'

export function useHousehold() {
  return useQuery({
    queryKey: queryKeys.household,
    queryFn: ({ signal }) => api<Household>('/household', { signal }),
  })
}

export function useRenameHousehold() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: HouseholdUpdate) =>
      api<Household>('/household', { method: 'PATCH', body }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.household, exact: true })
      await client.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function usePeople() {
  return useQuery({
    queryKey: queryKeys.people,
    queryFn: ({ signal }) => api<PeopleList>('/household/people', { signal }),
  })
}

function personPath(id: string): string {
  return `/household/people/${encodeURIComponent(id)}`
}

export async function createPerson(body: PersonCreate): Promise<Person> {
  return api<Person>('/household/people', { method: 'POST', body })
}

export function useCreatePerson() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: createPerson,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  })
}

export function useUpdatePerson() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PersonUpdate }) =>
      api<Person>(personPath(id), { method: 'PATCH', body }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.people })
      await client.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function useDeletePerson() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<undefined>(personPath(id), { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  })
}

export function useInvitePerson() {
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: InviteRequest }) =>
      withReauth(() =>
        api<InviteResponse>(`${personPath(id)}/invite`, { method: 'POST', body }),
      ),
  })
}

export function useAudit(enabled: boolean, limit = 20) {
  return useQuery({
    queryKey: [...queryKeys.audit, limit],
    queryFn: ({ signal }) => api<AuditPage>('/audit', { signal, query: { limit } }),
    enabled,
  })
}
