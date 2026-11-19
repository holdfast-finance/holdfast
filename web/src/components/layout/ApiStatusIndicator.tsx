import { useApiStatus } from '@/api/client'
import { cn } from '@/lib/utils'

const COPY = {
  checking: { label: 'Checking API', dot: 'bg-text-muted', text: 'text-text-muted' },
  ok: { label: 'API ok', dot: 'bg-signal', text: 'text-text-muted' },
  degraded: { label: 'API degraded', dot: 'bg-flare', text: 'text-flare-text' },
  unreachable: { label: 'API unreachable', dot: 'bg-breach', text: 'text-breach-text' },
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
      {version ? <span className="money text-text-muted">v{version}</span> : null}
    </p>
  )
}
