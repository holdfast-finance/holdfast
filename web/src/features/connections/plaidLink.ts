/*
 * Plaid Link, loaded from Plaid's own CDN (the CSP allows https://cdn.plaid.com for script and
 * frame, and nothing else). No npm wrapper: one script tag, one typed function, no dependency.
 *
 * The Link token is passed straight into `Plaid.create` and goes out of scope. It is never put
 * in React state, a store or a log. The one exception is a bank's OAuth hand-off, when it sits
 * in sessionStorage until Link is back (see oauthSession.ts). The public token goes from `onSuccess` to the
 * exchange call and nowhere else.
 */

export const PLAID_LINK_SCRIPT = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'

interface PlaidCreateConfig {
  token: string
  onSuccess: (publicToken: string) => void
  onExit: () => void
  onEvent?: (eventName: string) => void
  receivedRedirectUri?: string
}

interface PlaidHandler {
  open: () => void
  destroy: () => void
}

interface PlaidGlobal {
  create: (config: PlaidCreateConfig) => PlaidHandler
}

function plaidGlobal(): PlaidGlobal | undefined {
  return (window as unknown as { Plaid?: PlaidGlobal }).Plaid
}

let loading: Promise<PlaidGlobal> | null = null

/** Resolves with `window.Plaid`; rejects (and may be retried) if the script cannot load. */
export function loadPlaid(): Promise<PlaidGlobal> {
  const ready = plaidGlobal()
  if (ready) return Promise.resolve(ready)
  loading ??= new Promise<PlaidGlobal>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = PLAID_LINK_SCRIPT
    script.async = true
    script.onload = () => {
      const loaded = plaidGlobal()
      if (loaded) resolve(loaded)
      else reject(new Error('plaid_link_missing'))
    }
    script.onerror = () => {
      script.remove()
      reject(new Error('plaid_link_blocked'))
    }
    document.head.append(script)
  }).catch((error: unknown) => {
    loading = null
    throw error
  })
  return loading
}

export interface LinkCallbacks {
  onSuccess: (publicToken: string) => void
  /** Fires on every close without success. It never decides anything: callers resolve. */
  onExit: () => void
  /** Link's event names only (never metadata). OPEN_OAUTH means a bank is taking over the tab. */
  onEvent?: (eventName: string) => void
  /** The full return URL, when resuming after a bank's OAuth page. */
  receivedRedirectUri?: string
}

/** Open Link for a token. Rejects if Plaid's script could not be loaded. */
export async function openPlaidLink(token: string, callbacks: LinkCallbacks): Promise<void> {
  const plaid = await loadPlaid()
  let handler: PlaidHandler | null = null
  handler = plaid.create({
    token,
    onSuccess: (publicToken) => {
      handler?.destroy()
      callbacks.onSuccess(publicToken)
    },
    onExit: () => {
      handler?.destroy()
      callbacks.onExit()
    },
    onEvent: (eventName) => {
      callbacks.onEvent?.(eventName)
    },
    ...(callbacks.receivedRedirectUri
      ? { receivedRedirectUri: callbacks.receivedRedirectUri }
      : {}),
  })
  handler.open()
}
