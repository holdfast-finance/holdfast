import { AlertTriangle } from 'lucide-react'

import { PASSKEYS_NEED_HTTPS } from '@/api/client'

/**
 * Explains why the passkey button is disabled when WebAuthn isn't available (plain http on a
 * LAN or Tailscale IP, or a browser without passkeys). Render it only when
 * `passkeysAvailable()` is false.
 */
export function PasskeyNotice() {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/12 px-3 py-2.5 text-sm text-foreground"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-text" />
      <span>{PASSKEYS_NEED_HTTPS}</span>
    </div>
  )
}
