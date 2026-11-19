import { useQuery } from '@tanstack/react-query'

/*
 * Typed fetch wrapper for the Holdfast API (docs/phases/PHASE-1-contract.md, "API contract").
 *
 * - Same-origin only (`/api/...`): Caddy routes it in Docker, Vite proxies it in `pnpm dev`, MSW
 *   answers it in tests and in `VITE_MOCK_API=1` dev. The app makes no third-party requests.
 * - `credentials: 'same-origin'` so the HttpOnly `hf_session` cookie rides along.
 * - Every non-GET carries `X-Holdfast-CSRF: 1` (the API rejects non-GET without it).
 * - Non-2xx responses become a typed `ApiError` carrying the contract's `{error: {code, message}}`.
 * - 401s are reported to the query client (see `queryClient.ts`), which drops the cached session
 *   so the route guard sends the user to /login.
 *
 * TODO(openapi): types in `./types.ts` are hand-written; generate them with openapi-typescript
 * once the backend publishes /api/openapi.json, and keep amounts as `string`.
 *
 * Never log request bodies here: they carry passwords, TOTP codes and recovery codes.
 */

export const CSRF_HEADER = 'X-Holdfast-CSRF'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Absolute same-origin URL. Node's fetch (tests) rejects relative URLs; browsers accept both. */
export function apiUrl(path: string): string {
  return new URL(path, window.location.origin).toString()
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not JSON (proxy error page, empty body): fall through to a generic error.
  }
  if (isRecord(body) && isRecord(body.error)) {
    const { code, message } = body.error
    if (typeof code === 'string' && typeof message === 'string') {
      return new ApiError(response.status, code, message)
    }
  }
  const fallback =
    response.status === 401
      ? 'unauthenticated'
      : response.status >= 500
        ? 'server_error'
        : 'http_error'
  return new ApiError(
    response.status,
    fallback,
    `The server answered HTTP ${String(response.status)}.`,
  )
}

type Method = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

export interface RequestOptions {
  method?: Method
  body?: unknown
  query?: Record<string, string | number | undefined>
  signal?: AbortSignal
}

/**
 * Call `/api{path}` and return the parsed JSON (or `undefined` for 204).
 * The caller states the response type; validation is the API's contract.
 */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const url = new URL(apiUrl(`/api${path}`))
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') headers[CSRF_HEADER] = '1'
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(url.toString(), {
      method,
      headers,
      credentials: 'same-origin',
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'network_error', 'Holdfast could not reach its server.')
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** A human message for any error a query or mutation can throw. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}

// --- Health (Phase 0) -------------------------------------------------------------------------

export type CheckState = 'ok' | 'error'

export interface HealthResponse {
  status: CheckState
  version: string
  checks: Record<string, CheckState>
}

export type ApiStatus = 'ok' | 'degraded' | 'unreachable'

function isCheckState(value: unknown): value is CheckState {
  return value === 'ok' || value === 'error'
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  if (!isRecord(value)) return false
  const { status, version, checks } = value
  return (
    isCheckState(status) &&
    typeof version === 'string' &&
    isRecord(checks) &&
    Object.values(checks).every(isCheckState)
  )
}

/**
 * GET /api/health. 200 = all checks ok; 503 = a dependency is down (body still describes it).
 * Anything else, or a network failure, means the API is unreachable.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(apiUrl('/api/health'), {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal,
  })
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(
      response.status,
      'http_error',
      `Health check failed with HTTP ${String(response.status)}`,
    )
  }
  const body: unknown = await response.json()
  if (!isHealthResponse(body)) {
    throw new ApiError(response.status, 'bad_body', 'Health check returned an unexpected body')
  }
  return body
}

export const healthQueryKey = ['health'] as const

export function useHealth() {
  return useQuery({
    queryKey: healthQueryKey,
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: 30_000,
    retry: 1,
  })
}

export function useApiStatus(): { status: ApiStatus | 'checking'; version?: string } {
  const { data, isPending, isError } = useHealth()
  if (isPending) return { status: 'checking' }
  if (isError) return { status: 'unreachable' }
  return { status: data.status === 'ok' ? 'ok' : 'degraded', version: data.version }
}
