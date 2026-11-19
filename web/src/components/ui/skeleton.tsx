import { cn } from 'cn'

/*
 * `bg-skeleton` / `border-skeleton-edge` come from index.css. In light the old muted fill was
 * about 1.04:1 on white and vanished, so light gets a visible fill and a hairline edge. Dark
 * stays a quiet raised fill with no visible edge.
 */
function Skeleton({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="skeleton"
      className={cn('animate-pulse rounded-md border border-skeleton-edge bg-skeleton', className)}
      {...props}
    />
  )
}

export { Skeleton }
