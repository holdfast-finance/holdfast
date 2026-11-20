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

/*
 * Holo (Holdfast Cloud, ADR 0009). Same rules: hues are fills, colored text uses `*-text`.
 * `src/index.css` mirrors these under `[data-brand='holo']`. Light is the hero theme.
 */
export const HOLO_FILL = {
  indigo: { light: '#4f46e5', dark: '#5b52ee' },
  grow: { light: '#059669', dark: '#34d399' },
  'heads-up': { light: '#d97706', dark: '#fbbf24' },
  alert: { light: '#dc2626', dark: '#f87171' },
  lavender: { light: '#a78bfa', dark: '#a78bfa' },
} as const satisfies Record<string, ThemePair>

export const HOLO_TEXT = {
  indigo: { light: '#4338ca', dark: '#a5b4fc' },
  grow: { light: '#065f46', dark: '#34d399' },
  'heads-up': { light: '#92400e', dark: '#fbbf24' },
  alert: { light: '#b91c1c', dark: '#f87171' },
  lavender: { light: '#6d28d9', dark: '#c4b5fd' },
} as const satisfies Record<string, ThemePair>

/** Holo ground (page) and surface (card). */
export const HOLO_GROUND = {
  cloud: { light: '#f7f7fb', dark: '#12131a' },
  surface: { light: '#ffffff', dark: '#1b1c25' },
} as const satisfies Record<string, ThemePair>

export const HOLO_FOCUS_RING: ThemePair = { light: '#4f46e5', dark: '#a5b4fc' }
export const HOLO_INPUT_BORDER: ThemePair = { light: '#7b8094', dark: '#7a8099' }
/** White on the Indigo fill (primary pill) in light, white on the brighter dark fill. */
export const HOLO_INDIGO_INK: ThemePair = { light: '#ffffff', dark: '#ffffff' }
/** Ink on the Lavender fill (secondary). */
export const HOLO_LAVENDER_INK: ThemePair = { light: '#12131a', dark: '#12131a' }
export const HOLO_CHIP_TINT: Record<keyof typeof HOLO_FILL, number> = {
  indigo: 0.12,
  grow: 0.12,
  'heads-up': 0.12,
  alert: 0.12,
  lavender: 0.15,
}

/** Everything the AA test needs about one brand. */
export interface BrandTokens {
  fill: Record<string, ThemePair>
  text: Record<string, ThemePair>
  chipTint: Record<string, number>
  /** Page ground and card surface, by CSS variable name. */
  grounds: Record<string, ThemePair>
  /**
   * Hover and popover surface (`--hull-raised`). CSS keeps the color-mix; the AA test
   * resolves `percent` of `toward` over the `base` ground.
   */
  raised: { base: string; toward: ThemePair; percent: number }
  /** Body text (`--text`). */
  foreground: ThemePair
  /** Labels and captions (`--text-muted`). Must reach 4.5:1 on grounds and raised. */
  muted: ThemePair
  /** Ink on a fill, by CSS variable name (`deepwater-ink`, `indigo-ink`, `lavender-ink`). */
  inks: Record<string, ThemePair>
  focusRing: ThemePair
  inputBorder: ThemePair
  /** Button text on the primary and secondary fills: [hue, ink]. */
  buttons: readonly (readonly [string, ThemePair])[]
}

export const BRANDS = {
  signal: {
    fill: FILL,
    text: TEXT,
    chipTint: CHIP_TINT,
    grounds: GROUND,
    raised: { base: 'hull', toward: { light: '#0e1118', dark: '#ffffff' }, percent: 4 },
    foreground: { light: '#0e1118', dark: '#e8ecf4' },
    muted: { light: '#566074', dark: '#8a93a6' },
    inks: { 'deepwater-ink': DEEPWATER_INK },
    focusRing: FOCUS_RING,
    inputBorder: INPUT_BORDER,
    buttons: [
      ['signal', { light: ABYSS_INK, dark: ABYSS_INK }],
      ['deepwater', DEEPWATER_INK],
    ],
  },
  holo: {
    fill: HOLO_FILL,
    text: HOLO_TEXT,
    chipTint: HOLO_CHIP_TINT,
    grounds: HOLO_GROUND,
    raised: { base: 'surface', toward: { light: '#12131a', dark: '#ffffff' }, percent: 4 },
    foreground: { light: '#12131a', dark: '#eceef5' },
    muted: { light: '#575c70', dark: '#9aa0b4' },
    inks: { 'indigo-ink': HOLO_INDIGO_INK, 'lavender-ink': HOLO_LAVENDER_INK },
    focusRing: HOLO_FOCUS_RING,
    inputBorder: HOLO_INPUT_BORDER,
    buttons: [
      ['indigo', HOLO_INDIGO_INK],
      ['lavender', HOLO_LAVENDER_INK],
    ],
  },
} as const satisfies Record<string, BrandTokens>

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
