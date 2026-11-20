import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  BRANDS,
  contrast,
  hexToRgb,
  HOLO_FILL,
  HOLO_TEXT,
  over,
  type BrandTokens,
} from './lib/tokens.ts'

const CSS = readFileSync(join(import.meta.dirname, 'index.css'), 'utf8').toLowerCase()
const THEMES = ['light', 'dark'] as const

function at<T>(rec: Record<string, T>, key: string): T {
  const v = rec[key]
  if (v === undefined) throw new Error(`missing token ${key}`)
  return v
}

/** `color-mix` of `percent` `toward` over an opaque ground, as `#rrggbb`. */
function mixed(toward: string, percent: number, ground: string): `#${string}` {
  const rgb = over(hexToRgb(toward), percent, hexToRgb(ground))
  return `#${rgb.map((channel) => channel.toString(16).padStart(2, '0')).join('')}`
}

/** Holo's block starts at its selector; Signal's tokens live in `:root` above it. */
const HOLO_AT = CSS.indexOf(":root[data-brand='holo'] {")
const HOLO_END = CSS.indexOf('@media (prefers-color-scheme: dark)', HOLO_AT)
const SCOPE: Record<keyof typeof BRANDS, string> = {
  signal: CSS.slice(0, HOLO_AT),
  holo: CSS.slice(HOLO_AT, HOLO_END),
}

describe('Holo theme is scoped', () => {
  it('has a [data-brand=holo] block after the default tokens', () => {
    expect(HOLO_AT).toBeGreaterThan(0)
    expect(HOLO_END).toBeGreaterThan(HOLO_AT)
  })
})

const brandEntries = Object.entries(BRANDS) as [keyof typeof BRANDS, BrandTokens][]

describe.each(brandEntries)('%s: index.css mirrors src/lib/tokens.ts', (brand, t) => {
  const hues = Object.keys(t.fill)
  const cases: [string, { light: string; dark: string }][] = [
    ...hues.map((h): [string, { light: string; dark: string }] => [h, at(t.fill, h)]),
    ...hues.map((h): [string, { light: string; dark: string }] => [`${h}-text`, at(t.text, h)]),
    ...Object.entries(t.grounds),
    ['text', t.foreground],
    ['text-muted', t.muted],
    ...Object.entries(t.inks),
    ['focus-ring', t.focusRing],
    ['input-border', t.inputBorder],
  ]
  it.each(cases)('--%s', (name, pair) => {
    expect(SCOPE[brand]).toContain(`--${name}: light-dark(${pair.light}, ${pair.dark});`)
  })

  it('mirrors --hull-raised', () => {
    const { base, toward, percent } = t.raised
    expect(SCOPE[brand]).toContain(
      `--hull-raised: color-mix(in srgb, var(--${base}), light-dark(${toward.light}, ${toward.dark}) ${String(percent)}%);`,
    )
  })
})

describe.each(brandEntries)('%s: WCAG AA contrast', (_brand, t) => {
  const hues = Object.keys(t.fill)
  const grounds = Object.values(t.grounds)
  const textCases = THEMES.flatMap((theme) =>
    hues.flatMap((hue) => {
      const fg = hexToRgb(at(t.text, hue)[theme])
      const tint = hexToRgb(at(t.fill, hue)[theme])
      return grounds.flatMap((g, i) => {
        const bg = hexToRgb(g[theme])
        return [
          [`${theme} ${hue}-text on ground ${String(i)}`, contrast(fg, bg)] as const,
          [
            `${theme} ${hue}-text in chip on ground ${String(i)}`,
            contrast(fg, over(tint, at(t.chipTint, hue), bg)),
          ] as const,
        ]
      })
    }),
  )

  it.each(textCases)('%s >= 4.5 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5)
  })

  const ringCases = THEMES.flatMap((theme) =>
    grounds.flatMap((g, i) => [
      [
        `${theme} focus ring on ground ${String(i)}`,
        contrast(hexToRgb(t.focusRing[theme]), hexToRgb(g[theme])),
      ] as const,
      [
        `${theme} input border on ground ${String(i)}`,
        contrast(hexToRgb(t.inputBorder[theme]), hexToRgb(g[theme])),
      ] as const,
    ]),
  )

  it.each(ringCases)('%s >= 3.0 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(3)
  })

  const buttonCases = THEMES.flatMap((theme) =>
    t.buttons.map(
      ([hue, ink]) =>
        [
          `${theme} button ink on the ${hue} fill`,
          contrast(hexToRgb(ink[theme]), hexToRgb(at(t.fill, hue)[theme])),
        ] as const,
    ),
  )

  it.each(buttonCases)('%s >= 4.5 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5)
  })

  const base = t.grounds[t.raised.base]
  if (!base) throw new Error(`raised base --${t.raised.base} is not a ground`)
  const raised = {
    light: mixed(t.raised.toward.light, t.raised.percent / 100, base.light),
    dark: mixed(t.raised.toward.dark, t.raised.percent / 100, base.dark),
  }
  const foregroundCases = THEMES.flatMap((theme) =>
    [...grounds, raised].flatMap((g, i) => [
      [
        `${theme} text on ground ${String(i)}`,
        contrast(hexToRgb(t.foreground[theme]), hexToRgb(g[theme])),
      ] as const,
      [
        `${theme} text-muted on ground ${String(i)}`,
        contrast(hexToRgb(t.muted[theme]), hexToRgb(g[theme])),
      ] as const,
    ]),
  )

  it.each(foregroundCases)('%s >= 4.5 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5)
  })
})

describe('Holo exports stay in step', () => {
  it('every Holo fill has a text variant', () => {
    expect(Object.keys(HOLO_TEXT)).toEqual(Object.keys(HOLO_FILL))
  })
})

describe('CSP-safe base styles', () => {
  it('locks body scroll for Radix without its injected <style>', () => {
    expect(CSS).toMatch(
      /body\[data-scroll-locked\]\s*\{[^}]*overflow:\s*hidden\s*!important;[^}]*overscroll-behavior:\s*contain/,
    )
  })

  // Runs after `pnpm build`; skipped on a clean checkout.
  const ASSETS = join(import.meta.dirname, '..', 'dist', 'assets')
  const builtCss = existsSync(ASSETS)
    ? readdirSync(ASSETS)
        .filter((f) => f.endsWith('.css'))
        .map((f) => readFileSync(join(ASSETS, f), 'utf8'))
        .join('\n')
    : ''

  it.skipIf(builtCss === '')('built CSS keeps the scroll-lock rule', () => {
    expect(builtCss).toMatch(/body\[data-scroll-locked\]\{[^}]*overflow:hidden!important/)
  })

  it.skipIf(builtCss === '')('built CSS never inlines fonts as data: URIs (font-src self)', () => {
    expect(builtCss).not.toMatch(/data:(font|application\/(x-)?font)/i)
    expect(builtCss).not.toMatch(/url\(\s*["']?data:[^)]*\)\s*format\(\s*["']?(woff|truetype)/i)
    expect(builtCss).toMatch(/url\([^)]*\.woff2\)/)
  })
})
