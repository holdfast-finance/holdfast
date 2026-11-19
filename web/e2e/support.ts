import { execSync } from 'node:child_process'

import type { APIRequestContext, Page } from '@playwright/test'

/** Matches the one deliberate WARNING line the API prints at startup while setup is pending. */
const TOKEN_LINE = /HOLDFAST FIRST-RUN SETUP TOKEN: (\S+) ===/g

const DEFAULT_TOKEN_CMD = 'docker compose logs --no-log-prefix api'

/**
 * The first-run setup token, from (in order): SETUP_TOKEN, the APP_ENV=test hook, or the API log.
 * The hook route only exists when the API runs with APP_ENV=test; dev and prod answer 404.
 */
export async function readSetupToken(request: APIRequestContext): Promise<string> {
  const fromEnv = process.env.SETUP_TOKEN?.trim()
  if (fromEnv) return fromEnv

  const hook = await request.get('/api/test/setup-token')
  if (hook.ok()) {
    const body: unknown = await hook.json()
    if (typeof body === 'object' && body !== null && 'setup_token' in body) {
      const token = body.setup_token
      if (typeof token === 'string' && token) return token
    }
  }

  const cmd = process.env.SETUP_TOKEN_CMD ?? DEFAULT_TOKEN_CMD
  const logs = execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  const matches = [...logs.matchAll(TOKEN_LINE)]
  const last = matches.at(-1)?.[1]
  if (!last) {
    throw new Error(
      'No setup token: set SETUP_TOKEN, run the API with APP_ENV=test, or restart the API so it ' +
        'prints a fresh one (`docker compose restart api`).',
    )
  }
  return last
}

export async function needsSetup(request: APIRequestContext): Promise<boolean> {
  const res = await request.get('/api/setup/status')
  if (!res.ok()) throw new Error(`setup status failed: HTTP ${String(res.status())}`)
  const body: unknown = await res.json()
  return (
    typeof body === 'object' && body !== null && 'needs_setup' in body && body.needs_setup === true
  )
}

/**
 * A CTAP2 platform authenticator with resident keys and user verification, via the Chrome
 * DevTools Protocol. Returns a function that lists the credentials it holds.
 */
export async function addVirtualAuthenticator(page: Page): Promise<() => Promise<number>> {
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('WebAuthn.enable', { enableUI: false })
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  return async () => {
    const { credentials } = await cdp.send('WebAuthn.getCredentials', { authenticatorId })
    return credentials.length
  }
}
