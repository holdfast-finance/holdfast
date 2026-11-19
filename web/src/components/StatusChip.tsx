import { cn } from '@/lib/utils'

export type SyncStatus = 'synced' | 'relink' | 'watch-only' | 'error'

const STATUS: Record<SyncStatus, { label: string; tone: string; dot: string }> = {
  synced: { label: 'Synced', tone: 'bg-success/12 text-success-text', dot: 'bg-success' },
  relink: { label: 'Relink needed', tone: 'bg-warning/12 text-warning-text', dot: 'bg-warning' },
  'watch-only': {
    label: 'Watch-only',
    tone: 'bg-secondary/15 text-secondary-text',
    dot: 'bg-secondary',
  },
  error: { label: 'Error', tone: 'bg-destructive/12 text-destructive-text', dot: 'bg-destructive' },
}

export interface StatusChipProps {
  status: SyncStatus
  /** Override the default label, e.g. "9 of 10 accounts synced". Color never stands alone. */
  label?: string
  className?: string
}

/** A dot plus a label on a tinted pill (BRAND.md "Status chips"). */
export function StatusChip({ status, label, className }: StatusChipProps) {
  const style = STATUS[status]
  return (
    <span
      data-status={status}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap',
        style.tone,
        className,
      )}
    >
      <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', style.dot)} />
      {label ?? style.label}
    </span>
  )
}
