import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, readData } from '@/api/client'
import type { components } from '@/api/schema'
import { queryKeys } from '@/app/queryClient'
import { withReauth } from '@/stores/reauth'

type Schemas = components['schemas']

export function useHousehold() {
  return useQuery({
    queryKey: queryKeys.household,
    queryFn: ({ signal }) => readData(api.GET('/api/household', { signal })),
  })
}

export function useRenameHousehold() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['HouseholdUpdate']) =>
      readData(api.PATCH('/api/household', { body })),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.household, exact: true })
      await client.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function usePeople() {
  return useQuery({
    queryKey: queryKeys.people,
    queryFn: ({ signal }) => readData(api.GET('/api/household/people', { signal })),
  })
}

export async function createPerson(body: Schemas['PersonCreate']) {
  return readData(api.POST('/api/household/people', { body }))
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
    mutationFn: ({ id, body }: { id: string; body: Schemas['PersonUpdate'] }) =>
      readData(
        api.PATCH('/api/household/people/{person_id}', {
          body,
          params: { path: { person_id: id } },
        }),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.people })
      await client.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function useDeletePerson() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await api.DELETE('/api/household/people/{person_id}', { params: { path: { person_id: id } } })
    },
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  })
}

export function useInvitePerson() {
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas['InviteIn'] }) =>
      withReauth(() =>
        readData(
          api.POST('/api/household/people/{person_id}/invite', {
            body,
            params: { path: { person_id: id } },
          }),
        ),
      ),
  })
}

export function useAudit(enabled: boolean, limit = 20) {
  return useQuery({
    queryKey: [...queryKeys.audit, limit],
    queryFn: ({ signal }) =>
      readData(api.GET('/api/audit', { signal, params: { query: { limit } } })),
    enabled,
  })
}
