import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
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

describe('mock API stays out of production', () => {
  it('ships no MSW worker script in public/ (vite build copies public/ verbatim)', () => {
    expect(existsSync(join(WEB, 'public', 'mockServiceWorker.js'))).toBe(false)
  })

  it('imports the browser mock only behind import.meta.env.DEV', () => {
    const main = readFileSync(join(SRC, 'main.tsx'), 'utf8')
    expect(main).toMatch(
      /if \(import\.meta\.env\.DEV && import\.meta\.env\.VITE_MOCK_API === '1'\) \{\s*const \{ startMockApi \} = await import\('\.\/mocks\/browser'\)/,
    )
  })

  it('never imports msw or src/mocks from app code', () => {
    const offenders = walk(SRC)
      .filter((f) => /\.(ts|tsx)$/.test(f))
      .filter((f) => !/\.test\.(ts|tsx)$/.test(f))
      .filter((f) => !f.startsWith(join(SRC, 'mocks')) && !f.startsWith(join(SRC, 'test')))
      .filter((f) => !f.endsWith('main.tsx'))
      .filter((f) => /from ['"](msw|@\/mocks)/.test(readFileSync(f, 'utf8')))
      .map((f) => relative(WEB, f))
    expect(offenders).toEqual([])
  })
})
