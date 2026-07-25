import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CollapsedActivityCard } from './CollapsedActivityCard'
import { INLINE_ACTIVITY_ENTRY_LIMIT } from './activityEntryUtils'
import type { ToolEntryDisplay } from './tooling/types'

vi.mock('./ToolClusterTimelineOverlay', () => ({
  ToolClusterTimelineOverlay: ({ open, entries }: { open: boolean; entries: unknown[] }) => (
    open ? <div data-testid="overlay">overlay with {entries.length} entries</div> : null
  ),
}))

// Rendering entry internals is not what these tests are about; the click path is.
vi.mock('./ActivityEntryList', () => ({
  ActivityEntryList: ({ entries, onViewAll }: { entries: unknown[]; onViewAll?: () => void }) => (
    <div data-testid="inline-list">
      inline list with {entries.length} entries
      {onViewAll ? <button type="button" onClick={onViewAll}>View all actions</button> : null}
    </div>
  ),
}))

function makeEntries(count: number): ToolEntryDisplay[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `entry-${index}`,
    cursor: `cursor-${index}`,
    toolName: 'http_request',
    meta: { label: 'HTTP request' },
    summary: `Step ${index}`,
    timestamp: '2026-07-25T13:05:00Z',
    status: 'complete',
  })) as unknown as ToolEntryDisplay[]
}

describe('CollapsedActivityCard', () => {
  it('opens the full view in one click when the run is too long to show inline', () => {
    const entries = makeEntries(INLINE_ACTIVITY_ENTRY_LIMIT + 4)
    render(<CollapsedActivityCard overlayId="overlay-1" entries={entries} />)

    fireEvent.click(screen.getByRole('button', { name: /actions/i }))

    // No truncated intermediate list that would need a second click.
    expect(screen.queryByText('View all actions')).not.toBeInTheDocument()
    expect(screen.getByTestId('overlay')).toHaveTextContent(`overlay with ${entries.length} entries`)
  })

  it('announces that the control opens a dialog when it will not expand inline', () => {
    render(<CollapsedActivityCard overlayId="overlay-2" entries={makeEntries(INLINE_ACTIVITY_ENTRY_LIMIT + 1)} />)

    expect(screen.getByRole('button', { name: /actions/i })).toHaveAttribute('aria-haspopup', 'dialog')
  })

  it('still expands in place when every action fits', () => {
    render(<CollapsedActivityCard overlayId="overlay-3" entries={makeEntries(3)} />)
    const toggle = screen.getByRole('button', { name: /actions/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument()
  })
})
