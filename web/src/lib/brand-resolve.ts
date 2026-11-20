/** Pure brand selection (no DOM), shared by lib/brand.ts and the node tests. */

export type Brand = 'signal' | 'holo'

export const DEFAULT_BRAND: Brand = 'signal'

export function isBrand(value: unknown): value is Brand {
  return value === 'signal' || value === 'holo'
}

/**
 * Household override wins over the build default; anything unrecognised falls back to signal.
 * The build value is trimmed and lowercased like the HTML transform (defaultBrandEnv), so
 * `VITE_BRAND="Holo"` can't paint holo and then flip to signal at runtime.
 */
export function resolveBrand(buildBrand?: string, householdBrand?: string | null): Brand {
  if (isBrand(householdBrand)) return householdBrand
  const build = buildBrand?.trim().toLowerCase()
  if (isBrand(build)) return build
  return DEFAULT_BRAND
}
