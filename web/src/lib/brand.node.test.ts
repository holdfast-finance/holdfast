import { describe, expect, it } from 'vitest'

import { resolveBrand } from './brand-resolve.ts'

describe('resolveBrand', () => {
  it('defaults to signal', () => {
    expect(resolveBrand()).toBe('signal')
    expect(resolveBrand('nonsense')).toBe('signal')
  })

  it('follows the build brand', () => {
    expect(resolveBrand('holo')).toBe('holo')
  })

  it('lets a household override win', () => {
    expect(resolveBrand('holo', 'signal')).toBe('signal')
    expect(resolveBrand('signal', 'holo')).toBe('holo')
    expect(resolveBrand('holo', null)).toBe('holo')
  })
})
