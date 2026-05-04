import type { ThinkingEvent, TimelineEvent, ToolClusterEvent } from '../../types/agentChat'
import type { ToolEntryDisplay } from './tooling/types'
import { transformToolCluster } from './tooling/toolRegistry'

export const INLINE_ACTIVITY_ENTRY_LIMIT = 10

export function buildActionCountLabel(count: number): string {
  return `${count} action${count === 1 ? '' : 's'}`
}

export function buildThinkingCluster(event: ThinkingEvent): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor: event.cursor,
    entries: [],
    entryCount: 1,
    collapsible: false,
    collapseThreshold: Infinity,
    thinkingEntries: [event],
    earliestTimestamp: event.timestamp ?? null,
    latestTimestamp: event.timestamp ?? null,
  }
}

export function flattenTimelineEventsToEntries(
  events: TimelineEvent[],
  options?: { suppressedThinkingCursor?: string | null },
): ToolEntryDisplay[] {
  const allEntries: ToolEntryDisplay[] = []

  for (const event of events) {
    let cluster: ToolClusterEvent | null = null

    if (event.kind === 'steps') {
      cluster = event
    } else if (event.kind === 'thinking') {
      cluster = buildThinkingCluster(event)
    }

    if (!cluster) {
      continue
    }

    const transformed = transformToolCluster(cluster, options)
    allEntries.push(...transformed.entries)
  }

  return allEntries
}
