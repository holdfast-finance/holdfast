import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError, readData } from '@/api/client'
import type { MeResponse, PasswordLoginRequest } from '@/api/contracts'
import type { components } from '@/api/schema'
import { queryKeys } from '@/app/queryClient'
import { withReauth } from '@/stores/reauth'

/*
 * Auth hooks over the Phase 1 API contract. WebAuthn ceremonies run in the browser through
 * @simplewebauthn/browser; the server only ever sees the public credential JSON.
 */

// --- Session -----------------------------------------------------------------------------------

/** GET /auth/me. Resolves to `null` (not an error) when there is no session. */
export async function fetchMe(signal?: AbortSignal): Promise<MeResponse | null> {
  try {
    return await readData(api.GET('/api/auth/me', { signal }))
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => fetchMe(signal),
    staleTime: 60_000,
  })
}

export function useSetupStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.setupStatus,
    queryFn: ({ signal }) => readData(api.GET('/api/setup/status', { signal })),
    enabled,
    staleTime: 60_000,
  })
}

/** Store a fresh session's identity, then refetch /auth/me for the full shape (reauth_fresh). */
function useAdoptSession() {
  const client = useQueryClient()
  return async () => {
    client.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' || q.queryKey[1] !== 'me' })
    await client.invalidateQueries({ queryKey: queryKeys.me })
    await client.invalidateQueries({ queryKey: queryKeys.setupStatus })
  }
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/auth/logout')
    },
    onSettled: () => {
      // Whatever the server said, forget everything this tab knew.
      client.clear()
      client.setQueryData(queryKeys.me, null)
    },
  })
}

// --- WebAuthn ceremonies -----------------------------------------------------------------------

/** Options → browser ceremony → verify. Used by setup, invite accept and Security → Add passkey. */
export async function registerPasskey(nickname?: string) {
  const optionsJSON = await readData(
    api.POST('/api/auth/passkey/register/options', { body: nickname ? { nickname } : {} }),
  )
  // The OpenAPI response is an opaque dictionary from the backend's WebAuthn library.
  const credential = await startRegistration({
    optionsJSON: optionsJSON as unknown as PublicKeyCredentialCreationOptionsJSON,
  })
  return readData(
    api.POST('/api/auth/passkey/register/verify', {
      body: { credential: { ...credential }, ...(nickname ? { nickname } : {}) },
    }),
  )
}

async function assertPasskey(
  optionsPath: '/api/auth/passkey/login/options' | '/api/auth/reauth/passkey/options',
) {
  const optionsJSON = await readData(api.POST(optionsPath))
  const credential = await startAuthentication({
    optionsJSON: optionsJSON as unknown as PublicKeyCredentialRequestOptionsJSON,
  })
  return { credential: { ...credential } }
}

export function usePasskeyLogin() {
  const adopt = useAdoptSession()
  return useMutation({
    mutationFn: async () => {
      const body = await assertPasskey('/api/auth/passkey/login/options')
      return readData(api.POST('/api/auth/passkey/login/verify', { body }))
    },
    onSuccess: adopt,
  })
}

export function usePasswordLogin() {
  const adopt = useAdoptSession()
  return useMutation({
    mutationFn: (body: PasswordLoginRequest) =>
      readData(api.POST('/api/auth/password/login', { body })),
    onSuccess: adopt,
  })
}

export async function reauthWithPasskey(): Promise<void> {
  const body = await assertPasskey('/api/auth/reauth/passkey/options')
  await api.POST('/api/auth/reauth/passkey/verify', { body })
}

export async function reauthWithTotp(code: string): Promise<void> {
  await api.POST('/api/auth/reauth/totp', { body: { code } })
}

// --- First run and invites ---------------------------------------------------------------------

export function useSetup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: components['schemas']['SetupIn']) =>
      readData(api.POST('/api/setup', { body })),
    onSuccess: (data) => {
      // The wizard continues on /setup; don't let the guard bounce it before the passkey step.
      client.setQueryData(queryKeys.setupStatus, { needs_setup: false })
      client.setQueryData<MeResponse>(queryKeys.me, { ...data, reauth_fresh: true })
    },
  })
}

export function useAcceptInvite() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => readData(api.POST('/api/invites/accept', { body: { token } })),
    onSuccess: (data) => {
      client.setQueryData<MeResponse>(queryKeys.me, { ...data, reauth_fresh: true })
    },
  })
}

// --- Security settings -------------------------------------------------------------------------

export function usePasskeys(enabled = true) {
  return useQuery({
    queryKey: queryKeys.passkeys,
    queryFn: ({ signal }) => readData(api.GET('/api/auth/passkeys', { signal })),
    enabled,
  })
}

export function useAddPasskey() {
  const client = useQueryClient()
  return useMutation({
    // Step-up once the account already has a way in; the API exempts the very first credential,
    // so setup, invite accept and the missing-passkey banner never see the prompt.
    mutationFn: (nickname?: string) => withReauth(() => registerPasskey(nickname)),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.passkeys }),
  })
}

export function useRemovePasskey() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      withReauth(async () => {
        await api.DELETE('/api/auth/passkeys/{passkey_id}', {
          params: { path: { passkey_id: id } },
        })
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.passkeys }),
  })
}

export function useTotpSetup() {
  return useMutation({
    mutationFn: () => withReauth(() => readData(api.POST('/api/auth/totp/setup'))),
  })
}

export function useTotpEnable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (code: string) =>
      withReauth(() => readData(api.POST('/api/auth/totp/enable', { body: { code } }))),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

export function useTotpDisable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () =>
      withReauth(async () => {
        await api.POST('/api/auth/totp/disable')
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

export function useSetPassword() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (password: string) =>
      withReauth(async () => {
        await api.POST('/api/auth/password', { body: { password } })
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}
