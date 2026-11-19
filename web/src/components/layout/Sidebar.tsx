import { NavLink } from 'react-router'

import { PAGES, SIDEBAR_PRIMARY, SIDEBAR_SECONDARY, type PageId } from '@/app/nav'
import { Logomark, Wordmark } from '@/components/brand/Logomark'
import { ApiStatusIndicator } from '@/components/layout/ApiStatusIndicator'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

function SidebarLink({ id }: { id: PageId }) {
  const page = PAGES[id]
  const Icon = page.icon
  return (
    <li>
      <NavLink
        to={page.path}
        end={page.path === '/'}
        className={({ isActive }) =>
          cn(
            'flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
            isActive && 'bg-sidebar-accent text-foreground',
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon aria-hidden="true" className={cn('size-4', isActive && 'text-primary-text')} />
            {page.label}
          </>
        )}
      </NavLink>
    </li>
  )
}

export function Sidebar() {
  return (
    <aside
      data-testid="desktop-sidebar"
      className="sticky top-0 flex h-dvh w-60 shrink-0 flex-col border-r border-border bg-sidebar px-3 py-5"
    >
      <NavLink
        to="/"
        className="mb-6 flex min-h-11 items-center gap-2.5 rounded-lg px-3"
        aria-label="holdfast home"
      >
        <Logomark className="size-6 text-primary" />
        <Wordmark />
      </NavLink>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-4 overflow-y-auto">
        <ul className="flex flex-col gap-0.5">
          {SIDEBAR_PRIMARY.map((id) => (
            <SidebarLink key={id} id={id} />
          ))}
        </ul>
        <Separator />
        <ul className="flex flex-col gap-0.5" aria-label="Records">
          {SIDEBAR_SECONDARY.map((id) => (
            <SidebarLink key={id} id={id} />
          ))}
        </ul>
      </nav>
      <div className="mt-4 flex items-center justify-between gap-2 border-t border-border px-3 pt-4">
        <ApiStatusIndicator />
        <ThemeToggle />
      </div>
    </aside>
  )
}
