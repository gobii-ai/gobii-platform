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

// Default size estimates per event kind (px)
function estimateEventSize(event: TimelineEvent): number {
  switch (event.kind) {
    case 'message':
      return 140
    case 'steps': {
      const entryCount = event.entries?.length ?? 0
      const thinkingCount = event.thinkingEntries?.length ?? 0
      return 80 + entryCount * 32 + thinkingCount * 40
    }
    case 'thinking':
      return 60
    case 'kanban':
      return 200
    default:
      return 100
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
  overscan = 5,
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
