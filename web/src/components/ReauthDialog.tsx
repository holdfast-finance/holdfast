import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { reauthWithPasskey, reauthWithTotp, useMe } from '@/api/auth'
import { Field, FormError } from '@/components/forms/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useIsDesktop } from '@/hooks/useBreakpoint'
import { useReauthStore } from '@/stores/reauth'

const codeSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^\d{6}$/, 'Enter the 6-digit code from your authenticator.'),
})
type CodeForm = z.infer<typeof codeSchema>

/**
 * Step-up prompt for sensitive actions. Opens when `withReauth` hits `reauth_required`;
 * a bottom sheet on phones, a side sheet on desktop (no modals on small screens).
 */
export function ReauthDialog() {
  const pending = useReauthStore((s) => s.pending)
  const confirm = useReauthStore((s) => s.confirm)
  const cancel = useReauthStore((s) => s.cancel)
  const isDesktop = useIsDesktop()
  const { data: me } = useMe()
  const totpEnabled = me?.user.totp_enabled ?? false
  const [useCode, setUseCode] = useState(false)

  const passkey = useMutation({ mutationFn: reauthWithPasskey, onSuccess: confirm })
  const totp = useMutation({
    mutationFn: (code: string) => reauthWithTotp(code),
    onSuccess: confirm,
  })
  const form = useForm<CodeForm>({ resolver: zodResolver(codeSchema), defaultValues: { code: '' } })

  const open = pending !== null
  const onOpenChange = (next: boolean) => {
    if (!next) {
      cancel()
      passkey.reset()
      totp.reset()
      form.reset()
      setUseCode(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={isDesktop ? 'right' : 'bottom'}
        className="max-h-[90dvh] gap-6 overflow-y-auto p-5 pb-8"
      >
        <SheetHeader className="p-0">
          <SheetTitle>Confirm it&apos;s you</SheetTitle>
          <SheetDescription>
            Sensitive action. One check, then you&apos;re back to it.
          </SheetDescription>
        </SheetHeader>
        {!useCode ? (
          <div className="flex flex-col gap-3">
            <FormError error={passkey.error} />
            <Button
              type="button"
              size="lg"
              disabled={passkey.isPending}
              onClick={() => {
                passkey.mutate()
              }}
            >
              <KeyRound aria-hidden="true" />
              {passkey.isPending ? 'Waiting for your passkey…' : 'Confirm with passkey'}
            </Button>
            {totpEnabled && (
              <Button
                type="button"
                variant="link"
                className="min-h-11"
                onClick={() => {
                  setUseCode(true)
                }}
              >
                Use an authenticator code instead
              </Button>
            )}
          </div>
        ) : (
          <form
            noValidate
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              void form.handleSubmit((values) => {
                totp.mutate(values.code)
              })(e)
            }}
          >
            <FormError error={totp.error} />
            <Field label="Authenticator code" error={form.formState.errors.code?.message}>
              {(props) => (
                <Input
                  {...props}
                  {...form.register('code')}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  // The field replaces the button the user just pressed; put focus in it.
                  autoFocus
                  className="font-mono tabular-nums"
                />
              )}
            </Field>
            <Button type="submit" size="lg" disabled={totp.isPending}>
              Confirm
            </Button>
          </form>
        )}
      </SheetContent>
    </Sheet>
  )
}
