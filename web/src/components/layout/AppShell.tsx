import { Outlet, Link } from 'react-router'

import { Logomark, Wordmark } from '@/components/brand/Logomark'
import { ApiStatusIndicator } from '@/components/layout/ApiStatusIndicator'
import { MissingPasskeyBanner } from '@/components/layout/MissingPasskeyBanner'
import { Sidebar } from '@/components/layout/Sidebar'
import { BottomNav } from '@/components/mobile/BottomNav'
import { useIsDesktop } from '@/hooks/useBreakpoint'

const FOOTER_LINE = 'Not a bank. Not a CPA. Your software, your data.'

function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only rounded-lg bg-primary px-4 py-3 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50"
    >
      Skip to content
    </a>
  )
}

export function AppShell() {
  const isDesktop = useIsDesktop()

  if (isDesktop) {
    return (
      <div className="flex min-h-dvh" data-layout="desktop">
        <SkipLink />
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <main id="main" tabIndex={-1} className="flex-1 px-8 py-8 outline-none lg:px-10">
            <MissingPasskeyBanner />
            <Outlet />
          </main>
          <footer className="px-8 pb-6 text-xs text-muted-foreground lg:px-10">
            {FOOTER_LINE}
          </footer>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col" data-layout="mobile">
      <SkipLink />
      <header className="sticky top-0 z-30 flex min-h-12 items-center justify-between gap-3 border-b border-border bg-background/90 pt-[env(safe-area-inset-top)] pr-[max(1rem,env(safe-area-inset-right))] pl-[max(1rem,env(safe-area-inset-left))] backdrop-blur">
        <Link to="/" aria-label="holdfast home" className="flex min-h-11 items-center gap-2">
          <Logomark className="size-6 text-primary" />
          <Wordmark />
        </Link>
        <ApiStatusIndicator />
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="flex-1 pt-5 pr-[max(1rem,env(safe-area-inset-right))] pb-28 pl-[max(1rem,env(safe-area-inset-left))] outline-none"
      >
        <MissingPasskeyBanner />
        <Outlet />
        <p className="mt-10 text-xs text-muted-foreground">{FOOTER_LINE}</p>
      </main>
      <BottomNav />
    </div>
  )
}
