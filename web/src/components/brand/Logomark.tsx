import { cn } from '@/lib/utils'

/**
 * The holdfast mark: one asset node, one stem, three roots. Same geometry as
 * brand/logomark.svg; drawn in currentColor so it takes a token class (text-primary).
 */
export function Logomark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeWidth={3.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('size-6', className)}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <circle cx="24" cy="11" r="5.5" />
      <path d="M24 16.5V31" />
      <path d="M24 31C20 33 15 35 10 38.5" />
      <path d="M24 31C28 33 33 35 38 38.5" />
      <path d="M24 31V40" />
    </svg>
  )
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('font-display text-lg font-semibold tracking-[-0.02em]', className)}>
      holdfast
    </span>
  )
}
