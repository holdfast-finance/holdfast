import '@fontsource-variable/inter'
import '@fontsource/space-grotesk/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import './index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { Providers } from '@/app/Providers'
import { routes } from '@/app/routes'
import { initBrand } from '@/lib/brand'
import { initTheme } from '@/stores/theme'

initBrand()
initTheme()

async function enableMocking(): Promise<void> {
  // `import.meta.env.DEV` is a build-time `false` in production, so this branch and the msw
  // chunk are dropped from the bundle entirely. VITE_MOCK_API=1 only works under `pnpm dev`.
  if (import.meta.env.DEV && import.meta.env.VITE_MOCK_API === '1') {
    const { startMockApi } = await import('./mocks/browser')
    await startMockApi()
  }
}

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root element')
const root = container

void enableMocking().then(() => {
  const router = createBrowserRouter(routes)
  createRoot(root).render(
    <StrictMode>
      <Providers>
        <RouterProvider router={router} />
      </Providers>
    </StrictMode>,
  )
})
