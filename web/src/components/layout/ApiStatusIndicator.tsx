import { useApiStatus } from '@/api/client'
import { cn } from '@/lib/utils'

const COPY = {
  checking: { label: 'Checking API', dot: 'bg-muted-foreground', text: 'text-muted-foreground' },
  ok: { label: 'API ok', dot: 'bg-success', text: 'text-muted-foreground' },
  degraded: { label: 'API degraded', dot: 'bg-warning', text: 'text-warning-text' },
  unreachable: { label: 'API unreachable', dot: 'bg-destructive', text: 'text-destructive-text' },
} as const

/** Footer indicator driven by GET /api/health. Dot + label, never color alone. */
export function ApiStatusIndicator({ className }: { className?: string }) {
  const { status, version } = useApiStatus()
  const copy = COPY[status]
  return (
    <p
      role="status"
      aria-live="polite"
      data-api-status={status}
      className={cn('flex items-center gap-2 text-xs', copy.text, className)}
    >
      <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', copy.dot)} />
      <span>{copy.label}</span>
      {version ? <span className="money text-muted-foreground">v{version}</span> : null}
    </p>
  )
}
