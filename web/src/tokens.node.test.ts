import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  ABYSS_INK,
  CHIP_TINT,
  contrast,
  FILL,
  FOCUS_RING,
  GROUND,
  hexToRgb,
  over,
  TEXT,
  type ThemePair,
} from './lib/tokens.ts'

const CSS = readFileSync(join(import.meta.dirname, 'index.css'), 'utf8').toLowerCase()
const THEMES = ['light', 'dark'] as const
const HUES = Object.keys(TEXT) as (keyof typeof TEXT)[]

function declares(name: string, pair: ThemePair): boolean {
  return CSS.includes(`--${name}: light-dark(${pair.light}, ${pair.dark});`)
}

describe('index.css mirrors src/lib/tokens.ts', () => {
  it.each([
    ...HUES.map((h) => [h, FILL[h]] as const),
    ...HUES.map((h) => [`${h}-text`, TEXT[h]] as const),
    ['abyss', GROUND.abyss] as const,
    ['hull', GROUND.hull] as const,
    ['focus-ring', FOCUS_RING] as const,
  ])('--%s', (name, pair) => {
    expect(declares(name, pair)).toBe(true)
  })
})

describe('WCAG AA contrast', () => {
  const textCases = THEMES.flatMap((theme) =>
    HUES.flatMap((hue) => {
      const fg = hexToRgb(TEXT[hue][theme])
      const tint = hexToRgb(FILL[hue][theme])
      return (['abyss', 'hull'] as const).flatMap((g) => {
        const bg = hexToRgb(GROUND[g][theme])
        return [
          [`${theme} ${hue}-text on ${g}`, contrast(fg, bg)] as const,
          [
            `${theme} ${hue}-text in chip on ${g}`,
            contrast(fg, over(tint, CHIP_TINT[hue], bg)),
          ] as const,
        ]
      })
    }),
  )

  it.each(textCases)('%s >= 4.5 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5)
  })

  const ringCases = THEMES.flatMap((theme) =>
    (['abyss', 'hull'] as const).map(
      (g) =>
        [
          `${theme} focus ring on ${g}`,
          contrast(hexToRgb(FOCUS_RING[theme]), hexToRgb(GROUND[g][theme])),
        ] as const,
    ),
  )

  it.each(ringCases)('%s >= 3.0 (got %f)', (_label, ratio) => {
    expect(ratio).toBeGreaterThanOrEqual(3)
  })

  it.each(THEMES)('%s primary button: Abyss ink on the Signal fill >= 4.5', (theme) => {
    expect(contrast(hexToRgb(ABYSS_INK), hexToRgb(FILL.signal[theme]))).toBeGreaterThanOrEqual(4.5)
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
