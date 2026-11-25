import { describe, expect, it } from 'vitest'

import { formatMoney, parseMoney } from './money'

describe('money', () => {
  it('parses decimal strings exactly', () => {
    expect(parseMoney('-42.10').toFixed(2)).toBe('-42.10')
    expect(parseMoney('0.1').plus(parseMoney('0.2')).toString()).toBe('0.3')
  })

  it('rejects non-decimal input', () => {
    expect(() => parseMoney('1e3')).toThrow(TypeError)
    expect(() => parseMoney('abc')).toThrow(TypeError)
  })

  it('formats outflow negative and rounds half-even', () => {
    expect(formatMoney('-42.10')).toEqual({ text: '-$42.10', negative: true, positive: false })
    expect(formatMoney('0.125').text).toBe('$0.12')
    expect(formatMoney('0.135').text).toBe('$0.14')
  })

  it('treats negative zero as zero', () => {
    expect(formatMoney('-0.001').negative).toBe(false)
  })

  it('prefixes a plus on positive amounts only when asked', () => {
    expect(formatMoney('3200.00', { showPlus: true }).text).toBe('+$3,200.00')
    expect(formatMoney('3200.00').text).toBe('$3,200.00')
    expect(formatMoney('0.00', { showPlus: true }).text).toBe('$0.00')
    expect(formatMoney('-0.10', { showPlus: true }).text).toBe('-$0.10')
  })
})
