import { useCallback } from 'react'
import { useVirtualizer, type VirtualItem } from '@tanstack/react-virtual'

import type { TimelineEvent } from '../types/agentChat'

// Stable key for a virtual item — matches TimelineEventList's logic
function stableEventKey(event: TimelineEvent): string {
  if (event.kind === 'steps' && event.entries.length > 0) {
    return `cluster:${event.entries[0].id}`
  }
  return event.cursor
}

// Default size estimates per event kind (px).
// Accuracy matters: the closer these are to actual measured heights, the less
// the viewport jumps when items get measured by ResizeObserver.
function estimateEventSize(event: TimelineEvent): number {
  switch (event.kind) {
    case 'message': {
      // chat-bubble: padding 16+16, author row ~28, line-height ~25px/line
      const body = event.message?.bodyText ?? ''
      const lineCount = Math.max(1, Math.ceil(body.length / 60))
      // Short messages (1-2 lines) ~90px, longer messages scale up
      return 62 + lineCount * 25
    }
    case 'steps': {
      const entryCount = event.entries?.length ?? 0
      const thinkingCount = event.thinkingEntries?.length ?? 0
      // Collapsed cluster header ~48px + entries ~36px each + thinking ~32px each
      return 48 + entryCount * 36 + thinkingCount * 32
    }
    case 'thinking':
      return 48
    case 'kanban':
      return 200
    default:
      return 80
  }
}

type UseTimelineVirtualizerOptions = {
  events: TimelineEvent[]
  scrollContainerRef: React.RefObject<HTMLElement | null>
  overscan?: number
}

export function useTimelineVirtualizer({
  events,
  scrollContainerRef,
  overscan = 10,
}: UseTimelineVirtualizerOptions) {
  const estimateSize = useCallback(
    (index: number) => estimateEventSize(events[index]),
    [events],
  )

  const getItemKey = useCallback(
    (index: number) => stableEventKey(events[index]),
    [events],
  )

  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize,
    getItemKey,
    overscan,
    gap: 12, // matches --timeline-row-gap: 0.75rem
  })

  return virtualizer
}

export type { VirtualItem }
