import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { db, seedHousehold } from '@/mocks/db'
import { MOCK_CHECK_LIMIT, MOCK_REQUEST_ID, MOCK_SUGGESTED_REDIRECT } from '@/mocks/plaid'
import { renderApp, renderSignedIn } from '@/test/render'

/** Seed first (seeding resets the mock), then shape the Plaid scenario, then render. */
function renderWith(change: (plaid: typeof db.plaid) => void) {
  seedHousehold({ signedIn: true })
  change(db.plaid)
  return renderApp('/settings/connections')
}

async function step(title: string) {
  const list = await screen.findByRole('list', { name: 'Plaid setup steps' })
  const heading = await within(list).findByRole('heading', { name: title })
  const item = heading.closest('li')
  if (!item) throw new Error(`no step row for ${title}`)
  return within(item)
}

async function runCheck(name = 'Run check') {
  await userEvent.click(await screen.findByRole('button', { name }))
}

describe('Settings → Connections', () => {
  it('is reachable from the settings tabs', async () => {
    renderSignedIn('/settings/household')
    await userEvent.click(await screen.findByRole('link', { name: 'Connections' }))
    expect(await screen.findByRole('heading', { name: 'Plaid' })).toBeVisible()
  })

  it('shows the steps in order from configuration, before any check', async () => {
    renderSignedIn('/settings/connections')
    const list = await screen.findByRole('list', { name: 'Plaid setup steps' })
    expect(
      within(list)
        .getAllByRole('heading')
        .map((h) => h.textContent),
    ).toEqual([
      'Add your Plaid keys',
      'Plaid accepts the keys',
      'Products on your plan',
      'Redirect URI',
      'Bank login slots',
    ])
    expect((await step('Add your Plaid keys')).getByText('Done')).toBeVisible()
    expect((await step('Plaid accepts the keys')).getByText('Not checked')).toBeVisible()
    expect(screen.getByText('Sandbox · fake data')).toBeVisible()
    expect(screen.getByText(/Run the check to try them/)).toBeVisible()
    expect(db.plaid.checks).toBe(0)
  })

  it('tells a new install where to get keys', async () => {
    renderWith((plaid) => {
      plaid.hasClientId = false
      plaid.hasSecret = false
    })
    const keys = await step('Add your Plaid keys')
    expect(keys.getByText('Needs you')).toBeVisible()
    expect(keys.getByText(/PLAID_CLIENT_ID is empty/)).toBeVisible()
    expect(keys.getByRole('link', { name: /Create a Plaid account/ })).toHaveAttribute(
      'href',
      'https://dashboard.plaid.com/signup',
    )
  })

  it('names the production secret when that is the one missing', async () => {
    renderWith((plaid) => {
      plaid.env = 'production'
      plaid.hasSecret = false
    })
    const keys = await step('Add your Plaid keys')
    expect(keys.getByText(/PLAID_SECRET_PRODUCTION is empty/)).toBeVisible()
    expect(keys.getByRole('link', { name: /Apply for the Trial plan/ })).toBeVisible()
  })

  it('runs the check and reports ready', async () => {
    renderSignedIn('/settings/connections')
    await runCheck()
    expect((await step('Plaid accepts the keys')).getByText('Done')).toBeVisible()
    const products = within(await screen.findByRole('list', { name: 'Plaid products' }))
    expect(products.getByText('Transactions').closest('li')).toHaveTextContent('Transactions · On')
    expect(products.getByText('Balance').closest('li')).toHaveTextContent('Balance · Included')
    expect(await screen.findByText(/Plaid is set up for Sandbox/)).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeEnabled()
    expect(db.plaid.checks).toBe(1)
  })

  it('explains rejected keys and shows the Plaid request id', async () => {
    renderWith((plaid) => {
      plaid.keys = 'rejected'
    })
    await runCheck()
    const keys = await step('Plaid accepts the keys')
    expect(await keys.findByText('Needs you')).toBeVisible()
    expect(keys.getByText(/a Sandbox secret does not work in Production/)).toBeVisible()
    expect(keys.getByText(MOCK_REQUEST_ID)).toBeVisible()
    expect((await step('Products on your plan')).getByText('Not checked')).toBeVisible()
  })

  it('says when the server could not reach Plaid', async () => {
    renderWith((plaid) => {
      plaid.keys = 'unreachable'
    })
    await runCheck()
    const keys = await step('Plaid accepts the keys')
    expect(await keys.findByText('Blocked')).toBeVisible()
    expect(keys.getByText(/couldn't reach Plaid/)).toBeVisible()
  })

  it('lists a product the plan lacks and stays ready', async () => {
    renderWith((plaid) => {
      plaid.notEnabled = ['investments']
    })
    await runCheck()
    const products = await step('Products on your plan')
    expect(await products.findByText('Done')).toBeVisible()
    expect(products.getByText(/Holdfast skips those/)).toBeVisible()
    expect(products.getByText('Investments').closest('li')).toHaveTextContent(
      'Investments · Not on your plan',
    )
    expect(await screen.findByText(/Plaid is set up/)).toBeVisible()
  })

  it('offers the suggested redirect address to copy when none is set', async () => {
    renderSignedIn('/settings/connections')
    const redirect = await step('Redirect URI')
    expect(redirect.getByText('Optional')).toBeVisible()
    expect(redirect.getByText(MOCK_SUGGESTED_REDIRECT)).toBeVisible()
    expect(redirect.getByRole('button', { name: 'Copy address' })).toBeVisible()
  })

  it('flags a redirect URI that Plaid has not got on its allowlist', async () => {
    renderWith((plaid) => {
      plaid.redirectUri = 'https://money.example.com/plaid/oauth-return'
    })
    await runCheck()
    const redirect = await step('Redirect URI')
    expect(await redirect.findByText('Needs you')).toBeVisible()
    expect(redirect.getByText('https://money.example.com/plaid/oauth-return')).toBeVisible()
    expect(redirect.getByRole('link', { name: /Open allowed redirect URIs/ })).toBeVisible()
    expect(screen.queryByText(/Plaid is set up/)).not.toBeInTheDocument()
  })

  it('shows slot use in production and blocks at the limit', async () => {
    renderWith((plaid) => {
      plaid.env = 'production'
      plaid.slotsUsed = 10
    })
    const slots = await step('Bank login slots')
    expect(slots.getByText('Blocked')).toBeVisible()
    expect(slots.getByText('10 of 10')).toBeVisible()
    expect(slots.getByText(/new ones cannot be added/)).toBeVisible()
    expect(screen.getByText('Production · real data')).toBeVisible()
  })

  it('surfaces the rate limit from the server', async () => {
    renderWith((plaid) => {
      plaid.checks = MOCK_CHECK_LIMIT
    })
    await runCheck()
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many attempts')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run check' })).toBeEnabled()
    })
  })

  it('tells a member that only the owner sets up connections', async () => {
    renderSignedIn('/settings/connections', { as: 'member' })
    expect(await screen.findByText(/Only the household owner/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Run check' })).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Plaid setup steps' })).not.toBeInTheDocument()
  })

  it('points people without Plaid at statement import', async () => {
    renderSignedIn('/settings/connections')
    expect(await screen.findByRole('link', { name: 'Import statements' })).toHaveAttribute(
      'href',
      '/import',
    )
  })
})
