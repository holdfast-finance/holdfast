import { expect, test } from '@playwright/test'

import { addVirtualAuthenticator, needsSetup, readSetupToken } from './support.ts'

/*
 * Phase 1 acceptance: "create a household with 2 people, log in with a passkey", end to end
 * against the real stack. Needs a fresh instance (no household yet). To reset a dev stack:
 *   make down && docker volume rm holdfast_pgdata  (or your volume name) && make up && make migrate
 * then `docker compose restart api` if the API started before the migration.
 *
 * Run:  cd web && pnpm exec playwright install chromium && BASE_URL=https://localhost pnpm e2e
 *
 * Non-localhost APP_HOST: Chromium disables WebAuthn on pages with certificate errors, so trust
 * Caddy's local root CA in the test browser's NSS store (or use localhost).
 */

const runId = Date.now().toString(36)
const OWNER = {
  household: `E2E Household ${runId}`,
  name: 'Alex Example',
  email: `alex+${runId}@example.com`,
}
const SECOND_PERSON = 'Sam Example'

test('first run: claim, passkey, second person, sign out, passkey sign-in', async ({
  page,
  request,
}) => {
  test.skip(!(await needsSetup(request)), 'Instance is already set up; e2e needs a fresh stack.')
  const credentialCount = await addVirtualAuthenticator(page)
  const setupToken = await readSetupToken(request)

  // 1. Claim the server with the setup token.
  await page.goto('/')
  await expect(page).toHaveURL(/\/setup$/)
  await page.getByLabel('Setup token').fill(setupToken)
  await page.getByRole('button', { name: 'Continue' }).click()

  // 2. Household + owner.
  await page.getByLabel('Household name').fill(OWNER.household)
  await page.getByLabel('Your name').fill(OWNER.name)
  await page.getByLabel('Your email').fill(OWNER.email)
  await page.getByRole('button', { name: 'Create household' }).click()

  // 3. Passkey on the virtual authenticator.
  await page.getByLabel('Device name (optional)').fill('E2E virtual key')
  await page.getByRole('button', { name: 'Create passkey' }).click()

  // 4. Second person.
  await page.getByLabel('Their name').fill(SECOND_PERSON)
  await page.getByRole('button', { name: 'Add person' }).click()
  await expect(page).not.toHaveURL(/\/setup/)
  expect(await credentialCount()).toBe(1)

  await page.goto('/settings/household')
  await expect(page.getByText(OWNER.name).first()).toBeVisible()
  await expect(page.getByText(SECOND_PERSON).first()).toBeVisible()

  // 5. Sign out: the session is revoked server-side.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect((await page.request.get('/api/auth/me')).status()).toBe(401)

  // 6. Usernameless passkey sign-in.
  await page.getByRole('button', { name: 'Sign in with passkey' }).click()
  await expect(page).not.toHaveURL(/\/login/)

  const me = await page.request.get('/api/auth/me')
  expect(me.status()).toBe(200)
  const meBody = (await me.json()) as {
    user: { email: string; role: string }
    household: { name: string }
  }
  expect(meBody.user.email).toBe(OWNER.email)
  expect(meBody.user.role).toBe('owner')
  expect(meBody.household.name).toBe(OWNER.household)

  const people = await page.request.get('/api/household/people')
  const peopleBody = (await people.json()) as { items: { display_name: string }[] }
  expect(peopleBody.items.map((p) => p.display_name).sort()).toEqual(
    [OWNER.name, SECOND_PERSON].sort(),
  )

  // The cookie is HttpOnly + Secure + SameSite=Strict.
  const cookie = (await page.context().cookies()).find((c) => c.name === 'hf_session')
  expect(cookie?.httpOnly).toBe(true)
  expect(cookie?.secure).toBe(true)
  expect(cookie?.sameSite).toBe('Strict')
})
