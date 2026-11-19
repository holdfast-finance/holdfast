/*
 * Brand theme plumbing (ADR 0009). `<html data-brand>` is "signal" (Direction 01, default) or
 * "holo" (Holdfast Cloud). The build picks the default with VITE_BRAND; a household setting can
 * override it later through `setHouseholdBrand` (stub: no backend yet). Holo fonts are
 * dynamic imports, so Signal users never download them.
 */

import { type Brand, resolveBrand } from './brand-resolve.ts'

export type { Brand }
export { DEFAULT_BRAND, isBrand, resolveBrand } from './brand-resolve.ts'

let holoFonts: Promise<unknown> | undefined

function loadHoloFonts(): Promise<unknown> {
  holoFonts ??= Promise.all([
    import('@fontsource/sora/700.css'),
    import('@fontsource/manrope/400.css'),
    import('@fontsource/manrope/500.css'),
    import('@fontsource/manrope/600.css'),
    import('@fontsource/dm-mono/400.css'),
    import('@fontsource/dm-mono/500.css'),
  ])
  return holoFonts
}

export function applyBrand(brand: Brand): void {
  document.documentElement.dataset.brand = brand
  if (brand === 'holo') void loadHoloFonts()
}

/**
 * Household-setting override hook. Stubbed: the settings API does not carry a brand yet, so this
 * only re-applies. Wire it to the household settings query when Settings -> Appearance lands.
 */
export function setHouseholdBrand(householdBrand: Brand | null): Brand {
  const brand = resolveBrand(import.meta.env.VITE_BRAND, householdBrand)
  applyBrand(brand)
  return brand
}

export function initBrand(): Brand {
  return setHouseholdBrand(null)
}
