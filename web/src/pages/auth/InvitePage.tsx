import { useMutation } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'

import { registerPasskey, useAcceptInvite } from '@/api/auth'
import { FormError } from '@/components/forms/Field'
import { AuthLayout } from '@/components/layout/AuthLayout'
import { Button } from '@/components/ui/button'

/**
 * /invite/:token. Accepting creates this person's user and a session; the passkey comes right
 * after so they never end up with an account they can't get back into.
 */
export function InvitePage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const accept = useAcceptInvite()
  const passkey = useMutation({
    mutationFn: () => registerPasskey(),
    onSuccess: () => {
      void navigate('/', { replace: true })
    },
  })

  if (accept.isSuccess) {
    const name = accept.data.user.person?.display_name
    return (
      <AuthLayout
        title={name ? `Welcome, ${name}` : 'Welcome in'}
        subtitle={`You're part of ${accept.data.household.name}. Create your passkey to lock it in.`}
      >
        <div className="flex flex-col gap-4">
          <FormError error={passkey.error} />
          <Button
            size="lg"
            disabled={passkey.isPending}
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
      subtitle="Someone you share finances with set up a Holdfast server and wants you in. Their server, shared books, your own sign-in."
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
          <Link to="/login" className="text-primary-text underline-offset-4 hover:underline">
            Sign in
          </Link>
          .
        </p>
      </div>
    </AuthLayout>
  )
}
