import { MutationCache, QueryCache, QueryClient, type QueryKey } from '@tanstack/react-query'

import { ApiError } from '@/api/client'

/** Query keys shared by the hooks in `src/api/hooks.ts` and the 401 handler below. */
export const queryKeys = {
  me: ['auth', 'me'] as const,
  setupStatus: ['setup', 'status'] as const,
  passkeys: ['auth', 'passkeys'] as const,
  household: ['household'] as const,
  people: ['household', 'people'] as const,
  audit: ['audit'] as const,
  plaidSetup: ['plaid', 'setup'] as const,
}

function sameKey(a: QueryKey, b: QueryKey): boolean {
  return a.length === b.length && a.every((part, i) => part === b[i])
}

function shouldRetry(failureCount: number, error: unknown): boolean {
  // 4xx answers are final (bad input, no session, reauth needed); retry transient failures once.
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < 1
}

/**
 * Any 401 from the API means the session is gone (expired, revoked, logged out elsewhere).
 * Drop the cached `/auth/me` so the route guard redirects to /login, and clear everything else
 * so no household data lingers in memory after the session ends.
 */
function onUnauthorized(client: QueryClient, error: unknown, key?: QueryKey): void {
  if (!(error instanceof ApiError) || error.status !== 401) return
  if (key && sameKey(key, queryKeys.me)) return
  if (client.getQueryData(queryKeys.me) === null) return
  client.removeQueries({ predicate: (q) => !sameKey(q.queryKey, queryKeys.me) })
  client.setQueryData(queryKeys.me, null)
}

export function createQueryClient(): QueryClient {
  // The caches need the client and the client needs the caches; a late-bound ref breaks the loop.
  const ref: { client?: QueryClient } = {}
  const client = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (ref.client) onUnauthorized(ref.client, error, query.queryKey)
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        if (ref.client) onUnauthorized(ref.client, error)
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: shouldRetry,
      },
    },
  })
  ref.client = client
  return client
}
