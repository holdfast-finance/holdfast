import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative, sep } from 'node:path'

import { build } from 'vite'
import { describe, expect, it, vi } from 'vitest'

import { defaultBrandEnv } from './lib/brand-html.ts'

const WEB = join(import.meta.dirname, '..')
const SRC = join(WEB, 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

/*
 * ADR 0009: components use semantic tokens only (`bg-primary`, `text-success-text`, ...), so a
 * brand theme (Direction 01 or Holo) is a token swap. Brand hue names live only in index.css and
 * lib/tokens.ts. The match targets utility prefixes and CSS variables, so `AbortSignal` passes.
 */
const HUES = 'signal|deepwater|abyss|hull|flare|breach|indigo|lavender|cloud|grow|heads-up|alert'
const UTILITY =
  'bg|text|border(?:-[trblxyse])?|fill|stroke|ring(?:-offset)?|outline|from|via|to|shadow|decoration|divide|accent|caret|placeholder'
const BRAND_HUE = new RegExp(
  `(?:\\b(?:${UTILITY})-(?:${HUES})\\b)|(?:--(?:color-)?(?:${HUES})\\b)|(?:\\b(?:${HUES})-(?:text|ink|raised)\\b)`,
)
const RAW_COLOR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab)\(/

function componentSources(): string[] {
  return walk(SRC).filter(
    (f) =>
      /\.tsx?$/.test(f) &&
      !f.endsWith(join('lib', 'tokens.ts')) &&
      !f.includes('.test.') &&
      !f.includes(join('src', 'test') + sep),
  )
}

function offendingLines(pattern: RegExp): string[] {
  return componentSources().flatMap((f) =>
    readFileSync(f, 'utf8')
      .split('\n')
      .flatMap((line, i) =>
        pattern.test(line) ? [`${relative(WEB, f)}:${String(i + 1)}: ${line.trim()}`] : [],
      ),
  )
}

describe('token purity (ADR 0009)', () => {
  it.each([
    'bg-signal',
    'hover:bg-hull-raised',
    'text-breach-text',
    'border-t-hairline bg-abyss/90',
    'fill-deepwater/20',
    'stroke-flare',
    "style={{ color: 'var(--signal)' }}",
  ])('the guard catches %s', (sample) => {
    expect(BRAND_HUE.test(sample)).toBe(true)
  })

  it.each([
    'signal?: AbortSignal',
    'queryFn: ({ signal }) => f(signal)',
    'bg-primary text-primary-text',
  ])('the guard ignores %s', (sample) => {
    expect(BRAND_HUE.test(sample)).toBe(false)
  })

  it('no component names a brand hue', () => {
    expect(offendingLines(BRAND_HUE)).toEqual([])
  })

  it('no component uses a raw color literal', () => {
    expect(offendingLines(RAW_COLOR)).toEqual([])
  })
})

describe('brand guardrails', () => {
  it('ships the same favicon as brand/favicon.svg', () => {
    const published = readFileSync(join(WEB, 'public', 'favicon.svg'), 'utf8')
    const source = readFileSync(join(WEB, '..', 'brand', 'favicon.svg'), 'utf8')
    expect(published).toBe(source)
  })

  it('keeps raw hex colors out of components (tokens only)', () => {
    const offenders = walk(SRC)
      .filter(
        (f) =>
          /\.(tsx?|css)$/.test(f) &&
          !f.endsWith('index.css') &&
          !f.endsWith('lib/tokens.ts') &&
          !f.includes('.test.'),
      )
      .filter((f) => /#[0-9a-fA-F]{3,8}\b/.test(readFileSync(f, 'utf8')))
      .map((f) => relative(WEB, f))
    expect(offenders).toEqual([])
  })

  it.each([
    [undefined, 'signal'],
    ['holo', 'holo'],
    ['Holo', 'holo'],
    [' holo ', 'holo'],
  ])('renders VITE_BRAND=%s as data-brand="%s"', async (input, expected) => {
    const envDir = mkdtempSync(join(tmpdir(), 'holdfast-brand-'))
    vi.stubEnv('VITE_BRAND', input)
    try {
      const result = await build({
        root: WEB,
        envDir,
        logLevel: 'silent',
        build: { write: false },
      })
      if (!('output' in result)) throw new Error('Expected a single Vite build output')
      const html = result.output.find((file) => file.fileName === 'index.html')
      expect(html?.type === 'asset' ? html.source : undefined).toMatch(
        new RegExp(`<html[^>]* data-brand="${expected}"`),
      )
      expect(process.env.VITE_BRAND).toBe(input)
    } finally {
      vi.unstubAllEnvs()
      rmSync(envDir, { recursive: true, force: true })
    }
  })

  it('defaults blank values to signal silently and warns for an invalid brand', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(defaultBrandEnv(undefined)).toBe('signal')
    expect(defaultBrandEnv('')).toBe('signal')
    expect(defaultBrandEnv('  ')).toBe('signal')
    expect(defaultBrandEnv(' SIGNAL ')).toBe('signal')
    expect(warn).not.toHaveBeenCalled()
    expect(defaultBrandEnv('other')).toBe('signal')
    expect(warn).toHaveBeenCalledExactlyOnceWith(
      'VITE_BRAND="other" is not signal or holo; using signal',
    )
  })

  it('never loads fonts or scripts from a third-party CDN', () => {
    const files = [join(WEB, 'index.html'), ...walk(SRC).filter((f) => !f.includes('.test.'))]
    const offenders = files.filter((f) =>
      /fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|unpkg\.com/.test(readFileSync(f, 'utf8')),
    )
    expect(offenders).toEqual([])
  })
})
