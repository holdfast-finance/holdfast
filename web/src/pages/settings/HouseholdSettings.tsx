import { zodResolver } from '@hookform/resolvers/zod'
import { Pencil, Send, Trash2, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { useMe } from '@/api/auth'
import {
  useCreatePerson,
  useDeletePerson,
  useHousehold,
  useInvitePerson,
  usePeople,
  useRenameHousehold,
  useUpdatePerson,
} from '@/api/household'
import type { Person } from '@/api/types'
import { CopyButton } from '@/components/CopyButton'
import { Field, FormError } from '@/components/forms/Field'
import { ResponsiveSheet } from '@/components/ResponsiveSheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input, NativeSelect } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ReauthCancelledError } from '@/stores/reauth'
import { FILING_ROLE_LABELS } from '@/pages/settings/labels'
import { SettingsSection } from '@/pages/settings/SettingsLayout'

const personSchema = z.object({
  display_name: z.string().trim().min(1, 'Give them a name.').max(80),
  filing_role: z.enum(['primary', 'spouse', 'dependent']),
})
type PersonForm = z.infer<typeof personSchema>

const householdSchema = z.object({ name: z.string().trim().min(1, 'Name your household.').max(80) })
type HouseholdForm = z.infer<typeof householdSchema>

const inviteSchema = z.object({ email: z.email('Enter the email they will sign in with.') })
type InviteForm = z.infer<typeof inviteSchema>

function PersonFormFields({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  initial: PersonForm
  submitLabel: string
  pending: boolean
  error: unknown
  onSubmit: (values: PersonForm) => void
}) {
  const form = useForm<PersonForm>({ resolver: zodResolver(personSchema), defaultValues: initial })
  const { errors } = form.formState
  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit(onSubmit)(e)
      }}
    >
      <FormError error={error} />
      <Field label="Name" error={errors.display_name?.message}>
        {(p) => <Input {...p} {...form.register('display_name')} autoComplete="off" />}
      </Field>
      <Field label="Filing role" error={errors.filing_role?.message} hint="Used later by the optional tax module.">
        {(p) => (
          <NativeSelect {...p} {...form.register('filing_role')}>
            {Object.entries(FILING_ROLE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Button type="submit" size="lg" disabled={pending}>
        {submitLabel}
      </Button>
    </form>
  )
}

function InviteFlow({ person, onClose }: { person: Person; onClose: () => void }) {
  const invite = useInvitePerson()
  const form = useForm<InviteForm>({ resolver: zodResolver(inviteSchema), defaultValues: { email: '' } })
  if (invite.data) {
    const url = invite.data.invite_url
    return (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Send this to {person.display_name} however you like. It works once and expires in 24
          hours. Holdfast doesn&apos;t email it for you.
        </p>
        <Input readOnly value={url} aria-label="Invite link" className="font-mono text-xs md:text-xs" onFocus={(e) => { e.currentTarget.select() }} />
        <CopyButton text={url} label="Copy invite link" variant="default" />
        <Button variant="outline" onClick={onClose}>
          Done
        </Button>
      </div>
    )
  }
  const error = invite.error instanceof ReauthCancelledError ? null : invite.error
  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        void form.handleSubmit((v) => {
          invite.mutate({ id: person.id, body: v })
        })(e)
      }}
    >
      <FormError error={error} />
      <Field label="Their email" error={form.formState.errors.email?.message} hint="This becomes their sign-in name.">
        {(p) => <Input {...p} {...form.register('email')} type="email" autoComplete="off" />}
      </Field>
      <Button type="submit" size="lg" disabled={invite.isPending}>
        <Send aria-hidden="true" />
        Create invite link
      </Button>
    </form>
  )
}

type Panel = { kind: 'add' } | { kind: 'edit'; person: Person } | { kind: 'invite'; person: Person } | null

function HouseholdName({ name, isOwner }: { name: string; isOwner: boolean }) {
  const rename = useRenameHousehold()
  const [editing, setEditing] = useState(false)
  const form = useForm<HouseholdForm>({ resolver: zodResolver(householdSchema), values: { name } })
  if (!editing) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-heading text-xl">{name}</p>
        {isOwner && (
          <Button variant="outline" onClick={() => { setEditing(true) }}>
            <Pencil aria-hidden="true" />
            Rename
          </Button>
        )}
      </div>
    )
  }
  return (
    <form
      noValidate
      className="flex flex-col gap-3 sm:flex-row sm:items-end"
      onSubmit={(e) => {
        void form.handleSubmit((v) => {
          rename.mutate(v, { onSuccess: () => { setEditing(false) } })
        })(e)
      }}
    >
      <Field label="Household name" error={form.formState.errors.name?.message} className="flex-1">
        {(p) => <Input {...p} {...form.register('name')} autoComplete="off" />}
      </Field>
      <div className="flex gap-2">
        <Button type="button" variant="outline" onClick={() => { setEditing(false) }}>
          Cancel
        </Button>
        <Button type="submit" disabled={rename.isPending}>
          Save
        </Button>
      </div>
      <FormError error={rename.error} />
    </form>
  )
}

export function HouseholdSettings() {
  const { data: me } = useMe()
  const household = useHousehold()
  const people = usePeople()
  const create = useCreatePerson()
  const update = useUpdatePerson()
  const remove = useDeletePerson()
  const [panel, setPanel] = useState<Panel>(null)
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null)
  const isOwner = me?.user.role === 'owner'

  const close = () => {
    setPanel(null)
    create.reset()
    update.reset()
  }

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection id="household-name" title="Household">
        {household.data ? (
          <HouseholdName name={household.data.name} isOwner={isOwner} />
        ) : (
          <Skeleton className="h-7 w-48" />
        )}
      </SettingsSection>

      <SettingsSection
        id="people"
        title="People"
        description={
          isOwner
            ? 'Everyone in the household. Invite someone to give them their own sign-in.'
            : 'Everyone in the household. Only the owner can change this list.'
        }
        action={
          isOwner && (
            <Button onClick={() => { setPanel({ kind: 'add' }) }}>
              <UserPlus aria-hidden="true" />
              Add person
            </Button>
          )
        }
      >
        {people.isPending && <Skeleton className="h-16 w-full" />}
        {people.isError && <FormError error={people.error} />}
        {remove.error && <FormError error={remove.error} />}
        {people.data && (
          <ul className="flex flex-col divide-y divide-border" aria-label="People">
            {people.data.items.map((person) => (
              <li key={person.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="truncate font-medium">
                    {person.display_name}
                    {person.id === me?.user.person?.id && <span className="text-muted-foreground"> (you)</span>}
                  </p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>{FILING_ROLE_LABELS[person.filing_role]}</span>
                    <Badge variant="outline">{person.has_account ? 'Has sign-in' : 'No sign-in yet'}</Badge>
                  </div>
                </div>
                {isOwner && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      aria-label={`Edit ${person.display_name}`}
                      onClick={() => { setPanel({ kind: 'edit', person }) }}
                    >
                      <Pencil aria-hidden="true" />
                      Edit
                    </Button>
                    {!person.has_account && (
                      <>
                        <Button
                          variant="outline"
                          aria-label={`Invite ${person.display_name}`}
                          onClick={() => { setPanel({ kind: 'invite', person }) }}
                        >
                          <Send aria-hidden="true" />
                          Invite
                        </Button>
                        {confirmingRemove === person.id ? (
                          <Button
                            variant="destructive"
                            aria-label={`Confirm removing ${person.display_name}`}
                            disabled={remove.isPending}
                            onClick={() => {
                              remove.mutate(person.id, {
                                onSettled: () => {
                                  setConfirmingRemove(null)
                                },
                              })
                            }}
                          >
                            <Trash2 aria-hidden="true" />
                            Yes, remove
                          </Button>
                        ) : (
                          <Button
                            variant="destructive"
                            aria-label={`Remove ${person.display_name}`}
                            onClick={() => {
                              setConfirmingRemove(person.id)
                            }}
                          >
                            <Trash2 aria-hidden="true" />
                            Remove
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>

      <ResponsiveSheet open={panel?.kind === 'add'} onOpenChange={(o) => { if (!o) close() }} title="Add a person">
        <PersonFormFields
          initial={{ display_name: '', filing_role: 'spouse' }}
          submitLabel="Add person"
          pending={create.isPending}
          error={create.error}
          onSubmit={(v) => { create.mutate(v, { onSuccess: close }) }}
        />
      </ResponsiveSheet>

      <ResponsiveSheet open={panel?.kind === 'edit'} onOpenChange={(o) => { if (!o) close() }} title="Edit person">
        {panel?.kind === 'edit' && (
          <PersonFormFields
            initial={{ display_name: panel.person.display_name, filing_role: panel.person.filing_role }}
            submitLabel="Save changes"
            pending={update.isPending}
            error={update.error}
            onSubmit={(v) => { update.mutate({ id: panel.person.id, body: v }, { onSuccess: close }) }}
          />
        )}
      </ResponsiveSheet>

      <ResponsiveSheet
        open={panel?.kind === 'invite'}
        onOpenChange={(o) => { if (!o) close() }}
        title={panel?.kind === 'invite' ? `Invite ${panel.person.display_name}` : 'Invite'}
        description="They get their own passkey. You never see or share their credentials."
      >
        {panel?.kind === 'invite' && <InviteFlow person={panel.person} onClose={close} />}
      </ResponsiveSheet>
    </div>
  )
}
