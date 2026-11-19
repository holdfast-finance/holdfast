/** Pure brand selection (no DOM), shared by lib/brand.ts and the node tests. */

export type Brand = 'signal' | 'holo'

export const DEFAULT_BRAND: Brand = 'signal'

export function isBrand(value: unknown): value is Brand {
  return value === 'signal' || value === 'holo'
}

/** Household override wins over the build default; anything unrecognised falls back to signal. */
export function resolveBrand(buildBrand?: string, householdBrand?: string | null): Brand {
  if (isBrand(householdBrand)) return householdBrand
  if (isBrand(buildBrand)) return buildBrand
  return DEFAULT_BRAND
}
