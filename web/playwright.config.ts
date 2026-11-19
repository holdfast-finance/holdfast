import { defineConfig, devices } from '@playwright/test'

/*
 * End-to-end tests against a real, running Holdfast stack (Caddy + API + web + Postgres + Redis).
 * Nothing is mocked: the browser talks to BASE_URL and WebAuthn runs on a CDP virtual
 * authenticator (Chromium only).
 *
 *   BASE_URL      default https://localhost (Caddy's self-signed dev cert is accepted)
 *   SETUP_TOKEN   optional; otherwise read from GET /api/test/setup-token (API with APP_ENV=test
 *                 and E2E_HOOKS=true only) or
 *                 from `docker compose logs api` (override with SETUP_TOKEN_CMD)
 *
 * The first-run spec needs a fresh instance (GET /api/setup/status -> needs_setup) and skips
 * with a reason otherwise. Files are named *.e2e.ts so vitest never picks them up.
 */
const baseURL = process.env.BASE_URL ?? 'https://localhost'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? 'list' : [['list'], ['html', { open: 'never' }]],
  timeout: 60_000,
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // WebAuthn refuses pages with certificate errors except on localhost. For another
        // APP_HOST, trust Caddy's local CA instead (see the note in e2e/first-run.e2e.ts).
        launchOptions: { args: ['--allow-insecure-localhost'] },
      },
    },
  ],
})
