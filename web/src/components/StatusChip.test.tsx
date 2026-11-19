import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StatusChip, type SyncStatus } from '@/components/StatusChip'

const CASES: [SyncStatus, string, string][] = [
  ['synced', 'Synced', 'signal'],
  ['relink', 'Relink needed', 'flare'],
  ['watch-only', 'Watch-only', 'deepwater'],
  ['error', 'Error', 'breach'],
]

describe('<StatusChip>', () => {
  it.each(CASES)('%s shows a %s label with a %s dot', (status, label, token) => {
    const { container } = render(<StatusChip status={status} />)
    const chip = screen.getByText(label)
    expect(chip).toHaveAttribute('data-status', status)
    expect(chip).toHaveClass(`text-${token}-text`)
    const dot = container.querySelector('[aria-hidden="true"]')
    expect(dot).not.toBeNull()
    expect(dot).toHaveClass(`bg-${token}`, 'rounded-full')
  })

  it('accepts a custom label but keeps the dot', () => {
    const { container } = render(<StatusChip status="synced" label="9 of 10 accounts synced" />)
    expect(screen.getByText('9 of 10 accounts synced')).toBeInTheDocument()
    expect(container.querySelector('.bg-signal')).not.toBeNull()
  })
})
