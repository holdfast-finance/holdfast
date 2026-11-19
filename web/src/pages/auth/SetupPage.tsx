import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useNavigate } from 'react-router'
import { z } from 'zod'

import { registerPasskey, useSetup, useSetupStatus } from '@/api/auth'
import { isApiError } from '@/api/client'
import { createPerson } from '@/api/household'
import { ERROR_CODES, FILING_ROLES, type FilingRole } from '@/api/types'
import { Field, FormError } from '@/components/forms/Field'
import { AuthLayout, Splash } from '@/components/layout/AuthLayout'
import { Button } from '@/components/ui/button'
import { Input, NativeSelect } from '@/components/ui/input'
import { FILING_ROLE_LABELS } from '@/pages/settings/labels'

type Step = 'token' | 'about' | 'passkey' | 'person'
const STEPS: Step[] = ['token', 'about', 'passkey', 'person']

const tokenSchema = z.object({
  setup_token: z.string().trim().min(1, 'Paste the setup token from the server log.'),
})
const aboutSchema = z.object({
  household_name: z.string().trim().min(1, 'Name your household.').max(80),
  display_name: z.string().trim().min(1, 'What should Holdfast call you?').max(80),
  email: z.email('Enter an email you can sign in with.'),
})
const personSchema = z.object({
  display_name: z.string().trim().min(1, 'Give them a name.').max(80),
  filing_role: z.enum(['primary', 'spouse', 'dependent']),
})
type TokenForm = z.infer<typeof tokenSchema>
type AboutForm = z.infer<typeof aboutSchema>
type PersonForm = z.infer<typeof personSchema>

function StepHeader({ step }: { step: Step }) {
  const index = STEPS.indexOf(step) + 1
  return (
    <p className="font-mono text-xs text-muted-foreground tabular-nums">
      Step {index} of {STEPS.length}
    </p>
  )
}

function TokenStep({
  initial,
  error,
  onNext,
}: {
  initial: string
  error: unknown
  onNext: (token: string) => void
}) {
  const form = useForm<TokenForm>({
    resolver: zodResolver(tokenSchema),
    defaultValues: { setup_token: initial },
  })
  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit((v) => {
          onNext(v.setup_token)
        })(e)
      }}
    >
      <FormError error={error} />
      <Field
        label="Setup token"
        error={form.formState.errors.setup_token?.message}
        hint="Printed once in the API log on startup (docker compose logs api). It proves you run this box."
      >
        {(p) => (
          <Input
            {...p}
            {...form.register('setup_token')}
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
          />
        )}
      </Field>
      <Button type="submit" size="lg">
        Continue
      </Button>
    </form>
  )
}

function AboutStep({
  token,
  onBack,
  onDone,
  onBadToken,
}: {
  token: string
  onBack: () => void
  onDone: () => void
  onBadToken: (error: unknown) => void
}) {
  const setup = useSetup()
  const form = useForm<AboutForm>({
    resolver: zodResolver(aboutSchema),
    defaultValues: { household_name: '', display_name: '', email: '' },
  })
  const { errors } = form.formState
  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit((v) => {
          setup.mutate(
            { setup_token: token, ...v },
            {
              onSuccess: onDone,
              onError: (error) => {
                if (isApiError(error, ERROR_CODES.invalidSetupToken)) onBadToken(error)
              },
            },
          )
        })(e)
      }}
    >
      <FormError error={setup.error} />
      <Field label="Household name" error={errors.household_name?.message} hint="Shown only to the people in it.">
        {(p) => <Input {...p} {...form.register('household_name')} autoComplete="off" />}
      </Field>
      <Field label="Your name" error={errors.display_name?.message}>
        {(p) => <Input {...p} {...form.register('display_name')} autoComplete="name" />}
      </Field>
      <Field label="Your email" error={errors.email?.message} hint="Your sign-in name. Holdfast sends no email.">
        {(p) => <Input {...p} {...form.register('email')} type="email" autoComplete="email" />}
      </Field>
      <div className="flex flex-col-reverse gap-3 sm:flex-row">
        <Button type="button" variant="outline" size="lg" onClick={onBack}>
          Back
        </Button>
        <Button type="submit" size="lg" className="flex-1" disabled={setup.isPending}>
          {setup.isPending ? 'Creating…' : 'Create household'}
        </Button>
      </div>
    </form>
  )
}

function PasskeyStep({ onDone }: { onDone: () => void }) {
  const [nickname, setNickname] = useState('')
  const register = useMutation({
    mutationFn: () => registerPasskey(nickname.trim() || undefined),
    onSuccess: onDone,
  })
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Passkeys live on your device and can&apos;t be phished or reused. This becomes your key to
        the place.
      </p>
      <FormError error={register.error} />
      <Field label="Device name (optional)" hint="So you can tell your passkeys apart later.">
        {(p) => (
          <Input
            {...p}
            value={nickname}
            onChange={(e) => {
              setNickname(e.target.value)
            }}
            placeholder="Laptop"
            autoComplete="off"
          />
        )}
      </Field>
      <Button
        type="button"
        size="lg"
        disabled={register.isPending}
        onClick={() => {
          register.mutate()
        }}
      >
        <KeyRound aria-hidden="true" />
        {register.isPending ? 'Waiting for your device…' : 'Create passkey'}
      </Button>
    </div>
  )
}

function PersonStep({ onDone }: { onDone: () => void }) {
  const create = useMutation({ mutationFn: createPerson, onSuccess: onDone })
  const form = useForm<PersonForm>({
    resolver: zodResolver(personSchema),
    defaultValues: { display_name: '', filing_role: 'spouse' },
  })
  const { errors } = form.formState
  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit((v) => {
          create.mutate(v)
        })(e)
      }}
    >
      <p className="text-sm text-muted-foreground">
        Sharing the books with someone? Add them now and send an invite from Settings. You can
        also do this later.
      </p>
      <FormError error={create.error} />
      <Field label="Their name" error={errors.display_name?.message}>
        {(p) => <Input {...p} {...form.register('display_name')} autoComplete="off" />}
      </Field>
      <Field label="Role" error={errors.filing_role?.message}>
        {(p) => (
          <NativeSelect {...p} {...form.register('filing_role')}>
            {FILING_ROLES.filter((r): r is Exclude<FilingRole, 'primary'> => r !== 'primary').map((r) => (
              <option key={r} value={r}>
                {FILING_ROLE_LABELS[r]}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <div className="flex flex-col-reverse gap-3 sm:flex-row">
        <Button type="button" variant="outline" size="lg" onClick={onDone}>
          Skip for now
        </Button>
        <Button type="submit" size="lg" className="flex-1" disabled={create.isPending}>
          Add person
        </Button>
      </div>
    </form>
  )
}

const TITLES: Record<Step, { title: string; subtitle: string }> = {
  token: { title: 'Claim this server', subtitle: 'First run. Nobody owns this instance yet. Make it yours.' },
  about: { title: 'Your household', subtitle: 'One root for every account you hold.' },
  passkey: { title: 'Create your passkey', subtitle: 'No password to leak. Your device holds the key.' },
  person: { title: 'Add a second person', subtitle: 'Optional. Each person gets their own sign-in.' },
}

export function SetupPage() {
  const status = useSetupStatus()
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('token')
  const [token, setToken] = useState('')
  const [tokenError, setTokenError] = useState<unknown>(null)

  if (step === 'token') {
    if (status.isPending) return <Splash />
    // Setup already done: nothing to claim here.
    if (status.data && !status.data.needs_setup) return <Navigate to="/login" replace />
  }

  const finish = () => {
    void navigate('/', { replace: true })
  }

  return (
    <AuthLayout title={TITLES[step].title} subtitle={TITLES[step].subtitle}>
      <StepHeader step={step} />
      {step === 'token' && (
        <TokenStep
          initial={token}
          error={tokenError}
          onNext={(t) => {
            setToken(t)
            setTokenError(null)
            setStep('about')
          }}
        />
      )}
      {step === 'about' && (
        <AboutStep
          token={token}
          onBack={() => {
            setStep('token')
          }}
          onDone={() => {
            setStep('passkey')
          }}
          onBadToken={(error) => {
            setTokenError(error)
            setStep('token')
          }}
        />
      )}
      {step === 'passkey' && (
        <PasskeyStep
          onDone={() => {
            setStep('person')
          }}
        />
      )}
      {step === 'person' && <PersonStep onDone={finish} />}
    </AuthLayout>
  )
}
