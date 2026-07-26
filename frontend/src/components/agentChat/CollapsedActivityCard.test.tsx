import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CollapsedActivityCard } from './CollapsedActivityCard'
import type { ToolEntryDisplay } from './tooling/types'

vi.mock('./ToolClusterTimelineOverlay', () => ({
  ToolClusterTimelineOverlay: ({ open, entries }: { open: boolean; entries: unknown[] }) => (
    open ? <div data-testid="overlay">overlay with {entries.length} entries</div> : null
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
  // One control, one behaviour. A reader cannot tell how many actions are behind the label before
  // they click, so the click must not do two different things depending on that count.
  for (const count of [1, 3, 10, 14]) {
    it(`opens the full view in one click for a run of ${count}`, () => {
      const entries = makeEntries(count)
      render(<CollapsedActivityCard overlayId={`overlay-${count}`} entries={entries} />)

      fireEvent.click(screen.getByRole('button', { name: /action/i }))

      expect(screen.getByTestId('overlay')).toHaveTextContent(`overlay with ${count} entries`)
    })
  }

  it('always announces that the control opens a dialog', () => {
    render(<CollapsedActivityCard overlayId="overlay-aria" entries={makeEntries(2)} />)

    const toggle = screen.getByRole('button', { name: /action/i })
    expect(toggle).toHaveAttribute('aria-haspopup', 'dialog')
    // It no longer toggles a region, so it must not claim to.
    expect(toggle).not.toHaveAttribute('aria-expanded')
  })

  it('never grows the timeline in place', () => {
    render(<CollapsedActivityCard overlayId="overlay-inline" entries={makeEntries(3)} />)

    fireEvent.click(screen.getByRole('button', { name: /action/i }))

    // Inline expansion moved every row below the card; the overlay does not.
    expect(screen.queryByTestId('inline-list')).not.toBeInTheDocument()
  })

  it('renders nothing when there are no actions', () => {
    const { container } = render(<CollapsedActivityCard overlayId="overlay-empty" entries={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
