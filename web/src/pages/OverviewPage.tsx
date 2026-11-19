import { Lightbulb } from 'lucide-react'

import { StatusChip, type SyncStatus } from '@/components/StatusChip'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/*
 * Phase 0: static layout only. Everything on this page is DEMO DATA with placeholders
 * (BRAND.md "App overview"). Real figures arrive in Phase 5 from the API, as decimal strings.
 */

interface DemoAccount {
  name: string
  type: string
  status: SyncStatus
  balance: string
}

const DEMO_ACCOUNTS: DemoAccount[] = [
  { name: 'Checking ··0000', type: 'Bank', status: 'synced', balance: '[BALANCE]' },
  { name: 'Credit card ··0000', type: 'Card', status: 'relink', balance: '[BALANCE]' },
  { name: '401(k)', type: 'Retirement', status: 'synced', balance: '[BALANCE]' },
  { name: 'HSA', type: 'Health', status: 'synced', balance: '[BALANCE]' },
  { name: 'Cold storage (multisig)', type: 'Bitcoin', status: 'watch-only', balance: '[BTC]' },
]

interface DemoBudget {
  name: string
  pct: number
  tone: 'success' | 'warning' | 'secondary'
  note?: string
}

const DEMO_BUDGETS: DemoBudget[] = [
  { name: 'Groceries', pct: 62, tone: 'success' },
  { name: 'Dining', pct: 91, tone: 'warning', note: 'pace high' },
  { name: 'Gas & auto', pct: 40, tone: 'success' },
  { name: 'Subscriptions', pct: 78, tone: 'secondary' },
]

const BAR_TONE = {
  success: 'bg-success',
  warning: 'bg-warning',
  secondary: 'bg-secondary',
} as const
const TEXT_TONE = {
  success: 'text-muted-foreground',
  warning: 'text-warning-text',
  secondary: 'text-muted-foreground',
} as const

function DemoTag() {
  return <span className="text-muted-foreground"> · demo data</span>
}

function ForecastSketch() {
  return (
    <svg
      viewBox="0 0 600 160"
      preserveAspectRatio="none"
      className="h-36 w-full"
      role="img"
      aria-label="Illustration of an actual balance line joining a projected band. Demo data."
    >
      <line x1="0" x2="600" y1="40" y2="40" className="stroke-border" strokeWidth="1" />
      <line x1="0" x2="600" y1="120" y2="120" className="stroke-border" strokeWidth="1" />
      <path
        d="M345 80 C 420 70, 520 50, 600 40 L600 120 C 520 112, 420 96, 345 84 Z"
        className="fill-chart-forecast/20"
      />
      <path
        d="M0 84 C 80 80, 160 92, 240 84 S 320 78, 345 82"
        className="fill-none stroke-chart-actual"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M345 82 C 420 80, 520 76, 600 74"
        className="fill-none stroke-chart-forecast"
        strokeWidth="3"
        strokeDasharray="8 7"
        strokeLinecap="round"
      />
      <circle cx="345" cy="82" r="6" className="fill-card stroke-chart-actual" strokeWidth="3" />
    </svg>
  )
}

export function OverviewPage() {
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">
            Net worth
            <DemoTag />
          </p>
          <h1 id="page-title" className="sr-only">
            Overview
          </h1>
          <p className="money text-4xl font-medium tracking-tight text-foreground sm:text-5xl md:text-6xl">
            $[NET WORTH]
          </p>
        </div>
        <ul aria-label="Sync status (demo data)" className="flex flex-wrap gap-2">
          <li>
            <StatusChip status="synced" label="9 of 10 accounts synced" />
          </li>
          <li>
            <StatusChip status="relink" label="1 relink needed" />
          </li>
        </ul>
      </header>

      <p className="text-xs text-secondary-text">
        Coming in Phase 5. This page shows the layout with demo data only.
      </p>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="rounded-card border border-border ring-0 lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base font-semibold">90-day cash forecast</CardTitle>
            <p className="text-xs text-muted-foreground">
              P10 – P90 band · backtest error shown in Forecast
              <DemoTag />
            </p>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <ForecastSketch />
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-0.5 w-4 rounded bg-chart-actual" />
                Actual
              </span>
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-0.5 w-4 rounded bg-chart-forecast" />
                Projected (P50)
              </span>
            </div>
          </CardContent>
        </Card>

        <Card className="glass-card rounded-card ring-0">
          <CardHeader>
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Lightbulb aria-hidden="true" className="size-4" />
              <span>
                Advisor · local model
                <DemoTag />
              </span>
            </p>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <p className="text-base leading-relaxed">
              Your streaming subscriptions went up this month across two services. Want a list of
              what changed?
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="lg" disabled title="Advisor arrives in Phase 8">
                Show me
              </Button>
              <Button size="lg" variant="outline" disabled title="Advisor arrives in Phase 8">
                Dismiss
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-card border border-border ring-0 lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base font-semibold">
              Accounts
              <DemoTag />
            </CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full min-w-[32rem] text-left text-sm">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border">
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Account
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Type
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    Status
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody>
                {DEMO_ACCOUNTS.map((account) => (
                  <tr key={account.name} className="border-b border-border last:border-0">
                    <td className="py-3 pr-4">{account.name}</td>
                    <td className="py-3 pr-4 text-muted-foreground">{account.type}</td>
                    <td className="py-3 pr-4">
                      <StatusChip status={account.status} />
                    </td>
                    <td className="money py-3 text-right">{account.balance}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card className="rounded-card border border-border ring-0">
          <CardHeader>
            <CardTitle className="text-base font-semibold">
              Budgets · this month
              <DemoTag />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-4">
              {DEMO_BUDGETS.map((budget) => (
                <li key={budget.name} className="flex flex-col gap-1.5">
                  <div className="flex items-baseline justify-between text-sm">
                    <span>{budget.name}</span>
                    <span className={cn('money text-xs', TEXT_TONE[budget.tone])}>
                      {budget.pct}%{budget.note ? ` · ${budget.note}` : ''}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-accent" role="presentation">
                    <div
                      className={cn('h-full rounded-full', BAR_TONE[budget.tone])}
                      style={{ width: `${String(budget.pct)}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3 sm:grid-cols-3" aria-hidden="true">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    </section>
  )
}
