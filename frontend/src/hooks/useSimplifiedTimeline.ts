import { useMemo } from 'react'
import type { TimelineEvent, ToolCallEntry } from '../types/agentChat'

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

export type InlineCharterUpdate = {
  kind: 'inline-charter'
  cursor: string
  entry: ToolCallEntry
}

export type InlineScheduleUpdate = {
  kind: 'inline-schedule'
  cursor: string
  entry: ToolCallEntry
}

export type SimplifiedTimelineItem =
  | TimelineEvent
  | CollapsedEventGroup
  | InlineCharterUpdate
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

const KANBAN_TABLE_RE = /__kanban/i

/**
 * Check whether a ToolCallEntry is a kanban-only SQL batch.
 * The raw SQL may live in `entry.sqlStatements` (pre-parsed) or in
 * `entry.parameters.sql` / `entry.parameters.operations` (raw from backend).
 */
function isKanbanOnlySql(entry: ToolCallEntry): boolean {
  // Try pre-parsed sqlStatements first
  const stmts = entry.sqlStatements
  if (stmts && stmts.length > 0) {
    return stmts.every((s) => KANBAN_TABLE_RE.test(s))
  }
  // Fall back to parameters.sql (single string with semicolons)
  const params = entry.parameters as Record<string, unknown> | undefined | null
  if (params) {
    const rawSql = typeof params.sql === 'string' ? params.sql : null
    if (rawSql) {
      const lines = rawSql.split(/;\s*/).filter((s) => s.trim().length > 0)
      return lines.length > 0 && lines.every((s) => KANBAN_TABLE_RE.test(s))
    }
  }
  return false
}

/**
 * Returns true when every entry in a steps event is "invisible" — either
 * kanban-only SQL (the transform marks them skip:true), or a charter/schedule
 * entry that will be shown inline.  We strip these from collapsed groups so
 * the user doesn't see a pill that opens an empty overlay.
 */
function isEmptySteps(event: TimelineEvent): boolean {
  if (event.kind !== 'steps') return false
  if (event.entries.length === 0) return true
  return event.entries.every((entry) => {
    // Charter / schedule entries are shown inline — treat as invisible here
    if (isCharterEntry(entry)) return true
    if (isScheduleEntry(entry)) return true
    // Kanban-only SQL batches render empty (skip:true in tool transform)
    if (isKanbanOnlySql(entry)) return true
    return false
  })
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
  const parts: string[] = []
  const actionCount = counts.toolCallCount + counts.thinkingCount
  if (actionCount > 0) {
    parts.push(`${actionCount} action${actionCount === 1 ? '' : 's'}`)
  }
  if (counts.kanbanCount > 0) {
    parts.push(`${counts.kanbanCount} board update${counts.kanbanCount === 1 ? '' : 's'}`)
  }
  return parts.join(', ') || '1 action'
}

function makeCollapsedGroup(buffer: TimelineEvent[]): CollapsedEventGroup {
  const counts = countByKind(buffer)
  return {
    kind: 'collapsed-group',
    cursor: buffer[0].cursor,
    events: [...buffer],
    summary: {
      totalCount: buffer.length,
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
  charterClusterCursor: string | null
  charterEntry: ToolCallEntry | null
  scheduleClusterCursor: string | null
  scheduleEntry: ToolCallEntry | null
}

function findLatestStatusCursors(events: TimelineEvent[]): LatestStatusCursors {
  let kanbanCursor: string | null = null
  let charterClusterCursor: string | null = null
  let charterEntry: ToolCallEntry | null = null
  let scheduleClusterCursor: string | null = null
  let scheduleEntry: ToolCallEntry | null = null

  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.kind === 'kanban' && !kanbanCursor) {
      kanbanCursor = event.cursor
    }
    if (event.kind === 'steps') {
      for (const entry of event.entries) {
        if (isCharterEntry(entry) && !charterClusterCursor) {
          charterClusterCursor = event.cursor
          charterEntry = entry
        }
        if (isScheduleEntry(entry) && !scheduleClusterCursor) {
          scheduleClusterCursor = event.cursor
          scheduleEntry = entry
        }
      }
    }
    // Early exit once all found
    if (kanbanCursor && charterClusterCursor && scheduleClusterCursor) break
  }

  return { kanbanCursor, charterClusterCursor, charterEntry, scheduleClusterCursor, scheduleEntry }
}

// ---------------------------------------------------------------------------
// Main collapse algorithm
// ---------------------------------------------------------------------------

/**
 * Collapses consecutive non-message events into summary groups.
 *
 * Messages pass through unchanged. The *latest* kanban, charter, and schedule
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
    // Drop steps events whose entries are all invisible (kanban SQL, charter, schedule)
    const meaningful = buffer.filter((e) => !isEmptySteps(e))
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
      const hasCharter = event.cursor === latest.charterClusterCursor
      const hasSchedule = event.cursor === latest.scheduleClusterCursor

      if (hasCharter || hasSchedule) {
        // The cluster itself still collapses (may have other entries)
        buffer.push(event)
        flush()
        // Emit inline items for the latest charter/schedule
        if (hasCharter && latest.charterEntry) {
          result.push({
            kind: 'inline-charter',
            cursor: `charter:${latest.charterEntry.id ?? event.cursor}`,
            entry: latest.charterEntry,
          })
        }
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
