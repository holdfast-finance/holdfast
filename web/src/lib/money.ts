import Decimal from 'decimal.js'

/**
 * Money is a decimal STRING end to end ("-42.10"), exactly as the API serializes it.
 * Never convert an amount to `number`: arithmetic goes through decimal.js and formatting
 * hands Intl.NumberFormat a string, which it treats as an exact decimal (no float rounding).
 */
export type MoneyString = string

const AMOUNT_PATTERN = /^[+-]?\d+(\.\d+)?$/

export interface FormattedMoney {
  /** Display text, e.g. "-$0.10". */
  text: string
  /** True for amounts below zero. "-0.00" is zero, not negative. */
  negative: boolean
}

export interface FormatMoneyOptions {
  currency?: string
  locale?: string
  /** Display precision. Rounds half-even at this presentation boundary only. */
  fractionDigits?: number
}

export function parseMoney(amount: MoneyString): Decimal {
  const trimmed = amount.trim()
  if (!AMOUNT_PATTERN.test(trimmed)) {
    throw new TypeError(`Not a decimal amount string: ${JSON.stringify(amount)}`)
  }
  return new Decimal(trimmed)
}

export function formatMoney(
  amount: MoneyString,
  { currency = 'USD', locale = 'en-US', fractionDigits = 2 }: FormatMoneyOptions = {},
): FormattedMoney {
  const rounded = parseMoney(amount).toDecimalPlaces(fractionDigits, Decimal.ROUND_HALF_EVEN)
  const negative = rounded.isNegative() && !rounded.isZero()
  const magnitude = rounded.abs().toFixed(fractionDigits)

  const formatter = new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
  // Intl.NumberFormat.format accepts a decimal string (ES2023) and keeps it exact.
  // The cast only narrows string -> numeric-literal string; the value is validated above.
  const literal = (negative ? `-${magnitude}` : magnitude) as Intl.StringNumericLiteral
  const text = formatter.format(literal)
  return { text, negative }
}
