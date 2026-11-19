import { useSyncExternalStore } from 'react'

/** Tailwind's `md` breakpoint. Below it the app uses the mobile shell (PLAN 11.1). */
export const DESKTOP_QUERY = '(min-width: 768px)'

function subscribe(onChange: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => undefined
  const query = window.matchMedia(DESKTOP_QUERY)
  query.addEventListener('change', onChange)
  return () => {
    query.removeEventListener('change', onChange)
  }
}

function getSnapshot(): boolean {
  // Mobile-first: without matchMedia, assume the small screen.
  return typeof window.matchMedia === 'function' && window.matchMedia(DESKTOP_QUERY).matches
}

export function useIsDesktop(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
