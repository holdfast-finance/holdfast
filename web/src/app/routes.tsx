import { Navigate, type RouteObject } from 'react-router'

import { RequireAuth } from '@/app/guards'
import { PAGES, type PageId } from '@/app/nav'
import { AppShell } from '@/components/layout/AppShell'
import { InvitePage } from '@/pages/auth/InvitePage'
import { LoginPage } from '@/pages/auth/LoginPage'
import { SetupPage } from '@/pages/auth/SetupPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { PlaceholderPage } from '@/pages/PlaceholderPage'
import { HouseholdSettings } from '@/pages/settings/HouseholdSettings'
import { SecuritySettings } from '@/pages/settings/SecuritySettings'
import { SettingsLayout } from '@/pages/settings/SettingsLayout'

const placeholderIds = (Object.keys(PAGES) as PageId[]).filter(
  (id) => id !== 'overview' && id !== 'settings',
)

export const routes: RouteObject[] = [
  // Public: no session needed.
  { path: '/login', element: <LoginPage /> },
  { path: '/setup', element: <SetupPage /> },
  { path: '/invite/:token', element: <InvitePage /> },
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
            element: <SettingsLayout />,
            children: [
              { index: true, element: <Navigate to="household" replace /> },
              { path: 'household', element: <HouseholdSettings /> },
              { path: 'security', element: <SecuritySettings /> },
            ],
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
