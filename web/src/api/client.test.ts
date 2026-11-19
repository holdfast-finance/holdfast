import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { api, ApiError, apiUrl, CSRF_HEADER, errorMessage, isApiError } from '@/api/client'
import { ERROR_CODES } from '@/api/types'
import { seedHousehold } from '@/mocks/db'
import { server } from '@/mocks/node'
import { notAllowed } from '@/test/webauthn'

interface Seen {
  method: string
  csrf: string | null
  contentType: string | null
}

function recordRequests(): Seen[] {
  const seen: Seen[] = []
  server.events.on('request:start', ({ request }) => {
    seen.push({
      method: request.method,
      csrf: request.headers.get(CSRF_HEADER),
      contentType: request.headers.get('Content-Type'),
    })
  })
  return seen
}

describe('api client', () => {
  it('sends X-Holdfast-CSRF: 1 on every non-GET method, and not on GET', async () => {
    seedHousehold({ signedIn: true })
    const seen = recordRequests()
    server.use(http.all('*/api/echo', () => HttpResponse.json({ ok: true })))
    await api('/echo')
    await api('/echo', { method: 'POST', body: {} })
    await api('/echo', { method: 'PATCH', body: { name: 'x' } })
    await api('/echo', { method: 'PUT', body: {} })
    await api('/echo', { method: 'DELETE' })
    server.events.removeAllListeners()

    expect(seen.map((s) => [s.method, s.csrf])).toEqual([
      ['GET', null],
      ['POST', '1'],
      ['PATCH', '1'],
      ['PUT', '1'],
      ['DELETE', '1'],
    ])
    expect(seen[1]?.contentType).toBe('application/json')
  })

  it('sends the header on real auth calls (the mock API rejects any non-GET without it)', async () => {
    seedHousehold({ signedIn: true })
    await expect(api('/auth/totp/setup', { method: 'POST', body: {} })).resolves.toBeDefined()
    // A raw fetch without the header is refused, proving the mock enforces CSRF.
    const raw = await fetch(apiUrl('/api/auth/totp/setup'), { method: 'POST' })
    expect(raw.status).toBe(403)
    expect(await raw.json()).toEqual({
      error: { code: ERROR_CODES.csrfFailed, message: expect.any(String) as string },
    })
  })

  it('maps the contract error shape to a typed ApiError', async () => {
    server.use(
      http.post('*/api/thing', () =>
        HttpResponse.json(
          { error: { code: 'rate_limited', message: 'Slow down. Try again in 15 minutes.' } },
          { status: 429 },
        ),
      ),
    )
    const error: unknown = await api('/thing', { method: 'POST' }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(isApiError(error, ERROR_CODES.rateLimited)).toBe(true)
    expect(error).toMatchObject({ status: 429, message: 'Slow down. Try again in 15 minutes.' })
  })

  it('falls back to synthetic codes when the body is not the contract shape', async () => {
    server.use(
      http.get('*/api/down', () => new HttpResponse('<html>Bad gateway</html>', { status: 502 })),
      http.get('*/api/nosession', () => new HttpResponse(null, { status: 401 })),
      http.get('*/api/offline', () => HttpResponse.error()),
    )
    await expect(api('/down')).rejects.toMatchObject({ status: 502, code: 'server_error' })
    await expect(api('/nosession')).rejects.toMatchObject({ status: 401, code: 'unauthenticated' })
    await expect(api('/offline')).rejects.toMatchObject({ status: 0, code: 'network_error' })
  })

  it('returns undefined for 204 and parses JSON otherwise', async () => {
    seedHousehold({ signedIn: true })
    await expect(api('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('turns errors into short human messages', () => {
    expect(errorMessage(new ApiError(400, 'invalid_code', 'That code did not match.'))).toBe(
      'That code did not match.',
    )
    expect(errorMessage(new ApiError(403, ERROR_CODES.csrfFailed, 'x'))).toMatch(/reload/i)
    expect(errorMessage(notAllowed())).toMatch(/passkey prompt closed/i)
    expect(errorMessage('weird')).toBe('Something went wrong.')
  })

  it('never shows raw WebAuthn or runtime error text', () => {
    const named = (name: string, message: string) => Object.assign(new Error(message), { name })
    expect(errorMessage(named('SecurityError', 'The RP ID "x" is invalid for this domain'))).toBe(
      'Passkeys need HTTPS. Open Holdfast at its https:// address.',
    )
    expect(errorMessage(named('NotSupportedError', 'algorithm -7 unsupported'))).toMatch(
      /can't use passkeys/,
    )
    expect(errorMessage(named('AbortError', 'aborted'))).toMatch(/prompt closed/)
    const odd = errorMessage(new TypeError("Cannot read properties of undefined (reading 'id')"))
    expect(odd).toBe('Something went wrong. Try again.')
  })
})
