import type { ReactNode } from 'react'
import { LogOut } from 'lucide-react'
import { NavLink, Outlet, useNavigate } from 'react-router'

import { useLogout, useMe } from '@/api/auth'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const TABS = [
  { to: 'household', label: 'Household' },
  { to: 'security', label: 'Security' },
] as const

export function SettingsLayout() {
  const { data: me } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 id="page-title" className="text-3xl md:text-4xl">
            Settings
          </h1>
          {me && (
            <p className="text-sm text-muted-foreground">
              Signed in as {me.user.person?.display_name ?? me.user.email} · {me.household.name}
            </p>
          )}
        </div>
        <Button
          variant="outline"
          disabled={logout.isPending}
          onClick={() => {
            logout.mutate(undefined, {
              onSettled: () => {
                void navigate('/login', { replace: true })
              },
            })
          }}
        >
          <LogOut aria-hidden="true" />
          Sign out
        </Button>
      </header>
      <nav aria-label="Settings sections" className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) =>
              cn(
                '-mb-px flex min-h-11 items-center border-b-2 px-4 text-sm font-medium transition-colors',
                isActive
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </section>
  )
}

/** A titled block inside a settings page. */
export function SettingsSection({
  id,
  title,
  description,
  action,
  children,
}: {
  id: string
  title: string
  description?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4 rounded-card border border-border bg-card p-4 md:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id={id} className="text-lg">
            {title}
          </h2>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
