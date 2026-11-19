import { Check, Copy } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'

/** Copy text to the clipboard. Falls back to a visible error if the Clipboard API is blocked. */
export function CopyButton({
  text,
  label = 'Copy',
  variant = 'outline',
}: {
  text: string
  label?: string
  variant?: 'outline' | 'default'
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const shown =
    state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy blocked: select it by hand' : label
  return (
    <>
      <Button
        type="button"
        variant={variant}
        onClick={() => {
          navigator.clipboard.writeText(text).then(
            () => {
              setState('copied')
            },
            () => {
              setState('failed')
            },
          )
        }}
      >
        {state === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        {shown}
      </Button>
      {/* Announced from a sibling: a live region inside the focused button is read unreliably. */}
      <span role="status" className="sr-only">
        {state === 'idle' ? '' : shown}
      </span>
    </>
  )
}
