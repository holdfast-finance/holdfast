/**
 * First-paint brand on `<html>`. Vite substitutes `%VITE_BRAND%` only when the
 * variable is in its env map. Explicit signal and holo stay; a missing or empty
 * attribute is signal (Direction 01), never holo.
 */
export function rewriteDefaultBrand(html: string): string {
  return html.replace(/data-brand="(?:%VITE_BRAND%|)"/, 'data-brand="signal"')
}

/**
 * Value to put in Vite's env map. `holo` stays holo. An empty string stays empty
 * so the HTML post hook can rewrite `data-brand=""`. Anything else, including
 * unset, is signal.
 */
export function defaultBrandEnv(current: string | undefined): 'signal' | 'holo' | '' {
  if (current === 'holo' || current === '') return current
  return 'signal'
}
