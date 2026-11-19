import type { ReactNode } from 'react'

import { Logomark, Wordmark } from '@/components/brand/Logomark'
import { Skeleton } from '@/components/ui/skeleton'

/*
 * Suspense fallbacks for lazy routes (src/app/lazyRoutes.ts). Each has the same frame as the
 * page it stands in for, so the swap doesn't shift the layout.
 */

function Loading({ children }: { children: ReactNode }) {
  return (
    <div role="status" aria-busy="true" className="contents">
      <span className="sr-only">Loading</span>
      {children}
    </div>
  )
}

/** Stands in for AuthLayout (/login, /setup, /invite) while the page chunk loads. */
export function AuthPageSkeleton() {
  return (
    <Loading>
      <div className="flex min-h-dvh flex-col bg-background px-4 py-8" aria-hidden="true">
        <div className="mx-auto flex w-full max-w-md flex-1 flex-col gap-8">
          <div className="flex items-center gap-2.5">
            <Logomark className="size-7 text-primary" />
            <Wordmark />
          </div>
          <div className="flex flex-col gap-2">
            <Skeleton className="h-9 w-2/3" />
            <Skeleton className="h-4 w-full" />
          </div>
          <div className="flex flex-col gap-3">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        </div>
      </div>
    </Loading>
  )
}

/** Stands in for a page inside the AppShell <main> (settings) while its chunk loads. */
export function ShellPageSkeleton() {
  return (
    <Loading>
      <div className="mx-auto flex max-w-3xl flex-col gap-6" aria-hidden="true">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-40 w-full rounded-card" />
        <Skeleton className="h-40 w-full rounded-card" />
      </div>
    </Loading>
  )
}

/** Stands in for one settings tab below the tab bar. */
export function SectionSkeleton() {
  return (
    <Loading>
      <div className="flex flex-col gap-6" aria-hidden="true">
        <Skeleton className="h-40 w-full rounded-card" />
        <Skeleton className="h-40 w-full rounded-card" />
      </div>
    </Loading>
  )
}
