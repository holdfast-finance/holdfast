import { reasonLines } from './format'

/** Why a statement was flagged. `undefined`/null means the server did not record a reason. */
export function ReasonList({ reason }: { reason: string | null | undefined }) {
  const lines = reasonLines(reason)
  if (lines.length === 0) {
    return <p className="text-sm text-muted-foreground">Reason not recorded.</p>
  }
  return (
    <ul className="flex flex-col gap-1 text-sm text-warning-text">
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  )
}
