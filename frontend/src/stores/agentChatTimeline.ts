import type { ThinkingEvent, TimelineEvent, ToolClusterEvent, ToolCallEntry } from '../types/agentChat'
import { pickHtmlCandidate, sanitizeHtml } from '../util/sanitize'

type ParsedTimelineCursor = {
  value: number
  kind: string
  identifier: string
}

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

function parseTimelineCursor(raw: string | null | undefined): ParsedTimelineCursor | null {
  if (!raw) {
    return null
  }
  const parts = raw.split(':')
  if (parts.length < 3) {
    return null
  }
  const [valuePart, kind, ...identifierParts] = parts
  const value = Number(valuePart)
  if (!Number.isFinite(value)) {
    return null
  }
  return {
    value,
    kind,
    identifier: identifierParts.join(':'),
  }
}

function compareTimelineCursors(left: string, right: string): number {
  if (left === right) {
    return 0
  }
  const leftParsed = parseTimelineCursor(left)
  const rightParsed = parseTimelineCursor(right)
  if (leftParsed && rightParsed) {
    if (leftParsed.value !== rightParsed.value) {
      return leftParsed.value - rightParsed.value
    }
    if (leftParsed.kind !== rightParsed.kind) {
      return leftParsed.kind.localeCompare(rightParsed.kind)
    }
    if (leftParsed.kind === 'message') {
      const leftSeq = Number(leftParsed.identifier)
      const rightSeq = Number(rightParsed.identifier)
      if (Number.isFinite(leftSeq) && Number.isFinite(rightSeq) && leftSeq !== rightSeq) {
        return leftSeq - rightSeq
      }
    }
    return leftParsed.identifier.localeCompare(rightParsed.identifier)
  }
  const leftValue = Number(left.split(':', 1)[0])
  const rightValue = Number(right.split(':', 1)[0])
  if (Number.isFinite(leftValue) && Number.isFinite(rightValue) && leftValue !== rightValue) {
    return leftValue - rightValue
  }
  return left.localeCompare(right)
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
    cursor: incoming.cursor ?? base.cursor,
    meta: incoming.meta ?? base.meta,
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

function resolveClusterCursor(entries: ToolCallEntry[], fallback: string, secondaryFallback: string): string {
  const cursors = entries
    .map((entry) => entry.cursor)
    .filter((cursor): cursor is string => Boolean(cursor))
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

function buildCluster(
  base: ToolClusterEvent,
  entries: ToolCallEntry[],
  threshold: number,
  secondaryCursor: string,
  thinkingEntries?: ThinkingEvent[] | undefined,
): ToolClusterEvent {
  const sortedEntries = sortToolEntries(dedupeToolEntries(entries))
  const cursor = resolveClusterCursor(sortedEntries, base.cursor, secondaryCursor)
  return {
    kind: 'steps',
    cursor,
    entries: sortedEntries,
    entryCount: sortedEntries.length,
    collapseThreshold: threshold,
    collapsible: sortedEntries.length >= threshold,
    earliestTimestamp: pickTimestamp(sortedEntries, 'earliest'),
    latestTimestamp: pickTimestamp(sortedEntries, 'latest'),
    thinkingEntries: thinkingEntries?.length ? thinkingEntries : undefined,
  }
}

export function mergeToolClusters(base: ToolClusterEvent, incoming: ToolClusterEvent): ToolClusterEvent {
  const threshold = Math.max(base.collapseThreshold, incoming.collapseThreshold)
  const thinkingEntries = mergeThinkingEntries(base.thinkingEntries, incoming.thinkingEntries)
  return buildCluster(base, [...base.entries, ...incoming.entries], threshold, incoming.cursor, thinkingEntries)
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
    if (!stepEvents.length) {
      deduped.push(...segment)
      segment = []
      return
    }

    let mergedCluster: ToolClusterEvent | null = null
    let pendingStepThinking: ThinkingEvent[] = []
    for (const event of stepEvents) {
      const uniqueEntries = dedupeToolEntries(event.entries).filter((entry) => {
        if (seenToolEntryIds.has(entry.id)) {
          return false
        }
        seenToolEntryIds.add(entry.id)
        return true
      })

      if (!uniqueEntries.length) {
        if (event.thinkingEntries?.length) {
          pendingStepThinking = mergeThinkingEntries(pendingStepThinking, event.thinkingEntries) ?? pendingStepThinking
        }
        continue
      }

      const normalizedCluster = buildCluster(
        event,
        uniqueEntries,
        event.collapseThreshold,
        event.cursor,
        mergeThinkingEntries(event.thinkingEntries, pendingStepThinking),
      )
      pendingStepThinking = []
      mergedCluster = mergedCluster ? mergeToolClusters(mergedCluster, normalizedCluster) : normalizedCluster
    }

    if (!mergedCluster) {
      const thinkingEvents = segment.filter((event): event is ThinkingEvent => event.kind === 'thinking')
      const mergedThinking = mergeThinkingEntries(thinkingEvents, pendingStepThinking)
      deduped.push(...(mergedThinking ?? thinkingEvents))
      segment = []
      return
    }

    const toolEntryCursors = mergedCluster.entries
      .map((entry) => entry.cursor)
      .filter((cursor): cursor is string => Boolean(cursor))

    if (!toolEntryCursors.length) {
      const thinkingEvents = segment.filter((event): event is ThinkingEvent => event.kind === 'thinking')
      const mergedThinking = mergeThinkingEntries(thinkingEvents, pendingStepThinking)
      deduped.push(...(mergedThinking ?? thinkingEvents))
      segment = []
      return
    }

    const earliestCursor = toolEntryCursors.reduce((earliest, cursor) =>
      compareTimelineCursors(cursor, earliest) < 0 ? cursor : earliest,
    )
    const latestCursor = toolEntryCursors.reduce((latest, cursor) =>
      compareTimelineCursors(cursor, latest) > 0 ? cursor : latest,
    )

    const thinkingBefore: ThinkingEvent[] = []
    const thinkingBetween: ThinkingEvent[] = []
    const thinkingAfter: ThinkingEvent[] = []

    for (const event of segment) {
      if (event.kind !== 'thinking') {
        continue
      }
      if (compareTimelineCursors(event.cursor, earliestCursor) <= 0) {
        thinkingBefore.push(event)
        continue
      }
      if (compareTimelineCursors(event.cursor, latestCursor) >= 0) {
        thinkingAfter.push(event)
        continue
      }
      thinkingBetween.push(event)
    }

    const mergedThinking = mergeThinkingEntries(
      mergeThinkingEntries(mergedCluster.thinkingEntries, pendingStepThinking),
      thinkingBetween,
    )
    const mergedWithThinking = mergedThinking?.length
      ? { ...mergedCluster, thinkingEntries: mergedThinking }
      : mergedCluster

    deduped.push(...thinkingBefore, mergedWithThinking, ...thinkingAfter)
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
  for (const event of existing) {
    const normalized = normalizeTimelineEvent(event)
    map.set(normalized.cursor, normalized)
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
