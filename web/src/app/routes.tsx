import type { RouteObject } from 'react-router'

import { PAGES, type PageId } from '@/app/nav'
import { AppShell } from '@/components/layout/AppShell'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { PlaceholderPage } from '@/pages/PlaceholderPage'

const placeholderIds = (Object.keys(PAGES) as PageId[]).filter((id) => id !== 'overview')

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      ...placeholderIds.map((id) => ({
        path: PAGES[id].path.slice(1),
        element: <PlaceholderPage id={id} />,
      })),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
