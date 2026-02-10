import type { TimelineEvent, MessageEvent } from './types'

export type TraceGroup = {
  anchor: MessageEvent | null
  anchorCursor: string | null
  traceEvents: TimelineEvent[]
}

export type TraceGroupingResult = {
  groups: TraceGroup[]
  countsByAnchorCursor: Record<string, number>
}

function ensureGroup(
  groupsByCursor: Map<string, TraceGroup>,
  cursor: string,
  anchor: MessageEvent,
): TraceGroup {
  const existing = groupsByCursor.get(cursor)
  if (existing) {
    // Keep the first anchor event we see for a cursor.
    return existing
  }
  const group: TraceGroup = { anchor, anchorCursor: cursor, traceEvents: [] }
  groupsByCursor.set(cursor, group)
  return group
}

export function groupTraceByMessage(allEvents: TimelineEvent[]): TraceGroupingResult {
  const groupsByCursor = new Map<string, TraceGroup>()
  const order: string[] = []
  let currentAnchor: MessageEvent | null = null

  const prelude: TraceGroup = { anchor: null, anchorCursor: null, traceEvents: [] }

  for (const event of allEvents) {
    if (event.kind === 'message') {
      currentAnchor = event
      if (!groupsByCursor.has(event.cursor)) {
        order.push(event.cursor)
        ensureGroup(groupsByCursor, event.cursor, event)
      }
      continue
    }

    if (!currentAnchor) {
      prelude.traceEvents.push(event)
      continue
    }

    const group = ensureGroup(groupsByCursor, currentAnchor.cursor, currentAnchor)
    group.traceEvents.push(event)
  }

  const groups: TraceGroup[] = []
  if (prelude.traceEvents.length) {
    groups.push(prelude)
  }
  for (const cursor of order) {
    const group = groupsByCursor.get(cursor)
    if (group && group.traceEvents.length) {
      groups.push(group)
    }
  }

  const countsByAnchorCursor: Record<string, number> = {}
  for (const group of groups) {
    if (!group.anchorCursor) continue
    countsByAnchorCursor[group.anchorCursor] = group.traceEvents.length
  }

  return { groups, countsByAnchorCursor }
}

