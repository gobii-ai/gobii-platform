import { useMemo } from 'react'
import type { TimelineEvent, ToolCallEntry } from '../types/agentChat'
import { isClusterRenderable, transformToolCluster } from '../components/agentChat/tooling/toolRegistry'
import { buildActionCountLabel, flattenTimelineEventsToEntries } from '../components/agentChat/activityEntryUtils'
import type { StatusExpansionTargets } from '../components/agentChat/statusExpansion'
import {
  eventHasLatestStatus,
} from '../components/agentChat/statusExpansion'

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
    planCount: number
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

export type CollapseDetailedStatusRunsOptions = {
  keepTrailingActivityExpanded?: boolean
}

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
  if (event.kind === 'plan' || event.kind === 'kanban') return false
  if (event.kind !== 'steps') return true
  return isClusterRenderable(transformToolCluster(event))
}

function countByKind(events: TimelineEvent[]) {
  let toolCallCount = 0
  let thinkingCount = 0
  let planCount = 0
  for (const e of events) {
    if (e.kind === 'steps') toolCallCount += transformToolCluster(e).entries.length
    else if (e.kind === 'thinking') thinkingCount += flattenTimelineEventsToEntries([e]).length
    else if (e.kind === 'plan' || e.kind === 'kanban') planCount++
  }
  return { toolCallCount, thinkingCount, planCount }
}

export function buildCollapsedGroupLabel(counts: {
  toolCallCount: number
  thinkingCount: number
  planCount: number
}): string {
  const actionCount = counts.toolCallCount + counts.thinkingCount + counts.planCount
  return buildActionCountLabel(actionCount || 1)
}

function makeCollapsedGroup(buffer: TimelineEvent[]): CollapsedEventGroup {
  const counts = countByKind(buffer)
  const totalCount = counts.toolCallCount + counts.thinkingCount + counts.planCount
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

function visibleActivityCount(events: TimelineEvent[]): number {
  return flattenTimelineEventsToEntries(events).length
}

function disableStepClusterCollapse(event: TimelineEvent): TimelineEvent {
  if (event.kind !== 'steps') {
    return event
  }
  return {
    ...event,
    collapsible: false,
    collapseThreshold: Infinity,
  }
}

function expandedRenderableEvents(events: TimelineEvent[], disableStepCollapse: boolean): TimelineEvent[] {
  return events
    .filter((event) => visibleActivityCount([event]) > 0)
    .map((event) => (disableStepCollapse ? disableStepClusterCollapse(event) : event))
}

// ---------------------------------------------------------------------------
// Pre-scan: find the latest schedule cursor
// ---------------------------------------------------------------------------

type LatestStatusCursors = {
  scheduleClusterCursor: string | null
  scheduleEntry: ToolCallEntry | null
}

function findLatestStatusCursors(events: TimelineEvent[]): LatestStatusCursors {
  let scheduleClusterCursor: string | null = null
  let scheduleEntry: ToolCallEntry | null = null

  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
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
    if (scheduleClusterCursor) break
  }

  return { scheduleClusterCursor, scheduleEntry }
}

// ---------------------------------------------------------------------------
// Main collapse algorithm
// ---------------------------------------------------------------------------

/**
 * Collapses consecutive non-message events into summary groups.
 *
 * Messages pass through unchanged. The latest schedule update also appears
 * inline at its chronological position so the user can see current status at
 * a glance. Plan updates are rendered in the side panel, not the timeline.
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

    if (event.kind === 'plan' || event.kind === 'kanban') {
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

export function collapseDetailedStatusRuns(
  events: TimelineEvent[],
  targets: StatusExpansionTargets,
  options: CollapseDetailedStatusRunsOptions = {},
): SimplifiedTimelineItem[] {
  const result: SimplifiedTimelineItem[] = []
  let buffer: TimelineEvent[] = []
  const latestMessageIndex = options.keepTrailingActivityExpanded === true
    ? events.reduce((latestIndex, event, index) => (event.kind === 'message' ? index : latestIndex), -1)
    : -1

  const flush = (forceExpanded = false) => {
    if (buffer.length === 0) return
    const meaningful = buffer.filter(isRenderableCollapsedEvent)
    const actionCount = visibleActivityCount(meaningful)
    if (actionCount === 1 || (forceExpanded && actionCount > 0)) {
      result.push(...expandedRenderableEvents(meaningful, forceExpanded))
    } else if (actionCount > 1) {
      result.push(makeCollapsedGroup(meaningful))
    }
    buffer = []
  }

  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    if (event.kind === 'message') {
      flush()
      result.push(event)
      continue
    }

    if (event.kind === 'plan' || event.kind === 'kanban') {
      continue
    }

    if (eventHasLatestStatus(event, targets)) {
      flush(options.keepTrailingActivityExpanded === true && index > latestMessageIndex)
      result.push(event)
      continue
    }

    buffer.push(event)
  }

  flush(options.keepTrailingActivityExpanded === true)
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
