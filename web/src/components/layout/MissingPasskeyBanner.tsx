import { KeyRound, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation } from 'react-router'

import { usePasskeys } from '@/api/auth'
import { Button } from '@/components/ui/button'

/** Per tab session: dismissing it once keeps it away until the tab closes. */
const DISMISS_KEY = 'holdfast.missingPasskeyBanner.dismissed'
const SECURITY_PATH = '/settings/security'

function readDismissed(): boolean {
  try {
    return window.sessionStorage.getItem(DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

function writeDismissed(): void {
  try {
    window.sessionStorage.setItem(DISMISS_KEY, '1')
  } catch {
    // Storage blocked: the banner still hides for this page view.
  }
}

/**
 * Shown across the app shell when the signed-in user has zero passkeys (e.g. they reloaded
 * during setup before registering one). Without a passkey the next sign-in can strand them.
 */
export function MissingPasskeyBanner() {
  const passkeys = usePasskeys()
  const { pathname } = useLocation()
  const [dismissed, setDismissed] = useState(readDismissed)

  const missing = passkeys.data !== undefined && passkeys.data.items.length === 0
  // Settings → Security already shows the empty passkey list and the Add button.
  if (!missing || dismissed || pathname === SECURITY_PATH) return null

  return (
    <section
      aria-label="Account recovery"
      className="mb-5 flex flex-col gap-3 rounded-card border border-warning/40 bg-warning/12 p-4 sm:flex-row sm:items-center"
    >
      <div className="flex flex-1 items-start gap-3">
        <KeyRound aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-text" />
        <p className="text-sm text-foreground">Add a passkey so you can sign in again.</p>
      </div>
      <div className="flex items-center gap-2">
        <Button asChild size="lg" className="flex-1 sm:flex-none">
          <Link to={SECURITY_PATH}>Add a passkey</Link>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Dismiss for this session"
          onClick={() => {
            writeDismissed()
            setDismissed(true)
          }}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
    </section>
  )
}
