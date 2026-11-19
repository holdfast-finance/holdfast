import { useMutation } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { registerPasskey, useAcceptInvite } from '@/api/auth'
import { passkeysAvailable } from '@/api/client'
import { FormError } from '@/components/forms/Field'
import { AuthLayout } from '@/components/layout/AuthLayout'
import { PasskeyNotice } from '@/components/PasskeyNotice'
import { Button } from '@/components/ui/button'

/**
 * /invite#<token> (older /invite/:token links are redirected here by the router). Accepting creates this person's user and a
 * session; the passkey comes right after so they never end up with an account they can't get
 * back into.
 *
 * The token rides in the URL fragment, which browsers never send to a server, so it stays out
 * of proxy logs and Referer headers. It's read once, then stripped from the address bar.
 */
export function InvitePage() {
  const location = useLocation()
  const [token] = useState(() => location.hash.slice(1))
  const navigate = useNavigate()
  const accept = useAcceptInvite()
  const passkey = useMutation({
    mutationFn: () => registerPasskey(),
    onSuccess: () => {
      void navigate('/', { replace: true })
    },
  })

  useEffect(() => {
    if (!window.location.hash) return
    // Outside the router on purpose: a router navigation would re-render the route, and the
    // token is already in state. Keeps the router's history entry (window.history.state).
    const { pathname, search } = window.location
    window.history.replaceState(window.history.state, '', pathname + search)
  }, [])

  if (accept.isSuccess) {
    const name = accept.data.user.person?.display_name
    const available = passkeysAvailable()
    return (
      <AuthLayout
        title={name ? `Welcome, ${name}` : 'Welcome in'}
        subtitle={`You're in ${accept.data.household.name}. Add a passkey to finish.`}
        focusKey="accepted"
      >
        <div className="flex flex-col gap-4">
          {!available && <PasskeyNotice />}
          <FormError error={passkey.error} />
          <Button
            size="lg"
            disabled={!available || passkey.isPending}
            onClick={() => {
              passkey.mutate()
            }}
          >
            <KeyRound aria-hidden="true" />
            {passkey.isPending ? 'Waiting for your device…' : 'Create passkey'}
          </Button>
        </div>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout
      title="You're invited"
      subtitle="You've been invited to a shared Holdfast. Same books, your own sign-in."
      focusKey="invite"
    >
      <div className="flex flex-col gap-4">
        <FormError error={accept.error} />
        <Button
          size="lg"
          disabled={accept.isPending || !token}
          onClick={() => {
            accept.mutate(token)
          }}
        >
          {accept.isPending ? 'Accepting…' : 'Accept invite'}
        </Button>
        <p className="text-xs text-muted-foreground">
          The link works once and expires after 24 hours. Already in?{' '}
          <Link
            to="/login"
            className="inline-flex min-h-11 items-center text-primary-text underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </p>
      </div>
    </AuthLayout>
  )
}
