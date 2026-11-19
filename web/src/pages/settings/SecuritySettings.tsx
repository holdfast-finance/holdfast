import { zodResolver } from '@hookform/resolvers/zod'
import { Download, KeyRound, ShieldCheck, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import {
  useAddPasskey,
  useMe,
  usePasskeys,
  useRemovePasskey,
  useSetPassword,
  useTotpDisable,
  useTotpEnable,
  useTotpSetup,
} from '@/api/auth'
import { useAudit } from '@/api/household'
import type { Passkey } from '@/api/types'
import { CopyButton } from '@/components/CopyButton'
import { Field, FormError } from '@/components/forms/Field'
import { QrCode } from '@/components/QrCode'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ReauthCancelledError } from '@/stores/reauth'
import { formatDate } from '@/pages/settings/labels'
import { SettingsSection } from '@/pages/settings/SettingsLayout'

/** Hide the "you cancelled the step-up" non-error. */
function visibleError(error: unknown): unknown {
  return error instanceof ReauthCancelledError ? null : error
}

// --- Passkeys ------------------------------------------------------------------------------------

function PasskeysSection({ totpEnabled }: { totpEnabled: boolean }) {
  const passkeys = usePasskeys()
  const add = useAddPasskey()
  const remove = useRemovePasskey()
  const [adding, setAdding] = useState(false)
  const [nickname, setNickname] = useState('')
  const items: Passkey[] = passkeys.data?.items ?? []
  const onlyWayIn = items.length === 1 && !totpEnabled

  return (
    <SettingsSection
      id="passkeys"
      title="Passkeys"
      description="Your primary key. Phishing-proof, stored on your devices, never on this server."
      action={
        <Button
          onClick={() => {
            setAdding(true)
          }}
        >
          <KeyRound aria-hidden="true" />
          Add passkey
        </Button>
      }
    >
      {passkeys.isPending && <Skeleton className="h-14 w-full" />}
      <FormError error={visibleError(remove.error)} />
      {passkeys.data && items.length === 0 && (
        <p className="text-sm text-warning-text">
          No passkeys yet. Add one so you can always get back in.
        </p>
      )}
      {items.length > 0 && (
        <ul aria-label="Your passkeys" className="flex flex-col divide-y divide-border">
          {items.map((pk) => (
            <li
              key={pk.id}
              className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex flex-col gap-1">
                <p className="font-medium">{pk.nickname}</p>
                <p className="text-xs text-muted-foreground">
                  Added {formatDate(pk.created_at)} · Last used {formatDate(pk.last_used_at)}
                </p>
              </div>
              <Button
                variant="destructive"
                aria-label={`Remove passkey ${pk.nickname}`}
                disabled={onlyWayIn || remove.isPending}
                aria-describedby={onlyWayIn ? 'last-passkey-note' : undefined}
                onClick={() => {
                  remove.mutate(pk.id)
                }}
              >
                <Trash2 aria-hidden="true" />
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {onlyWayIn && (
        <p id="last-passkey-note" className="text-xs text-muted-foreground">
          This is your only way in. Add another passkey or turn on authenticator codes before
          removing it.
        </p>
      )}

      <ResponsiveSheet
        open={adding}
        onOpenChange={(open) => {
          if (!open) {
            setAdding(false)
            setNickname('')
            add.reset()
          }
        }}
        title="Add a passkey"
        description="Use this device, a phone, or a hardware key."
      >
        <div className="flex flex-col gap-4">
          <FormError error={add.error} />
          <Field label="Device name (optional)">
            {(p) => (
              <Input
                {...p}
                value={nickname}
                placeholder="Phone"
                autoComplete="off"
                onChange={(e) => {
                  setNickname(e.target.value)
                }}
              />
            )}
          </Field>
          <Button
            size="lg"
            disabled={add.isPending}
            onClick={() => {
              add.mutate(nickname.trim() || undefined, {
                onSuccess: () => {
                  setAdding(false)
                  setNickname('')
                },
              })
            }}
          >
            <KeyRound aria-hidden="true" />
            {add.isPending ? 'Waiting for your device…' : 'Create passkey'}
          </Button>
        </div>
      </ResponsiveSheet>
    </SettingsSection>
  )
}

// --- TOTP --------------------------------------------------------------------------------------

const codeSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^\d{6}$/, 'Enter the 6-digit code from your authenticator.'),
})
type CodeForm = z.infer<typeof codeSchema>

function downloadCodes(codes: string[]): void {
  const text = [
    'Holdfast recovery codes',
    'Each code works once. Keep them somewhere only you can reach.',
    '',
    ...codes,
    '',
  ].join('\n')
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'holdfast-recovery-codes.txt'
  a.click()
  URL.revokeObjectURL(url)
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="flex flex-col gap-4" aria-labelledby="recovery-title">
      <h3 id="recovery-title" className="text-base">
        Save your recovery codes
      </h3>
      <p className="text-sm text-muted-foreground">
        Shown once. Each code gets you in one time if you lose your authenticator. Store them
        offline.
      </p>
      <ol
        aria-label="Recovery codes"
        className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-muted p-3 font-mono text-sm tabular-nums sm:grid-cols-2"
      >
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ol>
      <div className="flex flex-col gap-2 sm:flex-row">
        <CopyButton text={codes.join('\n')} label="Copy codes" />
        <Button
          variant="outline"
          onClick={() => {
            downloadCodes(codes)
          }}
        >
          <Download aria-hidden="true" />
          Download .txt
        </Button>
        <Button onClick={onDone} className="sm:ml-auto">
          I saved them
        </Button>
      </div>
    </div>
  )
}

function TotpSection({ totpEnabled, hasPasskey }: { totpEnabled: boolean; hasPasskey: boolean }) {
  const setup = useTotpSetup()
  const enable = useTotpEnable()
  const disable = useTotpDisable()
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
  const form = useForm<CodeForm>({ resolver: zodResolver(codeSchema), defaultValues: { code: '' } })

  let body: ReactNode
  if (recoveryCodes) {
    body = (
      <RecoveryCodes
        codes={recoveryCodes}
        onDone={() => {
          setRecoveryCodes(null)
          setup.reset()
          enable.reset()
        }}
      />
    )
  } else if (totpEnabled) {
    body = (
      <div className="flex flex-col gap-3">
        <FormError error={visibleError(disable.error)} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Badge variant="outline" className="gap-1">
            <ShieldCheck aria-hidden="true" className="text-success-text" />
            On
          </Badge>
          <Button
            variant="outline"
            disabled={!hasPasskey || disable.isPending}
            onClick={() => {
              disable.mutate()
            }}
          >
            Turn off
          </Button>
        </div>
        {!hasPasskey && (
          <p className="text-xs text-muted-foreground">Add a passkey before turning codes off.</p>
        )}
      </div>
    )
  } else if (setup.data) {
    const { otpauth_uri: uri, secret } = setup.data
    body = (
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          void form.handleSubmit((v) => {
            enable.mutate(v.code, {
              onSuccess: (data) => {
                setRecoveryCodes(data.recovery_codes)
                form.reset()
              },
            })
          })(e)
        }}
      >
        <p className="text-sm text-muted-foreground">
          Scan this with your authenticator app, then enter the code it shows.
        </p>
        <div className="self-center">
          <QrCode value={uri} label="QR code for your authenticator app" />
        </div>
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Can&apos;t scan? Enter this key by hand:</p>
          <p className="font-mono text-sm break-all tabular-nums" data-testid="totp-secret">
            {secret}
          </p>
        </div>
        <FormError error={enable.error} />
        <Field label="Code from your app" error={form.formState.errors.code?.message}>
          {(p) => (
            <Input
              {...p}
              {...form.register('code')}
              inputMode="numeric"
              autoComplete="one-time-code"
              className="font-mono tabular-nums"
            />
          )}
        </Field>
        <Button type="submit" size="lg" disabled={enable.isPending}>
          Turn on codes
        </Button>
      </form>
    )
  } else {
    body = (
      <div className="flex flex-col gap-3">
        <FormError error={setup.error} />
        <Button
          className="self-start"
          disabled={setup.isPending}
          onClick={() => {
            setup.mutate()
          }}
        >
          Set up authenticator
        </Button>
      </div>
    )
  }

  return (
    <SettingsSection
      id="totp"
      title="Authenticator codes"
      description="Backup sign-in: password plus a 6-digit code. The QR code is drawn on this device; the secret never leaves your server."
    >
      {body}
    </SettingsSection>
  )
}

// --- Password ------------------------------------------------------------------------------------

const passwordSchema = z
  .object({
    password: z.string().min(12, 'Use at least 12 characters. A passphrase works well.').max(256),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ['confirm'], message: "Passwords don't match." })
type PasswordForm = z.infer<typeof passwordSchema>

function PasswordSection({
  hasPassword,
  totpEnabled,
}: {
  hasPassword: boolean
  totpEnabled: boolean
}) {
  const setPassword = useSetPassword()
  const form = useForm<PasswordForm>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { password: '', confirm: '' },
  })
  const { errors } = form.formState
  return (
    <SettingsSection
      id="password"
      title="Password"
      description={
        totpEnabled
          ? 'Used only with an authenticator code. Never on its own.'
          : 'Used only with an authenticator code, so turn those on too.'
      }
    >
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          void form.handleSubmit((v) => {
            setPassword.mutate(v.password, {
              onSuccess: () => {
                form.reset()
              },
            })
          })(e)
        }}
      >
        <FormError error={visibleError(setPassword.error)} />
        {setPassword.isSuccess && (
          <p role="status" className="text-sm text-success-text">
            Password saved.
          </p>
        )}
        <Field label={hasPassword ? 'New password' : 'Password'} error={errors.password?.message}>
          {(p) => (
            <Input
              {...p}
              {...form.register('password')}
              type="password"
              autoComplete="new-password"
            />
          )}
        </Field>
        <Field label="Confirm password" error={errors.confirm?.message}>
          {(p) => (
            <Input
              {...p}
              {...form.register('confirm')}
              type="password"
              autoComplete="new-password"
            />
          )}
        </Field>
        <Button type="submit" className="self-start" disabled={setPassword.isPending}>
          {hasPassword ? 'Change password' : 'Set password'}
        </Button>
      </form>
    </SettingsSection>
  )
}

// --- Activity ------------------------------------------------------------------------------------

function ActivitySection() {
  const audit = useAudit(true, 10)
  return (
    <SettingsSection
      id="activity"
      title="Recent activity"
      description="Sign-ins and changes, from the audit log."
    >
      {audit.isPending && <Skeleton className="h-14 w-full" />}
      {audit.data && audit.data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing yet.</p>
      )}
      {audit.data && audit.data.items.length > 0 && (
        <ul className="flex flex-col gap-2 text-sm">
          {audit.data.items.map((entry) => (
            <li key={entry.id} className="flex flex-wrap justify-between gap-x-3">
              <span className="font-mono text-xs">{entry.action}</span>
              <span
                className={
                  entry.outcome === 'failure'
                    ? 'text-xs text-destructive-text'
                    : 'text-xs text-muted-foreground'
                }
              >
                {entry.outcome === 'failure' ? 'Failed · ' : ''}
                {entry.actor_display_name ?? 'Unknown'} · {formatDate(entry.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  )
}

export function SecuritySettings() {
  const { data: me } = useMe()
  const passkeys = usePasskeys()
  if (!me) return null
  const hasPasskey = (passkeys.data?.items.length ?? 0) > 0
  return (
    <div className="flex flex-col gap-6">
      <PasskeysSection totpEnabled={me.user.totp_enabled} />
      <TotpSection totpEnabled={me.user.totp_enabled} hasPasskey={hasPasskey} />
      <PasswordSection hasPassword={me.user.has_password} totpEnabled={me.user.totp_enabled} />
      <SettingsSection
        id="sessions"
        title="Active sessions"
        description="See and revoke signed-in devices. Coming in Phase 9. Sessions expire after 12 hours."
      >
        <p className="text-xs text-muted-foreground">Coming in Phase 9</p>
      </SettingsSection>
      {me.user.role === 'owner' && <ActivitySection />}
    </div>
  )
}
