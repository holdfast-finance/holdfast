import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiStatusIndicator } from '@/components/layout/ApiStatusIndicator'

function renderIndicator() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ApiStatusIndicator />
    </QueryClientProvider>,
  )
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('<ApiStatusIndicator>', () => {
  it('shows "API ok" when /api/health returns 200', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse(200, {
          status: 'ok',
          version: '0.0.0',
          checks: { db: 'ok', redis: 'ok' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderIndicator()

    expect(await screen.findByText('API ok')).toBeInTheDocument()
    expect(screen.getByText('v0.0.0')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/health$/),
      expect.anything(),
    )
  })

  it('shows "API degraded" when a dependency is down (503)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(503, {
            status: 'error',
            version: '0.0.0',
            checks: { db: 'ok', redis: 'error' },
          }),
        ),
      ),
    )
    renderIndicator()
    expect(await screen.findByText('API degraded')).toBeInTheDocument()
  })

  it('shows "API unreachable" when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    renderIndicator()
    // useHealth retries once (1s backoff) before giving up.
    expect(
      await screen.findByText('API unreachable', undefined, { timeout: 4000 }),
    ).toBeInTheDocument()
  })

  it('shows "API unreachable" on an unexpected status or body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(502, { error: 'bad gateway' }))),
    )
    renderIndicator()
    // useHealth retries once (1s backoff) before giving up.
    expect(
      await screen.findByText('API unreachable', undefined, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})
