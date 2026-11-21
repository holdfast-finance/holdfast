import { Ellipsis } from 'lucide-react'
import { useState } from 'react'
import { NavLink, useLocation } from 'react-router'

import { MOBILE_MORE, MOBILE_TABS, PAGES, type PageId } from '@/app/nav'
import { ApiStatusIndicator } from '@/components/layout/ApiStatusIndicator'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

const tabClass =
  'flex min-h-14 min-w-11 flex-1 flex-col items-center justify-center gap-1 rounded-lg px-1 text-[11px] font-medium text-muted-foreground transition-colors'

function Tab({ id }: { id: PageId }) {
  const page = PAGES[id]
  const Icon = page.icon
  return (
    <li className="flex flex-1">
      <NavLink
        to={page.path}
        end={page.path === '/'}
        className={({ isActive }) => cn(tabClass, isActive && 'text-primary-text')}
      >
        <Icon aria-hidden="true" className="size-5" />
        {page.shortLabel ?? page.label}
      </NavLink>
    </li>
  )
}

function MoreSheet() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  const moreActive = MOBILE_MORE.some((id) => PAGES[id].path === pathname)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <li className="flex flex-1">
        <SheetTrigger asChild>
          <button type="button" className={cn(tabClass, moreActive && 'text-primary-text')}>
            <Ellipsis aria-hidden="true" className="size-5" />
            More
          </button>
        </SheetTrigger>
      </li>
      <SheetContent
        side="bottom"
        className="max-h-[85dvh] rounded-t-[var(--radius-card)] pb-[max(1.5rem,env(safe-area-inset-bottom))]"
      >
        <SheetHeader>
          <SheetTitle className="font-heading text-lg font-semibold">More</SheetTitle>
          <SheetDescription>Every account. One root.</SheetDescription>
        </SheetHeader>
        <nav aria-label="More pages" className="overflow-y-auto px-2">
          <ul className="grid grid-cols-1 gap-0.5">
            {MOBILE_MORE.map((id) => {
              const page = PAGES[id]
              const Icon = page.icon
              return (
                <li key={id}>
                  <NavLink
                    to={page.path}
                    onClick={() => {
                      setOpen(false)
                    }}
                    className={({ isActive }) =>
                      cn(
                        'flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium text-foreground hover:bg-accent',
                        isActive && 'bg-sidebar-accent text-primary-text',
                      )
                    }
                  >
                    <Icon aria-hidden="true" className="size-4" />
                    {page.label}
                  </NavLink>
                </li>
              )
            })}
          </ul>
        </nav>
        <div className="flex items-center justify-between gap-2 border-t border-border px-5 pt-4">
          <ApiStatusIndicator />
          <ThemeToggle />
        </div>
      </SheetContent>
    </Sheet>
  )
}

export function BottomNav() {
  return (
    <nav
      aria-label="Main"
      data-testid="mobile-tabbar"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur"
    >
      <ul className="mx-auto flex max-w-lg items-stretch gap-1 py-1 pr-[max(0.5rem,env(safe-area-inset-right))] pl-[max(0.5rem,env(safe-area-inset-left))]">
        {MOBILE_TABS.map((id) => (
          <Tab key={id} id={id} />
        ))}
        <MoreSheet />
      </ul>
    </nav>
  )
}
