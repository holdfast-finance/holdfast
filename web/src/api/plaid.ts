import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, readData } from '@/api/client'
import type { components } from '@/api/schema'
import { queryKeys } from '@/app/queryClient'

type Schemas = components['schemas']

export type PlaidSetup = Schemas['PlaidSetupOut']
export type PlaidSetupStep = Schemas['PlaidSetupStep']
export type PlaidProductStatus = Schemas['PlaidProductStatus']

/** The checklist from configuration alone. The server makes no call to Plaid for this. */
export function usePlaidSetup(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.plaidSetup,
    queryFn: ({ signal }) => readData(api.GET('/api/plaid/setup', { signal })),
    enabled,
    // A check result replaces this cache entry; a background refetch would wipe it back to
    // "unchecked", so the list only reloads when the page is opened again.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

/** Ask Plaid: keys, each product, the redirect URI. The result replaces the cached checklist. */
export function useCheckPlaidSetup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => readData(api.POST('/api/plaid/setup/check')),
    onSuccess: (data) => {
      client.setQueryData(queryKeys.plaidSetup, data)
    },
  })
}
