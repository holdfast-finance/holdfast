import { zodResolver } from '@hookform/resolvers/zod'
import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { Navigate } from 'react-router'
import { z } from 'zod'

import { usePasskeyLogin, usePasswordLogin, useSetupStatus } from '@/api/auth'
import { passkeysAvailable } from '@/api/client'
import type { PasswordLoginRequest } from '@/api/types'
import { RedirectIfSignedIn } from '@/app/guards'
import { Field, FormError } from '@/components/forms/Field'
import { AuthLayout } from '@/components/layout/AuthLayout'
import { PasskeyNotice } from '@/components/PasskeyNotice'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const passwordSchema = z
  .object({
    email: z.email('Enter the email on your account.'),
    password: z.string().min(1, 'Enter your password.'),
    method: z.enum(['totp', 'recovery']),
    code: z.string().trim(),
  })
  .superRefine((v, ctx) => {
    if (v.method === 'totp' && !/^\d{6}$/.test(v.code)) {
      ctx.addIssue({
        code: 'custom',
        path: ['code'],
        message: 'Enter the 6-digit code from your authenticator.',
      })
    }
    if (v.method === 'recovery' && v.code.length < 6) {
      ctx.addIssue({ code: 'custom', path: ['code'], message: 'Enter one of your recovery codes.' })
    }
  })
type PasswordForm = z.infer<typeof passwordSchema>

function PasswordLoginForm() {
  const login = usePasswordLogin()
  const form = useForm<PasswordForm>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { email: '', password: '', method: 'totp', code: '' },
  })
  const method = useWatch({ control: form.control, name: 'method' })
  const { errors } = form.formState

  const onSubmit = (v: PasswordForm) => {
    const body: PasswordLoginRequest =
      v.method === 'totp'
        ? { email: v.email, password: v.password, totp_code: v.code }
        : { email: v.email, password: v.password, recovery_code: v.code }
    login.mutate(body)
  }

  return (
    <form
      noValidate
      aria-label="Password and code sign-in"
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit(onSubmit)(e)
      }}
    >
      <FormError error={login.error} />
      <Field label="Email" error={errors.email?.message}>
        {(p) => <Input {...p} {...form.register('email')} type="email" autoComplete="username" />}
      </Field>
      <Field label="Password" error={errors.password?.message}>
        {(p) => (
          <Input
            {...p}
            {...form.register('password')}
            type="password"
            autoComplete="current-password"
          />
        )}
      </Field>
      <Field
        label={method === 'totp' ? 'Authenticator code' : 'Recovery code'}
        error={errors.code?.message}
        hint={method === 'recovery' ? 'Each recovery code works once.' : undefined}
      >
        {(p) => (
          <Input
            {...p}
            {...form.register('code')}
            inputMode={method === 'totp' ? 'numeric' : 'text'}
            autoComplete="one-time-code"
            className="font-mono tabular-nums"
          />
        )}
      </Field>
      <Button
        type="button"
        variant="link"
        className="min-h-11 self-start px-0"
        onClick={() => {
          form.setValue('method', method === 'totp' ? 'recovery' : 'totp')
          form.setValue('code', '')
          form.clearErrors('code')
        }}
      >
        {method === 'totp'
          ? 'Lost your authenticator? Use a recovery code'
          : 'Use an authenticator code'}
      </Button>
      <Button type="submit" size="lg" disabled={login.isPending}>
        {login.isPending ? 'Signing in…' : 'Sign in'}
      </Button>
    </form>
  )
}

function LoginScreen() {
  const passkey = usePasskeyLogin()
  const setup = useSetupStatus()
  const [showFallback, setShowFallback] = useState(false)
  const available = passkeysAvailable()

  if (setup.data?.needs_setup) return <Navigate to="/setup" replace />

  return (
    <AuthLayout title="Sign in" subtitle="Your money. Your keys. Unlock with your passkey.">
      <div className="flex flex-col gap-4">
        {!available && <PasskeyNotice />}
        <FormError error={passkey.error} />
        <Button
          type="button"
          size="lg"
          className="h-14 text-base md:h-14"
          disabled={!available || passkey.isPending}
          onClick={() => {
            passkey.mutate()
          }}
        >
          <KeyRound aria-hidden="true" className="size-5" />
          {passkey.isPending ? 'Waiting for your passkey…' : 'Sign in with passkey'}
        </Button>
        {/* The toggle stays mounted so focus never drops to <body> when the form opens. */}
        <Button
          type="button"
          variant="link"
          className="min-h-11"
          aria-expanded={showFallback}
          aria-controls="password-fallback"
          onClick={() => {
            setShowFallback((v) => !v)
          }}
        >
          {showFallback ? 'Hide password + code' : 'Use password + code instead'}
        </Button>
        {showFallback && (
          <section
            id="password-fallback"
            aria-labelledby="fallback-title"
            className="flex flex-col gap-4 border-t border-border pt-6"
          >
            <h2 id="fallback-title" className="text-lg">
              Password + code
            </h2>
            <PasswordLoginForm />
          </section>
        )}
      </div>
    </AuthLayout>
  )
}

export function LoginPage() {
  return (
    <RedirectIfSignedIn>
      <LoginScreen />
    </RedirectIfSignedIn>
  )
}
