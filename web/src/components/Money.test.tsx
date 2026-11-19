import { render, screen } from '@testing-library/react'
import Decimal from 'decimal.js'
import { describe, expect, it, vi } from 'vitest'

import { Money } from '@/components/Money'
import { formatMoney, parseMoney } from '@/lib/money'

describe('formatMoney', () => {
  it('formats a positive amount as USD', () => {
    expect(formatMoney('1234.5')).toEqual({ text: '$1,234.50', negative: false })
  })

  it('treats "-0.10" as negative', () => {
    expect(formatMoney('-0.10')).toEqual({ text: '-$0.10', negative: true })
  })

  it('treats negative zero as zero, not negative', () => {
    expect(formatMoney('-0.00')).toEqual({ text: '$0.00', negative: false })
    expect(formatMoney('-0.001')).toEqual({ text: '$0.00', negative: false })
  })

  it('keeps precision a float would lose', () => {
    // 2^53 + 1 cannot be represented as a double; a Number round-trip would print ...992.
    expect(formatMoney('9007199254740993.01').text).toBe('$9,007,199,254,740,993.01')
    // 0.1 + 0.2 style artefacts never appear.
    expect(formatMoney(new Decimal('0.1').plus('0.2').toString()).text).toBe('$0.30')
  })

  it('rounds half-even at the presentation boundary only', () => {
    expect(formatMoney('0.125').text).toBe('$0.12')
    expect(formatMoney('0.135').text).toBe('$0.14')
    expect(formatMoney('-2.675').text).toBe('-$2.68')
  })

  it('hands Intl.NumberFormat a string, never a number', () => {
    const RealNumberFormat = Intl.NumberFormat
    const seen: unknown[] = []
    vi.spyOn(Intl, 'NumberFormat').mockImplementation(function (
      ...args: ConstructorParameters<typeof Intl.NumberFormat>
    ) {
      const real = new RealNumberFormat(...args)
      return {
        format: (value: unknown) => {
          seen.push(value)
          return real.format(value as Intl.StringNumericLiteral)
        },
      } as Intl.NumberFormat
    })
    expect(formatMoney('-42.10').text).toBe('-$42.10')
    expect(formatMoney('1000000.99').text).toBe('$1,000,000.99')
    expect(seen).toEqual(['-42.10', '1000000.99'])
  })

  it('never coerces through Number or parseFloat', () => {
    const numberSpy = vi.spyOn(globalThis, 'Number')
    const parseFloatSpy = vi.spyOn(globalThis, 'parseFloat')
    const numberParseSpy = vi.spyOn(Number, 'parseFloat')
    formatMoney('-1234.56')
    expect(numberSpy).not.toHaveBeenCalled()
    expect(parseFloatSpy).not.toHaveBeenCalled()
    expect(numberParseSpy).not.toHaveBeenCalled()
  })

  it('rejects anything that is not a plain decimal string', () => {
    for (const bad of ['', 'abc', '1e3', 'NaN', 'Infinity', '1,000.00', '$5']) {
      expect(() => parseMoney(bad)).toThrow(TypeError)
    }
  })

  it('supports other currencies and precisions', () => {
    // de-DE puts a no-break space before the symbol.
    expect(formatMoney('1234.5', { currency: 'EUR', locale: 'de-DE' }).text).toMatch(
      /^1\.234,50\s€$/u,
    )
    expect(formatMoney('0.48210000', { currency: 'USD', fractionDigits: 4 }).text).toBe('$0.4821')
  })
})

describe('<Money>', () => {
  it('renders in the mono money style', () => {
    render(<Money amount="1234.50" />)
    const el = screen.getByText('$1,234.50')
    expect(el).toHaveClass('money')
    expect(el).not.toHaveClass('text-destructive-text')
    expect(el).not.toHaveAttribute('data-negative')
  })

  it('renders "-0.10" as negative in Breach', () => {
    render(<Money amount="-0.10" />)
    const el = screen.getByText('-$0.10')
    expect(el).toHaveClass('money', 'text-destructive-text')
    expect(el).toHaveAttribute('data-negative', 'true')
  })
})
