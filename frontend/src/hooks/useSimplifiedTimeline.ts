import { useMemo } from 'react'
import type { TimelineEvent, ToolCallEntry } from '../types/agentChat'
import { isClusterRenderable, transformToolCluster } from '../components/agentChat/tooling/toolRegistry'
import { buildActionCountLabel } from '../components/agentChat/activityEntryUtils'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CollapsedEventGroup = {
  kind: 'collapsed-group'
  cursor: string
  events: TimelineEvent[]
  summary: {
    totalCount: number
    toolCallCount: number
    thinkingCount: number
    kanbanCount: number
    label: string
  }
}

export type InlineScheduleUpdate = {
  kind: 'inline-schedule'
  cursor: string
  entry: ToolCallEntry
}

export type SimplifiedTimelineItem =
  | TimelineEvent
  | CollapsedEventGroup
  | InlineScheduleUpdate

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function isCharterEntry(entry: ToolCallEntry): boolean {
  if (entry.toolName === 'update_charter') return true
  if (entry.charterText != null && entry.charterText.trim() !== '') return true
  return false
}

export function isScheduleEntry(entry: ToolCallEntry): boolean {
  return entry.toolName === 'update_schedule'
}

function isRenderableCollapsedEvent(event: TimelineEvent): boolean {
  if (event.kind !== 'steps') return true
  return isClusterRenderable(transformToolCluster(event))
}

function countByKind(events: TimelineEvent[]) {
  let toolCallCount = 0
  let thinkingCount = 0
  let kanbanCount = 0
  for (const e of events) {
    if (e.kind === 'steps') toolCallCount += e.entryCount
    else if (e.kind === 'thinking') thinkingCount++
    else if (e.kind === 'kanban') kanbanCount++
  }
  return { toolCallCount, thinkingCount, kanbanCount }
}

export function buildCollapsedGroupLabel(counts: {
  toolCallCount: number
  thinkingCount: number
  kanbanCount: number
}): string {
  const actionCount = counts.toolCallCount + counts.thinkingCount + counts.kanbanCount
  return buildActionCountLabel(actionCount || 1)
}

function makeCollapsedGroup(buffer: TimelineEvent[]): CollapsedEventGroup {
  const counts = countByKind(buffer)
  const totalCount = counts.toolCallCount + counts.thinkingCount + counts.kanbanCount
  return {
    kind: 'collapsed-group',
    cursor: buffer[0].cursor,
    events: [...buffer],
    summary: {
      totalCount,
      ...counts,
      label: buildCollapsedGroupLabel(counts),
    },
  }
}

// ---------------------------------------------------------------------------
// Pre-scan: find the latest kanban, charter, and schedule cursors
// ---------------------------------------------------------------------------

type LatestStatusCursors = {
  kanbanCursor: string | null
  scheduleClusterCursor: string | null
  scheduleEntry: ToolCallEntry | null
}

function findLatestStatusCursors(events: TimelineEvent[]): LatestStatusCursors {
  let kanbanCursor: string | null = null
  let scheduleClusterCursor: string | null = null
  let scheduleEntry: ToolCallEntry | null = null

  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.kind === 'kanban' && !kanbanCursor) {
      kanbanCursor = event.cursor
    }
    if (event.kind === 'steps') {
      for (let j = event.entries.length - 1; j >= 0; j--) {
        const entry = event.entries[j]
        if (isScheduleEntry(entry) && !scheduleClusterCursor) {
          scheduleClusterCursor = event.cursor
          scheduleEntry = entry
        }
      }
    }
    // Early exit once all found
    if (kanbanCursor && scheduleClusterCursor) break
  }

  return { kanbanCursor, scheduleClusterCursor, scheduleEntry }
}

// ---------------------------------------------------------------------------
// Main collapse algorithm
// ---------------------------------------------------------------------------

/**
 * Collapses consecutive non-message events into summary groups.
 *
 * Messages pass through unchanged. The *latest* kanban and schedule
 * updates also appear inline at their chronological position so the user can
 * see current status at a glance. Older instances of these events collapse
 * normally.
 */
export function collapseTimeline(events: TimelineEvent[]): SimplifiedTimelineItem[] {
  const latest = findLatestStatusCursors(events)
  const result: SimplifiedTimelineItem[] = []
  let buffer: TimelineEvent[] = []

  const flush = () => {
    if (buffer.length === 0) return
    const meaningful = buffer.filter(isRenderableCollapsedEvent)
    if (meaningful.length > 0) {
      result.push(makeCollapsedGroup(meaningful))
    }
    buffer = []
  }

  for (const event of events) {
    // Messages always pass through
    if (event.kind === 'message') {
      flush()
      result.push(event)
      continue
    }

    // Latest kanban → show inline
    if (event.kind === 'kanban' && event.cursor === latest.kanbanCursor) {
      flush()
      result.push(event)
      continue
    }

    // Steps cluster that contains latest charter and/or schedule
    if (event.kind === 'steps') {
      const hasSchedule = event.cursor === latest.scheduleClusterCursor

      if (hasSchedule) {
        const filteredEntries = event.entries.filter((entry) => {
          if (hasSchedule && latest.scheduleEntry && entry.id === latest.scheduleEntry.id) {
            return false
          }
          return true
        })

        const clusterForCollapse = filteredEntries.length === event.entries.length
          ? event
          : {
            ...event,
            entries: filteredEntries,
            entryCount: filteredEntries.length,
            collapsible: filteredEntries.length >= event.collapseThreshold,
          }

        // Keep remaining cluster content collapsible, but avoid duplicating inline status entries.
        buffer.push(clusterForCollapse)
        flush()
        // Emit an inline item for the latest schedule update.
        if (hasSchedule && latest.scheduleEntry) {
          result.push({
            kind: 'inline-schedule',
            cursor: `schedule:${latest.scheduleEntry.id ?? event.cursor}`,
            entry: latest.scheduleEntry,
          })
        }
        continue
      }
    }

    // Everything else → buffer for collapsing
    buffer.push(event)
  }

  flush()
  return result
}

export function useSimplifiedTimeline(
  events: TimelineEvent[],
  enabled: boolean,
): SimplifiedTimelineItem[] {
  return useMemo(
    () => (enabled ? collapseTimeline(events) : (events as SimplifiedTimelineItem[])),
    [events, enabled],
  )
}
