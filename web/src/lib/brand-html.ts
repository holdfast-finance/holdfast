/** Normalize the first-paint brand for the HTML transform. */
export function defaultBrandEnv(current: string | undefined): 'signal' | 'holo' {
  const brand = current?.trim().toLowerCase()
  if (brand === 'signal' || brand === 'holo') return brand
  if (brand) console.warn(`VITE_BRAND="${current}" is not signal or holo; using signal`)
  return 'signal'
}
