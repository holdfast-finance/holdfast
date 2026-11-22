import { lazy } from 'react'

/*
 * Route-level code splitting. The shell, Overview and placeholders stay in the entry chunk
 * (first paint after sign-in); auth and settings screens, with their form, WebAuthn and QR
 * code, load on demand. Fallbacks live in components/layout/RouteSkeletons.tsx.
 */

export const LoginPage = lazy(() =>
  import('@/pages/auth/LoginPage').then((m) => ({ default: m.LoginPage })),
)
export const SetupPage = lazy(() =>
  import('@/pages/auth/SetupPage').then((m) => ({ default: m.SetupPage })),
)
export const InvitePage = lazy(() =>
  import('@/pages/auth/InvitePage').then((m) => ({ default: m.InvitePage })),
)
export const TransactionsPage = lazy(() =>
  import('@/pages/transactions/TransactionsPage').then((m) => ({ default: m.TransactionsPage })),
)
export const ImportPage = lazy(() =>
  import('@/pages/import/ImportPage').then((m) => ({ default: m.ImportPage })),
)
export const SettingsLayout = lazy(() =>
  import('@/pages/settings/SettingsLayout').then((m) => ({ default: m.SettingsLayout })),
)
export const HouseholdSettings = lazy(() =>
  import('@/pages/settings/HouseholdSettings').then((m) => ({ default: m.HouseholdSettings })),
)
export const SecuritySettings = lazy(() =>
  import('@/pages/settings/SecuritySettings').then((m) => ({ default: m.SecuritySettings })),
)
/** Step-up prompt: zod + react-hook-form stay out of the entry chunk until it first opens. */
export const ReauthDialog = lazy(() =>
  import('@/components/ReauthDialog').then((m) => ({ default: m.ReauthDialog })),
)
