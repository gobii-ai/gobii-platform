import type { KanbanEvent, ThinkingEvent, TimelineEvent, ToolCallEntry, ToolClusterEvent } from '../../types/agentChat'
import { compareTimelineCursors } from '../../util/timelineCursor'

function compareToolEntries(left: ToolCallEntry, right: ToolCallEntry): number {
  if (left.cursor && right.cursor) {
    return compareTimelineCursors(left.cursor, right.cursor)
  }
  if (left.timestamp && right.timestamp) {
    return left.timestamp.localeCompare(right.timestamp)
  }
  return left.id.localeCompare(right.id)
}

function dedupeAndSortToolEntries(entries: ToolCallEntry[]): ToolCallEntry[] {
  const byId = new Map<string, ToolCallEntry>()
  for (const entry of entries) {
    byId.set(entry.id, entry)
  }
  return Array.from(byId.values()).sort(compareToolEntries)
}

function pickSegmentBounds(segment: TimelineEvent[]): { cursor: string; earliestTimestamp: string | null; latestTimestamp: string | null } {
  let cursor = segment[0].cursor
  let earliestTimestamp: string | null = null
  let latestTimestamp: string | null = null

  const record = (value: string | null | undefined) => {
    if (!value) {
      return
    }
    if (!earliestTimestamp || value < earliestTimestamp) {
      earliestTimestamp = value
    }
    if (!latestTimestamp || value > latestTimestamp) {
      latestTimestamp = value
    }
  }

  for (const event of segment) {
    if (compareTimelineCursors(event.cursor, cursor) < 0) {
      cursor = event.cursor
    }
    if (event.kind === 'steps') {
      record(event.earliestTimestamp)
      record(event.latestTimestamp)
      for (const entry of event.entries) {
        record(entry.timestamp)
      }
      for (const thinking of event.thinkingEntries ?? []) {
        record(thinking.timestamp)
      }
      for (const kanban of event.kanbanEntries ?? []) {
        record(kanban.timestamp)
      }
      continue
    }
    record(event.timestamp)
  }

  return { cursor, earliestTimestamp, latestTimestamp }
}

export function collapseTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  const collapsed: TimelineEvent[] = []
  let segment: TimelineEvent[] = []

  const flush = () => {
    if (!segment.length) {
      return
    }

    if (segment.length === 1 && segment[0].kind === 'steps') {
      collapsed.push({ ...segment[0], collapsible: true, collapseThreshold: 1 })
      segment = []
      return
    }

    const toolEntries: ToolCallEntry[] = []
    const thinkingEntries: ThinkingEvent[] = []
    const kanbanEntries: KanbanEvent[] = []

    for (const event of segment) {
      if (event.kind === 'steps') {
        toolEntries.push(...event.entries)
        thinkingEntries.push(...(event.thinkingEntries ?? []))
        kanbanEntries.push(...(event.kanbanEntries ?? []))
      } else if (event.kind === 'thinking') {
        thinkingEntries.push(event)
      } else if (event.kind === 'kanban') {
        kanbanEntries.push(event)
      }
    }

    const { cursor, earliestTimestamp, latestTimestamp } = pickSegmentBounds(segment)
    const mergedCluster: ToolClusterEvent = {
      kind: 'steps',
      cursor,
      entries: dedupeAndSortToolEntries(toolEntries),
      entryCount: toolEntries.length + thinkingEntries.length + kanbanEntries.length,
      collapsible: true,
      collapseThreshold: 1,
      earliestTimestamp,
      latestTimestamp,
      thinkingEntries: thinkingEntries.length ? thinkingEntries : undefined,
      kanbanEntries: kanbanEntries.length ? kanbanEntries : undefined,
    }

    collapsed.push(mergedCluster)
    segment = []
  }

  for (const event of events) {
    if (event.kind === 'message') {
      flush()
      collapsed.push(event)
      continue
    }
    segment.push(event)
  }

  flush()
  return collapsed
}
