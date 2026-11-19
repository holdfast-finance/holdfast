import { setupServer } from 'msw/node'

import { handlers } from '@/mocks/handlers'

/** MSW server for vitest. Lifecycle lives in `src/test/setup.ts`. */
export const server = setupServer(...handlers)
