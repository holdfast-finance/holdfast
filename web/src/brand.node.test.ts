import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

const WEB = join(import.meta.dirname, '..')
const SRC = join(WEB, 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

describe('brand guardrails', () => {
  it('ships the same favicon as brand/favicon.svg', () => {
    const published = readFileSync(join(WEB, 'public', 'favicon.svg'), 'utf8')
    const source = readFileSync(join(WEB, '..', 'brand', 'favicon.svg'), 'utf8')
    expect(published).toBe(source)
  })

  it('keeps raw hex colors out of components (tokens only)', () => {
    const offenders = walk(SRC)
      .filter((f) => /\.(tsx?|css)$/.test(f) && !f.endsWith('index.css') && !f.includes('.test.'))
      .filter((f) => /#[0-9a-fA-F]{3,8}\b/.test(readFileSync(f, 'utf8')))
      .map((f) => relative(WEB, f))
    expect(offenders).toEqual([])
  })

  it('never loads fonts or scripts from a third-party CDN', () => {
    const files = [join(WEB, 'index.html'), ...walk(SRC).filter((f) => !f.includes('.test.'))]
    const offenders = files.filter((f) =>
      /fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|unpkg\.com/.test(readFileSync(f, 'utf8')),
    )
    expect(offenders).toEqual([])
  })
})
