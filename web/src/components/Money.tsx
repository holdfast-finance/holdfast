import { cn } from '@/lib/utils'
import { formatMoney, type FormatMoneyOptions, type MoneyString } from '@/lib/money'

export interface MoneyProps extends FormatMoneyOptions {
  /** Decimal amount as a string, exactly as the API sends it. Never a number. */
  amount: MoneyString
  className?: string
}

/** Renders an amount in the theme mono with tabular figures. Negative amounts use destructive. */
export function Money({ amount, className, ...options }: MoneyProps) {
  const { text, negative } = formatMoney(amount, options)
  return (
    <span
      className={cn('money', negative && 'text-destructive-text', className)}
      data-negative={negative ? 'true' : undefined}
    >
      {text}
    </span>
  )
}
