import type { ReactNode } from 'react'

import { Logomark, Wordmark } from '@/components/brand/Logomark'

/** Centered single-column frame for /login, /setup and /invite. Mobile-first, no app chrome. */
export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-h-dvh flex-col bg-background px-4 py-8">
      <main id="main" className="mx-auto flex w-full max-w-md flex-1 flex-col gap-8">
        <div className="flex items-center gap-2.5">
          <Logomark className="size-7 text-primary" />
          <Wordmark />
        </div>
        <header className="flex flex-col gap-2">
          <h1 className="text-3xl">{title}</h1>
          {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
        </header>
        {children}
      </main>
      <footer className="mx-auto mt-10 w-full max-w-md text-xs text-muted-foreground">
        Self-hosted. Your server, your keys, your data.
      </footer>
    </div>
  )
}

/** Full-screen loading state while the session is resolved. */
export function Splash() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-background" role="status">
      <Logomark className="size-10 animate-pulse text-primary motion-reduce:animate-none" />
      <span className="sr-only">Loading</span>
    </div>
  )
}
