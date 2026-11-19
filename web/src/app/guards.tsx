import { Suspense, useState, type ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'

import { useMe, useSetupStatus } from '@/api/auth'
import { errorMessage } from '@/api/client'
import { AuthLayout, Splash } from '@/components/layout/AuthLayout'
import { ReauthDialog } from '@/app/lazyRoutes'
import { Button } from '@/components/ui/button'
import { useReauthStore } from '@/stores/reauth'

export interface FromState {
  from?: string
}

/**
 * Everything behind a session. Auth state is `GET /api/auth/me` (TanStack Query):
 * no session → /setup while the instance has no household, otherwise /login (remembering
 * where the user was headed).
 */
export function RequireAuth() {
  const me = useMe()
  const location = useLocation()
  const signedOut = me.data === null
  const setup = useSetupStatus(signedOut)

  if (me.isPending) return <Splash />
  if (me.isError) {
    return (
      <AuthLayout title="Can't reach your server" subtitle={errorMessage(me.error)}>
        <Button
          size="lg"
          onClick={() => {
            void me.refetch()
          }}
        >
          Try again
        </Button>
      </AuthLayout>
    )
  }
  if (signedOut) {
    if (setup.isPending) return <Splash />
    if (setup.data?.needs_setup) return <Navigate to="/setup" replace />
    const state: FromState = { from: location.pathname + location.search }
    return <Navigate to="/login" replace state={state} />
  }
  return (
    <>
      <Outlet />
      <ReauthHost />
    </>
  )
}

/** Wrapper for /login: an existing session goes straight back into the app. */
export function RedirectIfSignedIn({ children }: { children: ReactNode }) {
  const me = useMe()
  const location = useLocation()
  if (me.isPending) return <Splash />
  if (me.data) {
    const from = (location.state as FromState | null)?.from
    return (
      <Navigate to={from && from.startsWith('/') && !from.startsWith('//') ? from : '/'} replace />
    )
  }
  return children
}

/**
 * Mounts the step-up prompt the first time `withReauth` asks for it, then keeps it mounted so
 * it can animate closed and reopen instantly.
 */
function ReauthHost() {
  const requested = useReauthStore((s) => s.pending !== null)
  const [mounted, setMounted] = useState(false)
  if (requested && !mounted) setMounted(true)
  if (!mounted) return null
  return (
    <Suspense fallback={null}>
      <ReauthDialog />
    </Suspense>
  )
}
