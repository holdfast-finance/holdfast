import {
  Check,
  CircleDashed,
  ExternalLink,
  Link2,
  Minus,
  RefreshCw,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { useMe } from '@/api/auth'
import {
  useCheckPlaidSetup,
  usePlaidSetup,
  type PlaidSetup,
  type PlaidSetupStep,
} from '@/api/plaid'
import { CopyButton } from '@/components/CopyButton'
import { FormError } from '@/components/forms/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConnectionList } from '@/features/connections/ConnectionList'
import { LinkFlow } from '@/features/connections/LinkFlow'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import {
  PRODUCT_STATE_LABELS,
  STATE_LABELS,
  STEP_TITLES,
  productLabel,
  stepCopy,
} from '@/pages/settings/plaidCopy'
import { SettingsSection } from '@/pages/settings/SettingsLayout'

const STATE_STYLE: Record<PlaidSetupStep['state'], { icon: typeof Check; tone: string }> = {
  done: { icon: Check, tone: 'bg-success/12 text-success-text' },
  action: { icon: TriangleAlert, tone: 'bg-warning/12 text-warning-text' },
  blocked: { icon: X, tone: 'bg-destructive/12 text-destructive-text' },
  optional: { icon: Minus, tone: 'bg-muted text-muted-foreground' },
  unchecked: { icon: CircleDashed, tone: 'bg-muted text-muted-foreground' },
  warn: { icon: TriangleAlert, tone: 'bg-warning/12 text-warning-text' },
}

const ENV_LABELS: Record<PlaidSetup['env'], string> = {
  sandbox: 'Sandbox · fake data',
  production: 'Production · real data',
}

function headline(setup: PlaidSetup): string {
  if (setup.ready) {
    return setup.env === 'sandbox'
      ? 'Plaid is set up for Sandbox. You can link a test bank above.'
      : 'Plaid is set up for real data. You can link a bank above.'
  }
  if (!setup.checked && setup.steps[0]?.state === 'done') {
    return 'Your keys are in place. Run the check to try them against Plaid.'
  }
  return 'Work down the list. Holdfast checks each step with Plaid for you.'
}

function StepRow({ step, setup }: { step: PlaidSetupStep; setup: PlaidSetup }) {
  const style = STATE_STYLE[step.state]
  const Icon = style.icon
  const copy = stepCopy(step)
  const address = setup.redirect_uri ?? setup.suggested_redirect_uri
  return (
    <li className="flex gap-3 py-4" data-step={step.id} data-state={step.state}>
      <span
        aria-hidden="true"
        className={cn(
          'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full',
          style.tone,
        )}
      >
        <Icon className="size-4" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h3 className="font-medium">{STEP_TITLES[step.id]}</h3>
          {/* The state is always written out: color and icon never carry it alone. */}
          <Badge variant="outline">{STATE_LABELS[step.state]}</Badge>
        </div>
        {copy.body && <p className="text-sm text-muted-foreground">{copy.body}</p>}

        {step.id === 'products' && setup.products.length > 0 && (
          <ul aria-label="Plaid products" className="flex flex-wrap gap-2">
            {setup.products.map((p) => (
              <li key={p.product} className="rounded-full border border-border px-2.5 py-1 text-xs">
                <span className="font-medium">{productLabel(p.product)}</span>
                <span className="text-muted-foreground"> · {PRODUCT_STATE_LABELS[p.state]}</span>
              </li>
            ))}
          </ul>
        )}

        {step.id === 'redirect_uri' && step.state !== 'done' && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <code className="min-w-0 rounded-lg border border-border bg-muted px-3 py-2 font-mono text-xs break-all">
              {address}
            </code>
            <CopyButton text={address} label="Copy address" />
          </div>
        )}

        {step.id === 'item_slots' && setup.env === 'production' && (
          <p className="text-sm">
            <span className="font-mono tabular-nums">
              {setup.item_slots_used} of {setup.item_slots_total}
            </span>{' '}
            used
          </p>
        )}

        {copy.link && step.state !== 'done' && (
          <a
            href={copy.link.href}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex min-h-11 w-fit items-center gap-1.5 text-sm font-medium text-primary-text underline-offset-4 hover:underline"
          >
            {copy.link.label}
            <ExternalLink aria-hidden="true" className="size-3.5" />
            <span className="sr-only">(opens plaid.com in a new tab)</span>
          </a>
        )}

        {step.request_id && (
          <p className="text-xs text-muted-foreground">
            Plaid request ID, if you contact their support:{' '}
            <span className="font-mono">{step.request_id}</span>
          </p>
        )}
      </div>
    </li>
  )
}

export function ConnectionsSettings() {
  const { data: me } = useMe()
  const isOwner = me?.user.role === 'owner'
  const setup = usePlaidSetup(isOwner)
  const check = useCheckPlaidSetup()

  const [linking, setLinking] = useState(false)

  if (me && !isOwner) {
    return (
      <div className="flex flex-col gap-6">
        <SettingsSection
          id="connections"
          title="Connections"
          description="Only the household owner can link or change connections."
        >
          <ConnectionList isOwner={false} />
        </SettingsSection>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection
        id="connections"
        title="Connections"
        description="Banks linked through Plaid."
        action={
          <Button
            disabled={!setup.data?.ready}
            onClick={() => {
              setLinking(true)
            }}
          >
            <Link2 aria-hidden="true" />
            Link a bank
          </Button>
        }
      >
        {setup.data && !setup.data.ready && (
          <p className="text-sm text-muted-foreground">
            Finish the Plaid checklist below first. Linking opens once every required step is done.
          </p>
        )}
        <ConnectionList isOwner />
      </SettingsSection>
      {linking && setup.data && (
        <LinkFlow
          env={setup.data.env}
          onClose={() => {
            setLinking(false)
          }}
        />
      )}
      <SettingsSection
        id="plaid"
        title="Plaid"
        description="Your keys, your Plaid account. Nobody sits between you and your bank data."
        action={
          setup.data && (
            <Button
              disabled={check.isPending}
              onClick={() => {
                check.mutate()
              }}
            >
              <RefreshCw aria-hidden="true" className={cn(check.isPending && 'animate-spin')} />
              {check.isPending ? 'Checking…' : setup.data.checked ? 'Check again' : 'Run check'}
            </Button>
          )
        }
      >
        {setup.isPending && <Skeleton className="h-40 w-full" />}
        {setup.isError && <FormError error={setup.error} />}
        {check.error && <FormError error={check.error} />}
        {setup.data && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{ENV_LABELS[setup.data.env]}</Badge>
            </div>
            <p role="status" className="text-sm">
              {headline(setup.data)}
            </p>
            <ol aria-label="Plaid setup steps" className="flex flex-col divide-y divide-border">
              {setup.data.steps.map((step) => (
                <StepRow key={step.id} step={step} setup={setup.data} />
              ))}
            </ol>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        id="no-plaid"
        title="No Plaid account?"
        description="You don't need one to use Holdfast."
      >
        <p className="text-sm text-muted-foreground">
          Statement import works without any bank connection, and it reaches further back than
          Plaid's two years.
        </p>
        <Button asChild variant="outline" className="w-fit">
          <Link to="/import">Import statements</Link>
        </Button>
      </SettingsSection>
    </div>
  )
}
