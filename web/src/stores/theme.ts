import { create } from 'zustand'

export type ThemePreference = 'system' | 'dark' | 'light'
export type ResolvedTheme = 'dark' | 'light'

const STORAGE_KEY = 'holdfast.theme'
const LIGHT_QUERY = '(prefers-color-scheme: light)'

function readPreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    if (value === 'dark' || value === 'light' || value === 'system') return value
  } catch {
    // Storage can be blocked (private mode, strict settings). Fall back to the system.
  }
  return 'system'
}

function writePreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    // Not persisted; the choice still applies for this session.
  }
}

function systemPrefersLight(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(LIGHT_QUERY).matches
}

/** Dark is the hero theme: only an explicit or system "light" switches away from it. */
export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return systemPrefersLight() ? 'light' : 'dark'
  return preference
}

function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.dataset.theme = theme
}

interface ThemeState {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  /** Flip between dark and light (stores an explicit choice). */
  toggle: () => void
}

export const useThemeStore = create<ThemeState>()((set, get) => {
  const preference = readPreference()
  return {
    preference,
    resolved: resolveTheme(preference),
    setPreference: (next) => {
      writePreference(next)
      const resolved = resolveTheme(next)
      applyTheme(resolved)
      set({ preference: next, resolved })
    },
    toggle: () => {
      get().setPreference(get().resolved === 'dark' ? 'light' : 'dark')
    },
  }
})

/** Apply the stored theme to <html> and follow system changes while on "system". */
export function initTheme(): () => void {
  applyTheme(useThemeStore.getState().resolved)
  if (typeof window.matchMedia !== 'function') return () => undefined
  const query = window.matchMedia(LIGHT_QUERY)
  const onChange = () => {
    const { preference } = useThemeStore.getState()
    if (preference !== 'system') return
    const resolved = resolveTheme('system')
    applyTheme(resolved)
    useThemeStore.setState({ resolved })
  }
  query.addEventListener('change', onChange)
  return () => {
    query.removeEventListener('change', onChange)
  }
}
