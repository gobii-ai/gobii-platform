import type { TimelineEvent, ToolClusterEvent } from '../../types/agentChat'
import { parseAgentConfigUpdates } from '../tooling/agentConfigSql'
import { transformToolCluster } from './tooling/toolRegistry'
import type { ToolEntryDisplay } from './tooling/types'

export type StatusExpansionTargets = {
  latestKanbanCursor: string | null
  latestScheduleEntryId: string | null
}

export function isScheduleDisplayEntry(entry: ToolEntryDisplay): boolean {
  if (entry.toolName === 'update_schedule') {
    return true
  }

  if (entry.toolName !== 'sqlite_batch' || !entry.sqlStatements?.length) {
    return false
  }

  const parsedUpdate = parseAgentConfigUpdates(entry.sqlStatements)
  return Boolean(parsedUpdate?.updatesSchedule)
}

export function isStatusDisplayEntry(entry: ToolEntryDisplay): boolean {
  return entry.toolName === 'kanban' || isScheduleDisplayEntry(entry)
}

export function resolveEntrySeparation(
  entry: ToolEntryDisplay,
  targets: StatusExpansionTargets,
): boolean {
  if (entry.toolName === 'kanban') {
    return entry.cursor === targets.latestKanbanCursor
  }

  if (isScheduleDisplayEntry(entry)) {
    return entry.id === targets.latestScheduleEntryId
  }

  return Boolean(entry.separateFromPreview)
}

export function findLatestStatusExpansionTargets(events: TimelineEvent[]): StatusExpansionTargets {
  let latestKanbanCursor: string | null = null
  let latestScheduleEntryId: string | null = null

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]

    if (!latestKanbanCursor && event.kind === 'kanban') {
      latestKanbanCursor = event.cursor
    }

    if (!latestScheduleEntryId && event.kind === 'steps') {
      const transformed = transformToolCluster(event)
      for (let entryIndex = transformed.entries.length - 1; entryIndex >= 0; entryIndex -= 1) {
        const entry = transformed.entries[entryIndex]
        if (isScheduleDisplayEntry(entry)) {
          latestScheduleEntryId = entry.id
          break
        }
      }
    }

    if (latestKanbanCursor && latestScheduleEntryId) {
      break
    }
  }

  return {
    latestKanbanCursor,
    latestScheduleEntryId,
  }
}

export function eventHasLatestStatus(event: TimelineEvent, targets: StatusExpansionTargets): boolean {
  if (event.kind === 'kanban') {
    return event.cursor === targets.latestKanbanCursor
  }
  if (event.kind !== 'steps') {
    return false
  }
  return transformToolCluster(event as ToolClusterEvent).entries.some(
    (entry) => isStatusDisplayEntry(entry) && resolveEntrySeparation(entry, targets),
  )
}

export function eventHasHistoricalStatus(event: TimelineEvent, targets: StatusExpansionTargets): boolean {
  if (event.kind === 'kanban') {
    return event.cursor !== targets.latestKanbanCursor
  }
  if (event.kind !== 'steps') {
    return false
  }
  return transformToolCluster(event as ToolClusterEvent).entries.some(
    (entry) => isStatusDisplayEntry(entry) && !resolveEntrySeparation(entry, targets),
  )
}
