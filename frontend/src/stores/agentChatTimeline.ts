import type { KanbanEvent, ThinkingEvent, TimelineEvent, ToolClusterEvent, ToolCallEntry } from '../types/agentChat'
import { pickHtmlCandidate, sanitizeHtml } from '../util/sanitize'
import { compareTimelineCursors } from '../util/timelineCursor'

export function normalizeTimelineEvent(event: TimelineEvent): TimelineEvent {
  if (event.kind !== 'message') {
    return event
  }

  const candidate = pickHtmlCandidate(event.message.bodyHtml, event.message.bodyText)
  if (!candidate) {
    if (event.message.bodyHtml === undefined) {
      return {
        ...event,
        message: {
          ...event.message,
          bodyHtml: '',
        },
      }
    }
    return event
  }

  const sanitized = sanitizeHtml(candidate)
  if ((event.message.bodyHtml ?? '') === sanitized) {
    return event
  }

  return {
    ...event,
    message: {
      ...event.message,
      bodyHtml: sanitized,
    },
  }
}

function sortTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  return [...events].sort((a, b) => compareTimelineCursors(a.cursor, b.cursor))
}

function pickNonEmptyString(value: string | null | undefined, fallback: string | null | undefined): string | undefined {
  if (typeof value === 'string' && value.trim()) {
    return value
  }
  return fallback ?? undefined
}

function pickNonEmptyArray<T>(value: T[] | null | undefined, fallback: T[] | null | undefined): T[] | undefined {
  if (value && value.length) {
    return value
  }
  return fallback ?? undefined
}

function sortThinkingEntries(entries: ThinkingEvent[]): ThinkingEvent[] {
  return [...entries].sort((left, right) => compareTimelineCursors(left.cursor, right.cursor))
}

function sortKanbanEntries(entries: KanbanEvent[]): KanbanEvent[] {
  return [...entries].sort((left, right) => compareTimelineCursors(left.cursor, right.cursor))
}

function mergeThinkingEntries(
  base: ThinkingEvent[] | undefined,
  incoming: ThinkingEvent[] | undefined,
): ThinkingEvent[] | undefined {
  if (!base?.length && !incoming?.length) {
    return undefined
  }

  const entryMap = new Map<string, ThinkingEvent>()
  for (const entry of base ?? []) {
    if (!entry?.cursor) {
      continue
    }
    entryMap.set(entry.cursor, entry)
  }
  for (const entry of incoming ?? []) {
    if (!entry?.cursor) {
      continue
    }
    entryMap.set(entry.cursor, entry)
  }

  return sortThinkingEntries(Array.from(entryMap.values()))
}

function mergeKanbanEntries(
  base: KanbanEvent[] | undefined,
  incoming: KanbanEvent[] | undefined,
): KanbanEvent[] | undefined {
  if (!base?.length && !incoming?.length) {
    return undefined
  }

  const entryMap = new Map<string, KanbanEvent>()
  for (const entry of base ?? []) {
    if (!entry?.cursor) {
      continue
    }
    entryMap.set(entry.cursor, entry)
  }
  for (const entry of incoming ?? []) {
    if (!entry?.cursor) {
      continue
    }
    entryMap.set(entry.cursor, entry)
  }

  return sortKanbanEntries(Array.from(entryMap.values()))
}

function mergeToolEntry(base: ToolCallEntry, incoming: ToolCallEntry): ToolCallEntry {
  return {
    ...base,
    ...incoming,
    summary: pickNonEmptyString(incoming.summary, base.summary),
    caption: pickNonEmptyString(incoming.caption ?? undefined, base.caption ?? undefined),
    timestamp: pickNonEmptyString(incoming.timestamp ?? undefined, base.timestamp ?? undefined),
    toolName: pickNonEmptyString(incoming.toolName ?? undefined, base.toolName ?? undefined),
    parameters: incoming.parameters ?? base.parameters,
    sqlStatements: pickNonEmptyArray(incoming.sqlStatements ?? undefined, base.sqlStatements ?? undefined),
    result: pickNonEmptyString(incoming.result ?? undefined, base.result ?? undefined),
    charterText: pickNonEmptyString(incoming.charterText ?? undefined, base.charterText ?? undefined),
    status: incoming.status ?? base.status,
    cursor: incoming.cursor ?? base.cursor,
    meta: incoming.meta ?? base.meta,
    chartImageUrl: pickNonEmptyString(incoming.chartImageUrl ?? undefined, base.chartImageUrl ?? undefined),
    createImageUrl: pickNonEmptyString(incoming.createImageUrl ?? undefined, base.createImageUrl ?? undefined),
  }
}

function dedupeToolEntries(entries: ToolCallEntry[]): ToolCallEntry[] {
  const entryMap = new Map<string, ToolCallEntry>()
  for (const entry of entries) {
    if (!entry?.id) {
      continue
    }
    const existing = entryMap.get(entry.id)
    entryMap.set(entry.id, existing ? mergeToolEntry(existing, entry) : entry)
  }
  return Array.from(entryMap.values())
}

function compareToolEntries(left: ToolCallEntry, right: ToolCallEntry): number {
  if (left.cursor && right.cursor) {
    return compareTimelineCursors(left.cursor, right.cursor)
  }
  if (left.timestamp && right.timestamp) {
    return left.timestamp.localeCompare(right.timestamp)
  }
  if (left.timestamp) {
    return -1
  }
  if (right.timestamp) {
    return 1
  }
  return left.id.localeCompare(right.id)
}

function sortToolEntries(entries: ToolCallEntry[]): ToolCallEntry[] {
  return [...entries].sort(compareToolEntries)
}

function resolveClusterCursor(
  entries: ToolCallEntry[],
  fallback: string,
  secondaryFallback: string,
  thinkingEntries: ThinkingEvent[] | undefined,
  kanbanEntries: KanbanEvent[] | undefined,
): string {
  const entryCursors = entries
    .map((entry) => entry.cursor)
    .filter((cursor): cursor is string => Boolean(cursor))
  const thinkingCursors = (thinkingEntries ?? [])
    .map((entry) => entry.cursor)
    .filter((cursor): cursor is string => Boolean(cursor))
  const kanbanCursors = (kanbanEntries ?? [])
    .map((entry) => entry.cursor)
    .filter((cursor): cursor is string => Boolean(cursor))
  const cursors = [...entryCursors, ...thinkingCursors, ...kanbanCursors]
  if (!cursors.length) {
    return compareTimelineCursors(fallback, secondaryFallback) <= 0 ? fallback : secondaryFallback
  }
  return cursors.reduce((earliest, cursor) => (compareTimelineCursors(cursor, earliest) < 0 ? cursor : earliest))
}

function pickTimestamp(entries: ToolCallEntry[], direction: 'earliest' | 'latest'): string | null {
  if (direction === 'earliest') {
    for (const entry of entries) {
      if (entry.timestamp) {
        return entry.timestamp
      }
    }
    return null
  }
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].timestamp) {
      return entries[i].timestamp ?? null
    }
  }
  return null
}

function pickNonToolTimestamp(
  thinkingEntries: ThinkingEvent[] | undefined,
  kanbanEntries: KanbanEvent[] | undefined,
  direction: 'earliest' | 'latest',
): string | null {
  const combined = [...(thinkingEntries ?? []), ...(kanbanEntries ?? [])]
  if (!combined.length) {
    return null
  }
  const ordered = combined.sort((left, right) => compareTimelineCursors(left.cursor, right.cursor))
  const slice = direction === 'earliest' ? ordered : [...ordered].reverse()
  for (const entry of slice) {
    if (entry.timestamp) {
      return entry.timestamp ?? null
    }
  }
  return null
}

function buildCluster(
  base: ToolClusterEvent,
  entries: ToolCallEntry[],
  threshold: number,
  secondaryCursor: string,
  thinkingEntries?: ThinkingEvent[] | undefined,
  kanbanEntries?: KanbanEvent[] | undefined,
): ToolClusterEvent {
  const sortedEntries = sortToolEntries(dedupeToolEntries(entries))
  const cursor = resolveClusterCursor(sortedEntries, base.cursor, secondaryCursor, thinkingEntries, kanbanEntries)
  const earliestTimestamp =
    pickTimestamp(sortedEntries, 'earliest') ?? pickNonToolTimestamp(thinkingEntries, kanbanEntries, 'earliest')
  const latestTimestamp =
    pickTimestamp(sortedEntries, 'latest') ?? pickNonToolTimestamp(thinkingEntries, kanbanEntries, 'latest')
  return {
    kind: 'steps',
    cursor,
    entries: sortedEntries,
    entryCount: sortedEntries.length,
    collapseThreshold: threshold,
    collapsible: sortedEntries.length >= threshold,
    earliestTimestamp,
    latestTimestamp,
    thinkingEntries: thinkingEntries?.length ? thinkingEntries : undefined,
    kanbanEntries: kanbanEntries?.length ? kanbanEntries : undefined,
  }
}

export function mergeToolClusters(base: ToolClusterEvent, incoming: ToolClusterEvent): ToolClusterEvent {
  const threshold = Math.max(base.collapseThreshold, incoming.collapseThreshold)
  const thinkingEntries = mergeThinkingEntries(base.thinkingEntries, incoming.thinkingEntries)
  const kanbanEntries = mergeKanbanEntries(base.kanbanEntries, incoming.kanbanEntries)
  return buildCluster(base, [...base.entries, ...incoming.entries], threshold, incoming.cursor, thinkingEntries, kanbanEntries)
}

function coalesceTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  const deduped: TimelineEvent[] = []
  const seenToolEntryIds = new Set<string>()
  let segment: TimelineEvent[] = []

  const flushSegment = () => {
    if (!segment.length) {
      return
    }

    const stepEvents = segment.filter((event): event is ToolClusterEvent => event.kind === 'steps')
    const thinkingEvents = segment.filter((event): event is ThinkingEvent => event.kind === 'thinking')
    if (!stepEvents.length) {
      deduped.push(...segment)
      segment = []
      return
    }

    let stepThinking: ThinkingEvent[] | undefined
    let stepKanban: KanbanEvent[] | undefined
    const toolEntryMap = new Map<string, ToolCallEntry>()
    let collapseThreshold = 0

    for (const event of stepEvents) {
      collapseThreshold = Math.max(collapseThreshold, event.collapseThreshold)
      if (event.thinkingEntries?.length) {
        stepThinking = mergeThinkingEntries(stepThinking, event.thinkingEntries) ?? stepThinking
      }
      if (event.kanbanEntries?.length) {
        stepKanban = mergeKanbanEntries(stepKanban, event.kanbanEntries) ?? stepKanban
      }
      for (const entry of event.entries) {
        if (!entry?.id || seenToolEntryIds.has(entry.id)) {
          continue
        }
        const existing = toolEntryMap.get(entry.id)
        toolEntryMap.set(entry.id, existing ? mergeToolEntry(existing, entry) : entry)
      }
    }

    for (const entryId of toolEntryMap.keys()) {
      seenToolEntryIds.add(entryId)
    }

    const toolEntries = Array.from(toolEntryMap.values())
    const mergedThinking = mergeThinkingEntries(stepThinking, thinkingEvents)
    const mergedKanban = stepKanban

    if (toolEntries.length) {
      const threshold = collapseThreshold || 3
      const base = stepEvents[0]
      const secondaryCursor = stepEvents[stepEvents.length - 1].cursor
      const mergedCluster = buildCluster(
        base,
        toolEntries,
        threshold,
        secondaryCursor,
        mergedThinking,
        undefined,
      )
      deduped.push(mergedCluster)
      if (mergedKanban?.length) {
        deduped.push(...mergedKanban)
      }
      segment = []
      return
    }

    if (mergedThinking?.length) {
      deduped.push(...mergedThinking)
    }
    if (mergedKanban?.length) {
      deduped.push(...mergedKanban)
      segment = []
      return
    }

    segment = []
  }

  for (const event of events) {
    if (event.kind === 'steps' || event.kind === 'thinking') {
      segment.push(event)
      continue
    }

    flushSegment()
    deduped.push(event)
  }

  flushSegment()

  return deduped
}

function finalizeTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  const sorted = sortTimelineEvents(events)
  const coalesced = coalesceTimelineEvents(sorted)
  const resorted = sortTimelineEvents(coalesced)
  return coalesceTimelineEvents(resorted)
}

export function prepareTimelineEvents(events: TimelineEvent[]): TimelineEvent[] {
  return mergeTimelineEvents([], events)
}

export function mergeTimelineEvents(existing: TimelineEvent[], incoming: TimelineEvent[]): TimelineEvent[] {
  const map = new Map<string, TimelineEvent>()
  // Existing events are already normalized when stored; avoid re-sanitizing each merge.
  for (const event of existing) {
    if (!event?.cursor) {
      continue
    }
    map.set(event.cursor, event)
  }
  for (const event of incoming) {
    const normalized = normalizeTimelineEvent(event)
    const current = map.get(normalized.cursor)
    if (current && current.kind === 'steps' && normalized.kind === 'steps') {
      map.set(normalized.cursor, mergeToolClusters(current, normalized))
    } else {
      map.set(normalized.cursor, normalized)
    }
  }
  return finalizeTimelineEvents(Array.from(map.values()))
}
