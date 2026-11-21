import * as React from 'react'

import { cn } from '@/lib/utils'

// shadcn/ui input, Holdfast sizing: 44px tall below md (touch targets), ring-token focus. Borders use `--input` (>= 3:1, WCAG 1.4.11).
function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'h-11 w-full min-w-0 rounded-lg border border-input bg-transparent px-3 py-1 text-base text-foreground transition-colors outline-none placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:pointer-fine:h-10 md:pointer-fine:text-sm',
        'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        'aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20',
        className,
      )}
      {...props}
    />
  )
}

function NativeSelect({ className, ...props }: React.ComponentProps<'select'>) {
  return (
    <select
      data-slot="select"
      className={cn(
        'h-11 w-full min-w-0 rounded-lg border border-input bg-card px-3 text-base text-foreground outline-none md:pointer-fine:h-10 md:pointer-fine:text-sm',
        'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        'aria-invalid:border-destructive',
        className,
      )}
      {...props}
    />
  )
}

export { Input, NativeSelect }
