import { useId, type ReactElement, type ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'

import { errorMessage } from '@/api/client'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

export interface FieldControlProps {
  id: string
  'aria-invalid': boolean
  'aria-describedby': string | undefined
}

/**
 * Label + control + hint + error, wired for screen readers: the control gets `id`,
 * `aria-invalid` and `aria-describedby` pointing at the hint and the error.
 */
export function Field({
  label,
  hint,
  error,
  className,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string
  className?: string
  children: (props: FieldControlProps) => ReactElement
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({ id, 'aria-invalid': Boolean(error), 'aria-describedby': describedBy })}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-destructive-text">
          {error}
        </p>
      )}
    </div>
  )
}

/** Form-level error banner (API errors). Announced politely; the colour is paired with an icon. */
export function FormError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
    >
      <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{errorMessage(error)}</span>
    </div>
  )
}
