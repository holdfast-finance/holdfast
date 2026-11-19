import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '@/api/client'
import type {
  MeResponse,
  PasskeyAssertionRequest,
  PasskeyList,
  PasskeyRegisterOptionsRequest,
  PasskeyRegisterVerifyRequest,
  PasskeyRegisterVerifyResponse,
  PasswordLoginRequest,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  SessionResponse,
  SetPasswordRequest,
  SetupRequest,
  SetupStatus,
  TotpEnableResponse,
  TotpReauthRequest,
  TotpSetupResponse,
  UserResponse,
} from '@/api/types'
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
    return await api<MeResponse>('/auth/me', { signal })
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
    queryFn: ({ signal }) => api<SetupStatus>('/setup/status', { signal }),
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
    mutationFn: () => api<undefined>('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      // Whatever the server said, forget everything this tab knew.
      client.clear()
      client.setQueryData(queryKeys.me, null)
    },
  })
}

// --- WebAuthn ceremonies -----------------------------------------------------------------------

/** Options → browser ceremony → verify. Used by setup, invite accept and Security → Add passkey. */
export async function registerPasskey(nickname?: string): Promise<PasskeyRegisterVerifyResponse> {
  const body: PasskeyRegisterOptionsRequest = nickname ? { nickname } : {}
  const optionsJSON = await api<PublicKeyCredentialCreationOptionsJSON>(
    '/auth/passkey/register/options',
    { method: 'POST', body },
  )
  const credential = await startRegistration({ optionsJSON })
  const verify: PasskeyRegisterVerifyRequest = nickname ? { credential, nickname } : { credential }
  return api<PasskeyRegisterVerifyResponse>('/auth/passkey/register/verify', {
    method: 'POST',
    body: verify,
  })
}

async function assertPasskey(prefix: '/auth/passkey/login' | '/auth/reauth/passkey') {
  const optionsJSON = await api<PublicKeyCredentialRequestOptionsJSON>(`${prefix}/options`, {
    method: 'POST',
    body: {},
  })
  const credential = await startAuthentication({ optionsJSON })
  const body: PasskeyAssertionRequest = { credential }
  return { body, verifyPath: `${prefix}/verify` }
}

export function usePasskeyLogin() {
  const adopt = useAdoptSession()
  return useMutation({
    mutationFn: async () => {
      const { body, verifyPath } = await assertPasskey('/auth/passkey/login')
      return api<UserResponse>(verifyPath, { method: 'POST', body })
    },
    onSuccess: adopt,
  })
}

export function usePasswordLogin() {
  const adopt = useAdoptSession()
  return useMutation({
    mutationFn: (body: PasswordLoginRequest) =>
      api<UserResponse>('/auth/password/login', { method: 'POST', body }),
    onSuccess: adopt,
  })
}

export async function reauthWithPasskey(): Promise<void> {
  const { body, verifyPath } = await assertPasskey('/auth/reauth/passkey')
  await api<unknown>(verifyPath, { method: 'POST', body })
}

export async function reauthWithTotp(code: string): Promise<void> {
  const body: TotpReauthRequest = { code }
  await api<undefined>('/auth/reauth/totp', { method: 'POST', body })
}

// --- First run and invites ---------------------------------------------------------------------

export function useSetup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: SetupRequest) => api<SessionResponse>('/setup', { method: 'POST', body }),
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
    mutationFn: (token: string) =>
      api<SessionResponse>('/invites/accept', { method: 'POST', body: { token } }),
    onSuccess: (data) => {
      client.setQueryData<MeResponse>(queryKeys.me, { ...data, reauth_fresh: true })
    },
  })
}

// --- Security settings -------------------------------------------------------------------------

export function usePasskeys() {
  return useQuery({
    queryKey: queryKeys.passkeys,
    queryFn: ({ signal }) => api<PasskeyList>('/auth/passkeys', { signal }),
  })
}

export function useAddPasskey() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (nickname?: string) => registerPasskey(nickname),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.passkeys }),
  })
}

export function useRemovePasskey() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      withReauth(() =>
        api<undefined>(`/auth/passkeys/${encodeURIComponent(id)}`, { method: 'DELETE' }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.passkeys }),
  })
}

export function useTotpSetup() {
  return useMutation({
    mutationFn: () => api<TotpSetupResponse>('/auth/totp/setup', { method: 'POST', body: {} }),
  })
}

export function useTotpEnable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (code: string) =>
      api<TotpEnableResponse>('/auth/totp/enable', { method: 'POST', body: { code } }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

export function useTotpDisable() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () =>
      withReauth(() => api<undefined>('/auth/totp/disable', { method: 'POST', body: {} })),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

export function useSetPassword() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (password: string) => {
      const body: SetPasswordRequest = { password }
      return withReauth(() => api<undefined>('/auth/password', { method: 'POST', body }))
    },
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}
