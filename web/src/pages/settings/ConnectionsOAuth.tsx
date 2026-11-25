import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { useExchange, useResolveReservation } from '@/api/connections'
import { Button } from '@/components/ui/button'
import { linkErrorMessage } from '@/features/connections/copy'
import {
  clearOAuthSession,
  readOAuthSession,
  saveOAuthSession,
  type OAuthSession,
} from '@/features/connections/oauthSession'
import { openPlaidLink } from '@/features/connections/plaidLink'
import { useReauthStore } from '@/stores/reauth'
import { SettingsSection } from '@/pages/settings/SettingsLayout'

const BACK = '/settings/connections'

/** A route change should not strand focus on a page that is gone: land it on the new h1. */
function focusPageTitle(tries = 30): void {
  requestAnimationFrame(() => {
    const title = document.getElementById('page-title')
    if (title) title.focus()
    else if (tries > 0) focusPageTitle(tries - 1)
  })
}

/**
 * Where a bank's OAuth page sends the browser back (the redirect URI registered with Plaid).
 * It changes nothing on load: it re-opens Link with the saved token and the full return URL, and
 * only a finished Link triggers the exchange. The query string (it carries `oauth_state_id`) is
 * read once, then removed from the address bar.
 */
export function ConnectionsOAuth() {
  const navigate = useNavigate()
  const location = useLocation()
  const [session] = useState<OAuthSession | null>(readOAuthSession)
  const [problem, setProblem] = useState<unknown>(null)
  const started = useRef(false)
  const returnedTo = useRef(window.location.href)
  const handler = useRef<{ destroy: () => void } | null>(null)
  // Bumped on unmount: a Link that finishes loading for an older run is destroyed, not used.
  const generation = useRef(0)
  const aborted = useRef(false)
  const reauthPending = useReauthStore((s) => s.pending !== null)
  const exchange = useExchange()
  const resolve = useResolveReservation()

  useEffect(() => {
    aborted.current = false
    const run = generation.current
    return () => {
      aborted.current = true
      generation.current = run + 1
      started.current = false
      handler.current?.destroy()
      handler.current = null
    }
  }, [])

  useEffect(() => {
    if (!session || started.current) return
    started.current = true
    const run = generation.current
    void navigate(location.pathname, { replace: true })
    const finish = () => {
      void navigate(BACK, { replace: true })
      focusPageTitle()
    }
    void openPlaidLink(session.token, {
      receivedRedirectUri: returnedTo.current,
      // A second bank hop re-opens the OAuth page: keep the token for it.
      onEvent: (eventName) => {
        if (eventName === 'OPEN_OAUTH' && !aborted.current) {
          saveOAuthSession({ token: session.token, reservationId: session.reservationId })
        }
      },
      onSuccess: (publicToken) => {
        clearOAuthSession()
        exchange.mutate(
          { publicToken, reservationId: session.reservationId },
          {
            onSuccess: finish,
            onSettled: () => {
              exchange.reset()
            },
            // The public token is spent. Plaid still knows what happened, so ask it.
            onError: () => {
              resolve.mutate(session.reservationId, { onSettled: finish })
            },
          },
        )
      },
      // Closing Link proves nothing: resolve, never abandon.
      onExit: () => {
        clearOAuthSession()
        resolve.mutate(session.reservationId, { onSettled: finish })
      },
    })
      .then((opened) => {
        if (generation.current !== run) opened.destroy()
        else handler.current = opened
      })
      .catch((error: unknown) => {
        if (generation.current !== run) return
        clearOAuthSession()
        setProblem(error)
      })
  }, [session, navigate, location.pathname, exchange, resolve])

  if (!session) {
    return (
      <SettingsSection
        id="oauth-return"
        title="This link has run out"
        description="Holdfast no longer has the bank link you were in the middle of."
      >
        <p className="text-sm text-muted-foreground">
          That happens if the tab was closed, or you opened this address by hand. Nothing was
          linked. Go back and start the link again; if a slot is still held, it shows up under
          Unfinished links.
        </p>
        <Button asChild className="w-fit">
          <Link to={BACK}>Back to Connections</Link>
        </Button>
      </SettingsSection>
    )
  }

  return (
    <SettingsSection
      id="oauth-return"
      title="Finishing your bank link"
      description="Picking up where your bank left off."
    >
      {problem === null ? (
        <p role="status" className="text-sm text-muted-foreground">
          {reauthPending ? "Confirming it's you…" : 'Opening Plaid Link again…'}
        </p>
      ) : (
        <>
          <p role="alert" className="text-sm text-destructive-text">
            {linkErrorMessage(problem)} Plaid Link didn&apos;t open, so nothing was linked.
          </p>
          <Button asChild className="w-fit">
            <Link to={BACK}>Back to Connections</Link>
          </Button>
        </>
      )}
    </SettingsSection>
  )
}
