/*
 * OAuth banks send the browser away from Holdfast and back to the redirect URI, where Link must
 * be re-opened with the same token. That is the one case where the Link token has to outlive the
 * page, so it goes in sessionStorage (this tab only, gone when the tab closes), under one key,
 * and only from the moment Link reports it is handing off to a bank (OPEN_OAUTH). It is removed
 * on exit, on success, on abandon and when the return page has used it. Never localStorage.
 */

export const OAUTH_STORAGE_KEY = 'holdfast.plaid.oauth'
/** Where Plaid sends the browser back to; the suggested redirect URI in the setup check. */
export const OAUTH_RETURN_PATH = '/plaid/oauth-return'

export interface OAuthSession {
  token: string
  reservationId: string
}

export function saveOAuthSession(session: OAuthSession): void {
  try {
    window.sessionStorage.setItem(OAUTH_STORAGE_KEY, JSON.stringify(session))
  } catch {
    // Storage blocked: the return page will say to start again.
  }
}

export function clearOAuthSession(): void {
  try {
    window.sessionStorage.removeItem(OAUTH_STORAGE_KEY)
  } catch {
    // Nothing to clear.
  }
}

export function readOAuthSession(): OAuthSession | null {
  try {
    const raw = window.sessionStorage.getItem(OAUTH_STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const { token, reservationId } = parsed as Record<string, unknown>
    if (typeof token !== 'string' || typeof reservationId !== 'string') return null
    return { token, reservationId }
  } catch {
    return null
  }
}
