import { useQuery } from '@tanstack/react-query'

/*
 * Phase 0 API client: only /api/health.
 *
 * TODO(phase 1): generate types from the backend's OpenAPI document instead of writing them
 * by hand (CLAUDE.md "Style"; PLAN 11.1):
 *   pnpm dlx openapi-typescript http://localhost:8000/api/openapi.json -o src/api/schema.d.ts
 * then wrap `openapi-fetch` (createClient<paths>({ baseUrl: '/' })) in TanStack Query hooks here.
 * Amount fields stay `string` in the generated types; never coerce them to number.
 *
 * All requests are same-origin (`/api/...`): Caddy routes them in Docker, Vite proxies them in
 * `pnpm dev`. The app makes no third-party requests.
 */

export type CheckState = 'ok' | 'error'

export interface HealthResponse {
  status: CheckState
  version: string
  checks: Record<string, CheckState>
}

export type ApiStatus = 'ok' | 'degraded' | 'unreachable'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

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
  const response = await fetch('/api/health', {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal,
  })
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(response.status, `Health check failed with HTTP ${String(response.status)}`)
  }
  const body: unknown = await response.json()
  if (!isHealthResponse(body)) {
    throw new ApiError(response.status, 'Health check returned an unexpected body')
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
