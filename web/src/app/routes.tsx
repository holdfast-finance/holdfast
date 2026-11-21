import { Suspense, type ReactNode } from 'react'
import { Navigate, type RouteObject } from 'react-router'

import { RequireAuth } from '@/app/guards'
import { PAGES, type PageId } from '@/app/nav'
import {
  HouseholdSettings,
  InvitePage,
  LoginPage,
  SecuritySettings,
  SettingsLayout,
  SetupPage,
  TransactionsPage,
} from '@/app/lazyRoutes'
import { AppShell } from '@/components/layout/AppShell'
import {
  AuthPageSkeleton,
  SectionSkeleton,
  ShellPageSkeleton,
} from '@/components/layout/RouteSkeletons'
import { LegacyInviteRedirect } from '@/pages/auth/LegacyInviteRedirect'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { PlaceholderPage } from '@/pages/PlaceholderPage'

const placeholderIds = (Object.keys(PAGES) as PageId[]).filter(
  (id) => id !== 'overview' && id !== 'settings' && id !== 'transactions',
)

function withSuspense(node: ReactNode, fallback: ReactNode) {
  return <Suspense fallback={fallback}>{node}</Suspense>
}

export const routes: RouteObject[] = [
  // Public: no session needed.
  // Lazy chunks: auth and settings load on demand (src/app/lazyRoutes.ts).
  { path: '/login', element: withSuspense(<LoginPage />, <AuthPageSkeleton />) },
  { path: '/setup', element: withSuspense(<SetupPage />, <AuthPageSkeleton />) },
  // Invite token in the fragment (/invite#<token>); old /invite/:token links are redirected there.
  { path: '/invite', element: withSuspense(<InvitePage />, <AuthPageSkeleton />) },
  { path: '/invite/:token', element: <LegacyInviteRedirect /> },
  // Everything else sits behind the session guard.
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <OverviewPage /> },
          {
            path: 'settings',
            element: withSuspense(<SettingsLayout />, <ShellPageSkeleton />),
            children: [
              { index: true, element: <Navigate to="household" replace /> },
              {
                path: 'household',
                element: withSuspense(<HouseholdSettings />, <SectionSkeleton />),
              },
              {
                path: 'security',
                element: withSuspense(<SecuritySettings />, <SectionSkeleton />),
              },
            ],
          },
          {
            path: 'transactions',
            element: withSuspense(<TransactionsPage />, <ShellPageSkeleton />),
          },
          ...placeholderIds.map((id) => ({
            path: PAGES[id].path.slice(1),
            element: <PlaceholderPage id={id} />,
          })),
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]
