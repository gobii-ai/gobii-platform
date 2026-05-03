import type { TimelineEvent, ToolClusterEvent } from '../../types/agentChat'
import { parseAgentConfigUpdates } from '../tooling/agentConfigSql'
import { transformToolCluster } from './tooling/toolRegistry'
import type { ToolEntryDisplay } from './tooling/types'

export type StatusExpansionTargets = {
  latestPlanCursor: string | null
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
  return isScheduleDisplayEntry(entry)
}

export function resolveEntrySeparation(
  entry: ToolEntryDisplay,
  targets: StatusExpansionTargets,
): boolean {
  if (isScheduleDisplayEntry(entry)) {
    return entry.id === targets.latestScheduleEntryId
  }

  return Boolean(entry.separateFromPreview)
}

export function findLatestStatusExpansionTargets(events: TimelineEvent[]): StatusExpansionTargets {
  let latestScheduleEntryId: string | null = null

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]

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

    if (latestScheduleEntryId) {
      break
    }
  }

  return {
    latestPlanCursor: null,
    latestScheduleEntryId,
  }
}

export function eventHasLatestStatus(event: TimelineEvent, targets: StatusExpansionTargets): boolean {
  if (event.kind === 'plan' || event.kind === 'kanban') {
    return false
  }
  if (event.kind !== 'steps') {
    return false
  }
  return transformToolCluster(event as ToolClusterEvent).entries.some(
    (entry) => isStatusDisplayEntry(entry) && resolveEntrySeparation(entry, targets),
  )
}

export function eventHasHistoricalStatus(event: TimelineEvent, targets: StatusExpansionTargets): boolean {
  if (event.kind === 'plan' || event.kind === 'kanban') {
    return false
  }
  if (event.kind !== 'steps') {
    return false
  }
  return transformToolCluster(event as ToolClusterEvent).entries.some(
    (entry) => isStatusDisplayEntry(entry) && !resolveEntrySeparation(entry, targets),
  )
}
