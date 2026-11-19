/*
 * Holdfast palette as data (docs/brand/BRAND.md, Direction 01).
 *
 * This is the single source of truth for the raw hex values. `src/index.css` mirrors them in
 * `light-dark(<light>, <dark>)` pairs, and `src/tokens.node.test.ts` fails if the two drift or
 * if any text pairing drops below WCAG AA. Components never import hex from here, and never name a
 * brand hue: they use the semantic Tailwind tokens (`bg-primary`, `text-success-text`, ...)
 * that index.css maps onto these values (ADR 0009).
 *
 * Fills vs text: the brand hues (`signal`, `flare`, `breach`, `deepwater`) are for fills, dots,
 * bars, chart strokes and button backgrounds. Text in those hues uses the `*-text` tokens, which
 * are tuned to reach 4.5:1 on ground, surface and the tinted chip backgrounds.
 */

export type Hex = `#${string}`

export interface ThemePair {
  light: Hex
  dark: Hex
}

/** Brand fills. The light Signal fill stays bright so Abyss text on the primary button holds. */
export const FILL = {
  signal: { light: '#0fb894', dark: '#3df5c8' },
  deepwater: { light: '#5b41e0', dark: '#7b61ff' },
  flare: { light: '#d99a3c', dark: '#ffb547' },
  breach: { light: '#d94f68', dark: '#ff5d7a' },
} as const satisfies Record<string, ThemePair>

/** Text in a brand hue. Each must reach 4.5:1 on ground, surface and its own chip tint. */
export const TEXT = {
  signal: { light: '#09745d', dark: '#3df5c8' },
  flare: { light: '#8b5e1b', dark: '#ffb547' },
  breach: { light: '#bf2945', dark: '#ff5d7a' },
  deepwater: { light: '#5b41e0', dark: '#8b74ff' },
} as const satisfies Record<string, ThemePair>

/** Grounds and surfaces. */
export const GROUND = {
  abyss: { light: '#f7f8fb', dark: '#07080c' },
  hull: { light: '#ffffff', dark: '#0e1118' },
} as const satisfies Record<string, ThemePair>

/** Focus ring: non-text UI, needs 3:1 against ground and surface. Dark keeps Signal. */
export const FOCUS_RING: ThemePair = { light: '#0b8a6f', dark: '#3df5c8' }

/** Form control borders: non-text UI, needs 3:1 against ground and surface (WCAG 1.4.11). */
export const INPUT_BORDER: ThemePair = { light: '#7d869a', dark: '#606879' }

/** Ink that sits on a Signal fill (primary button text). */
export const ABYSS_INK: Hex = '#07080c'

/** Ink that sits on a Deepwater fill (`--secondary-foreground`: secondary button/badge text). */
export const DEEPWATER_INK: ThemePair = { light: '#ffffff', dark: '#07080c' }

/** Chip tint opacity per hue (StatusChip `bg-<hue>/<n>`). */
export const CHIP_TINT: Record<keyof typeof FILL, number> = {
  signal: 0.12,
  flare: 0.12,
  breach: 0.12,
  deepwater: 0.15,
}

type Rgb = readonly [number, number, number]

export function hexToRgb(hex: string): Rgb {
  const m = /^#([0-9a-f]{6})$/i.exec(hex)
  if (!m?.[1]) throw new Error(`Expected #rrggbb, got ${hex}`)
  const v = m[1]
  return [
    Number.parseInt(v.slice(0, 2), 16),
    Number.parseInt(v.slice(2, 4), 16),
    Number.parseInt(v.slice(4, 6), 16),
  ]
}

function channel(c: number): number {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
}

/** WCAG 2.x relative luminance. */
export function luminance(rgb: Rgb): number {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])
}

/** WCAG 2.x contrast ratio, 1 to 21. */
export function contrast(a: Rgb, b: Rgb): number {
  const la = luminance(a)
  const lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Composite `fg` at `alpha` over an opaque `bg` (how a `bg-x/12` tint renders). */
export function over(fg: Rgb, alpha: number, bg: Rgb): Rgb {
  const mix = (f: number, b: number) => Math.round(f * alpha + b * (1 - alpha))
  return [mix(fg[0], bg[0]), mix(fg[1], bg[1]), mix(fg[2], bg[2])]
}
