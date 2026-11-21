import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { PAGES } from '@/app/nav'
import { mockViewport } from '@/test/matchMedia'
import { renderSignedIn as renderApp } from '@/test/render'

describe('AppShell layout', () => {
  it('shows the bottom tab bar and no sidebar at 375px', async () => {
    mockViewport(375)
    renderApp('/')

    const tabbar = await screen.findByTestId('mobile-tabbar')
    expect(screen.queryByTestId('desktop-sidebar')).not.toBeInTheDocument()
    const tabs = within(tabbar)
      .getAllByRole('link')
      .map((a) => a.textContent)
    expect(tabs).toEqual(['Dashboard', 'Transactions', 'Budgets', 'Advisor'])
    expect(within(tabbar).getByRole('button', { name: 'More' })).toBeInTheDocument()
  })

  it('opens the More sheet with the remaining pages on mobile', async () => {
    mockViewport(375)
    renderApp('/')
    await userEvent.click(await screen.findByRole('button', { name: 'More' }))

    const sheet = await screen.findByRole('dialog')
    const more = within(sheet).getByRole('navigation', { name: 'More pages' })
    for (const label of ['Forecast', 'Investments', 'Crypto', 'Settings', 'Sync health']) {
      expect(within(more).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })

  it('shows the sidebar in brand order and no tab bar on desktop', async () => {
    mockViewport(1440)
    renderApp('/')

    const sidebar = await screen.findByTestId('desktop-sidebar')
    expect(screen.queryByTestId('mobile-tabbar')).not.toBeInTheDocument()
    const nav = within(sidebar).getByRole('navigation', { name: 'Main' })
    const primary = within(nav).getAllByRole('list')[0]
    if (!primary) throw new Error('missing primary list')
    expect(
      within(primary)
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual([
      'Overview',
      'Transactions',
      'Import',
      'Budgets',
      'Forecast',
      'Investments',
      'Crypto',
      'Advisor',
      'Settings',
    ])
    expect(within(sidebar).getByRole('link', { name: 'holdfast home' })).toBeInTheDocument()
  })

  it.each(
    Object.values(PAGES).filter(
      (p) =>
        p.id !== 'overview' && p.id !== 'settings' && p.id !== 'transactions' && p.id !== 'import',
    ),
  )('routes $path to a placeholder for Phase $phase', async (page) => {
    mockViewport(1440)
    renderApp(page.path)
    expect(await screen.findByRole('heading', { level: 1, name: page.label })).toBeVisible()
    expect(screen.getByText(`Coming in Phase ${page.phase}`)).toBeInTheDocument()
  })

  it('labels the overview as demo data', async () => {
    renderApp('/')
    expect(await screen.findByText('$[NET WORTH]')).toBeInTheDocument()
    expect(screen.getAllByText(/demo data/).length).toBeGreaterThan(0)
  })
})
