import { expect, test } from '@playwright/test'

import { addVirtualAuthenticator, needsSetup, readSetupToken } from './support.ts'

/*
 * Phase 4 W1, end to end against the real stack: set up a household, upload a synthetic CSV,
 * open Transactions, work through "Needs category", set a category, and see it stick after a
 * reload (so it came from the API, not from the optimistic cache).
 *
 * Like first-run.e2e.ts this needs a fresh instance (it claims the server) and skips otherwise.
 * Run:  cd web && BASE_URL=https://localhost pnpm e2e activity
 * The CSV uses the `generic` profile's columns (backend/app/importers/csv_profiles/generic.yaml).
 */

const runId = Date.now().toString(36)
const CSV = [
  'Example Posting Date,Example Payee,Example Net Amount,Example Running Balance',
  '2026-10-01,E2E Corner Market,-12.50,987.50',
  '2026-10-02,E2E Coffee Cart,-4.25,983.25',
  '',
].join('\n')

test('activity: upload, filter needs category, set a category', async ({ page, request }) => {
  test.skip(!(await needsSetup(request)), 'Instance is already set up; e2e needs a fresh stack.')
  await addVirtualAuthenticator(page)
  const setupToken = await readSetupToken(request)

  await page.goto('/')
  await page.getByLabel('Setup token').fill(setupToken)
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Household name').fill(`E2E Activity ${runId}`)
  await page.getByLabel('Your name').fill('Alex Example')
  await page.getByLabel('Your email').fill(`alex+act${runId}@example.com`)
  await page.getByRole('button', { name: 'Create household' }).click()
  await page.getByRole('button', { name: 'Create passkey' }).click()
  await page.getByLabel('Their name').fill('Sam Example')
  await page.getByRole('button', { name: 'Add person' }).click()
  await expect(page).not.toHaveURL(/\/setup/)

  // Upload a synthetic CSV into a new account.
  await page.goto('/import')
  await page.locator('input[type=file]').setInputFiles({
    name: `e2e-${runId}.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from(CSV),
  })
  await page.getByRole('button', { name: 'Import statement' }).click()
  await expect(page.getByText(/E2E Corner Market|2 transactions|imported/i).first()).toBeVisible()

  // Every imported row starts as "Needs category".
  await page.goto('/transactions')
  const market = page.getByRole('button', { name: /E2E Corner Market/ })
  await expect(market).toContainText('Needs category')

  await page.getByRole('button', { name: 'Needs category', exact: true }).click()
  await expect(page).toHaveURL(/uncategorized=1/)
  await expect(market).toBeVisible()

  // Set a category from the sheet.
  await market.click()
  const sheet = page.getByRole('dialog')
  await sheet.getByRole('radio', { name: 'Groceries' }).check()
  await sheet.getByRole('button', { name: 'Save category' }).click()
  await expect(sheet).toBeHidden()

  // It leaves the needs-category view, and survives a reload with its source.
  await expect(market).toBeHidden()
  await page.goto('/transactions')
  await expect(page.getByRole('button', { name: /E2E Corner Market/ })).toContainText('Groceries')
  const rows = await page.request.get('/api/transactions?uncategorized=true')
  const body = (await rows.json()) as { items: { description: string }[] }
  expect(body.items.map((t) => t.description)).not.toContain('E2E Corner Market')
})
