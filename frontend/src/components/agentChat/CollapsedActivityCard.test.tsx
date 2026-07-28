import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CollapsedActivityCard } from './CollapsedActivityCard'
import type { ToolEntryDisplay } from './tooling/types'
import { chatActions } from '../../store/chatSlice'
import { createTestAppStore, StoreProvider } from '../../test/storeTestUtils'

vi.mock('./ToolClusterTimelineOverlay', () => ({
  ToolClusterTimelineOverlay: ({ open, entries, onClose }: { open: boolean; entries: unknown[]; onClose: () => void }) => (
    open ? (
      <div data-testid="overlay">
        overlay with {entries.length} entries
        <button type="button" onClick={onClose}>close overlay</button>
      </div>
    ) : null
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

function makeStore() {
  const store = createTestAppStore()
  store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
  return store
}

function renderCard(
  store: ReturnType<typeof makeStore>,
  props: { overlayId: string; entries: ToolEntryDisplay[] },
) {
  return render(
    <StoreProvider store={store}>
      <CollapsedActivityCard {...props} />
    </StoreProvider>,
  )
}

describe('CollapsedActivityCard', () => {
  // One control, one behaviour. A reader cannot tell how many actions are behind the label before
  // they click, so the click must not do two different things depending on that count.
  for (const count of [1, 3, 10, 14]) {
    it(`opens the full view in one click for a run of ${count}`, () => {
      const entries = makeEntries(count)
      renderCard(makeStore(), { overlayId: `overlay-${count}`, entries })

      fireEvent.click(screen.getByRole('button', { name: /action/i }))

      expect(screen.getByTestId('overlay')).toHaveTextContent(`overlay with ${count} entries`)
    })
  }

  it('always announces that the control opens a dialog', () => {
    renderCard(makeStore(), { overlayId: 'overlay-aria', entries: makeEntries(2) })

    const toggle = screen.getByRole('button', { name: /action/i })
    expect(toggle).toHaveAttribute('aria-haspopup', 'dialog')
    // It no longer toggles a region, so it must not claim to.
    expect(toggle).not.toHaveAttribute('aria-expanded')
  })

  it('never grows the timeline in place', () => {
    renderCard(makeStore(), { overlayId: 'overlay-inline', entries: makeEntries(3) })

    fireEvent.click(screen.getByRole('button', { name: /action/i }))

    // Inline expansion moved every row below the card; the overlay does not.
    expect(screen.queryByTestId('inline-list')).not.toBeInTheDocument()
  })

  it('renders nothing when there are no actions', () => {
    const { container } = renderCard(makeStore(), { overlayId: 'overlay-empty', entries: [] })

    expect(container).toBeEmptyDOMElement()
  })

  // #306: the panel closed whenever a new message arrived, because the open flag was
  // component state and the card is unmounted/remounted as its run collapses, expands,
  // and coalesces. Held in the store under a stable overlay id, it must survive a full
  // unmount/remount.
  describe('open state survives the card being replaced', () => {
    it('keeps the panel open across an unmount/remount', () => {
      const store = makeStore()
      const entries = makeEntries(3)
      const first = renderCard(store, { overlayId: 'run-1', entries })

      fireEvent.click(screen.getByRole('button', { name: /action/i }))
      expect(screen.getByTestId('overlay')).toBeInTheDocument()

      first.unmount()
      renderCard(store, { overlayId: 'run-1', entries })

      expect(screen.getByTestId('overlay')).toBeInTheDocument()
    })

    it('stays closed after closing, including across remounts', () => {
      const store = makeStore()
      const entries = makeEntries(3)
      const first = renderCard(store, { overlayId: 'run-1', entries })

      fireEvent.click(screen.getByRole('button', { name: /action/i }))
      fireEvent.click(screen.getByText('close overlay'))
      expect(screen.queryByTestId('overlay')).not.toBeInTheDocument()

      first.unmount()
      renderCard(store, { overlayId: 'run-1', entries })
      expect(screen.queryByTestId('overlay')).not.toBeInTheDocument()
    })

    it('does not open for a different overlay id', () => {
      const store = makeStore()
      store.dispatch(chatActions.activityOverlayOpened({ overlayId: 'some-other-run' }))
      renderCard(store, { overlayId: 'run-1', entries: makeEntries(2) })

      expect(screen.queryByTestId('overlay')).not.toBeInTheDocument()
    })
  })
})
