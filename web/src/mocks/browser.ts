import { setupWorker } from 'msw/browser'

import { MOCK_SETUP_TOKEN, MOCK_TOTP_CODE, db, type MockState } from '@/mocks/db'
import { handlers } from '@/mocks/handlers'
import { seedImportRuns } from '@/mocks/imports'
import { seedLedger } from '@/mocks/ledger'

/*
 * Dev-only mock API (`VITE_MOCK_API=1 pnpm dev`). main.tsx imports this module behind
 * `import.meta.env.DEV`, so it is never part of a production build. The worker script itself is
 * served by a dev-only Vite middleware (vite.config.ts), not from public/.
 *
 * State survives reloads in sessionStorage so the setup → passkey → dashboard flow can be walked
 * through by hand. Close the tab to start over.
 */

const STORAGE_KEY = 'holdfast.mock-api'

type Persisted = Omit<MockState, 'challenges'>

function load(): void {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const saved = JSON.parse(raw) as Persisted
    Object.assign(db, saved, { challenges: new Set<string>() })
  } catch {
    // Private mode or corrupt state: start fresh.
  }
}

function save(): void {
  try {
    const persisted: Persisted = {
      household: db.household,
      people: db.people,
      users: db.users,
      invites: db.invites,
      audit: db.audit,
      accounts: db.accounts,
      transactions: db.transactions,
      importRuns: db.importRuns,
      importedFiles: db.importedFiles,
      plaid: db.plaid,
      connections: db.connections,
      session: db.session,
      failedLogins: db.failedLogins,
      seq: db.seq,
    }
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(persisted))
  } catch {
    // Storage unavailable: state lives for this page load only.
  }
}

export async function startMockApi(): Promise<void> {
  load()
  if (db.accounts.length === 0) {
    seedLedger()
    seedImportRuns()
  }
  const worker = setupWorker(...handlers)
  worker.events.on('response:mocked', save)
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true })
  console.info(
    `[holdfast] Mock API on. Setup token: ${MOCK_SETUP_TOKEN}. TOTP code: ${MOCK_TOTP_CODE}.`,
  )
}
