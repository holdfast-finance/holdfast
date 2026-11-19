import type { QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Providers } from '@/app/Providers'
import { createQueryClient } from '@/app/queryClient'
import { routes } from '@/app/routes'
import { seedHousehold, type SeedOptions } from '@/mocks/db'

/** Same 401 handling as production, but no retries or polling. */
export function createTestQueryClient(): QueryClient {
  const client = createQueryClient()
  client.setDefaultOptions({
    queries: { ...client.getDefaultOptions().queries, retry: false, refetchInterval: false },
  })
  return client
}

/**
 * Render the full app (router + guard) at a route against the MSW mock API.
 * Seed the mock first (`seedHousehold`, or leave it empty for a fresh instance), or pass `seed`.
 */
export function renderApp(path = '/', options: { seed?: SeedOptions } = {}) {
  if (options.seed) seedHousehold(options.seed)
  const client = createTestQueryClient()
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return {
    router,
    client,
    ...render(
      <Providers client={client}>
        <RouterProvider router={router} />
      </Providers>,
    ),
  }
}

/** Render signed in as the household owner (most app tests). */
export function renderSignedIn(path = '/', seed: SeedOptions = {}) {
  return renderApp(path, { seed: { signedIn: true, ...seed } })
}
