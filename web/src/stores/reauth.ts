import { create } from 'zustand'

import { isApiError } from '@/api/client'
import { ERROR_CODES } from '@/api/types'

/*
 * Step-up (re-auth) prompt state. Sensitive API calls answer 403 `reauth_required` when the
 * session's reauth_at is older than 5 minutes. `withReauth` catches that, opens the prompt
 * (<ReauthDialog>, mounted once in the authenticated layout), waits for the user to confirm with
 * a passkey or TOTP code, then retries the call exactly once.
 */

interface Pending {
  resolve: () => void
  reject: (error: Error) => void
}

interface ReauthState {
  pending: Pending | null
  /** Open the prompt; resolves when the step-up succeeds, rejects if the user cancels. */
  request: () => Promise<void>
  confirm: () => void
  cancel: () => void
}

export class ReauthCancelledError extends Error {
  constructor() {
    super('Confirmation cancelled.')
    this.name = 'ReauthCancelledError'
  }
}

export const useReauthStore = create<ReauthState>((set, get) => ({
  pending: null,
  request: () =>
    new Promise<void>((resolve, reject) => {
      // A second request while one is open joins the first prompt.
      const current = get().pending
      if (current) {
        const prev = current
        set({
          pending: {
            resolve: () => {
              prev.resolve()
              resolve()
            },
            reject: (error) => {
              prev.reject(error)
              reject(error)
            },
          },
        })
        return
      }
      set({ pending: { resolve, reject } })
    }),
  confirm: () => {
    const { pending } = get()
    set({ pending: null })
    pending?.resolve()
  },
  cancel: () => {
    const { pending } = get()
    set({ pending: null })
    pending?.reject(new ReauthCancelledError())
  },
}))

/** Run `fn`; if the API wants a fresh step-up, prompt for it and retry once. */
export async function withReauth<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (error) {
    if (!isApiError(error, ERROR_CODES.reauthRequired)) throw error
    await useReauthStore.getState().request()
    return fn()
  }
}
