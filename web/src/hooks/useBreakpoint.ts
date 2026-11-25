import { useSyncExternalStore } from 'react'

/**
 * Below this the app uses the mobile shell (PLAN 11.1). 1024 keeps iPhone landscape (up to 932)
 * and iPad portrait on the bottom-nav shell, where the 240px sidebar would starve the page.
 */
export const DESKTOP_QUERY = '(min-width: 1024px)'

/** Room for the sidebar, the accounts aside and a readable list side by side. */
export const WIDE_QUERY = '(min-width: 1200px)'

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window.matchMedia !== 'function') return () => undefined
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => {
        list.removeEventListener('change', onChange)
      }
    },
    // Mobile-first: without matchMedia, assume the small screen.
    () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
    () => false,
  )
}

export function useIsDesktop(): boolean {
  return useMedia(DESKTOP_QUERY)
}

export function useIsWide(): boolean {
  return useMedia(WIDE_QUERY)
}
