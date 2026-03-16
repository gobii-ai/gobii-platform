import { CalendarClock, FileCheck2, Workflow } from 'lucide-react'

import type { ToolCallEntry } from '../../../types/agentChat'
import { summarizeSchedule } from '../../../util/schedule'
import { parseAgentConfigUpdates } from '../../tooling/agentConfigSql'
import { extractSqliteGroupedResult } from '../../tooling/sqliteDisplay'
import { AgentConfigUpdateDetail } from '../toolDetails'
import type { ToolEntryDisplay } from './types'

function truncate(value: string, max = 60): string {
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

export function buildSqliteSyntheticId(baseId: string, suffix: string, index: number): string {
  return `${baseId}:sqlite:${String(index).padStart(3, '0')}:${suffix}`
}

export function buildAgentConfigEntry(
  clusterCursor: string,
  entry: ToolCallEntry,
  statements: string[],
  statementIndexes: number[],
): ToolEntryDisplay | null {
  const parsedUpdate = parseAgentConfigUpdates(statements)
  if (!parsedUpdate) {
    return null
  }

  const {
    updatesCharter,
    updatesSchedule,
    charterValue,
    scheduleValue,
    scheduleCleared,
  } = parsedUpdate
  const scheduleKnown = scheduleCleared || scheduleValue !== null
  const normalizedSchedule = scheduleCleared ? null : scheduleValue
  const scheduleSummary = scheduleKnown ? summarizeSchedule(normalizedSchedule) : null
  const scheduleCaption = scheduleCleared
    ? 'Disabled'
    : scheduleSummary ?? 'Schedule updated'
  const scheduleSummaryText = scheduleCleared
    ? 'Schedule disabled.'
    : scheduleSummary
      ? `Schedule set to ${scheduleSummary}.`
      : 'Schedule updated.'

  let label = 'Database query'
  let caption: string | null = null
  let summary: string | null = null
  let icon = Workflow
  let iconBgClass = 'bg-indigo-100'
  let iconColorClass = 'text-indigo-600'

  if (updatesCharter && updatesSchedule) {
    label = 'Assignment and schedule updated'
    caption = `Assignment updated • ${scheduleCleared ? 'Schedule disabled' : scheduleSummary ?? 'Schedule updated'}`
    summary = scheduleCleared
      ? 'Assignment updated. Schedule disabled.'
      : scheduleSummary
        ? `Assignment updated. Schedule set to ${scheduleSummary}.`
        : 'Assignment and schedule updated.'
  } else if (updatesCharter) {
    label = 'Assignment updated'
    caption = charterValue ? truncate(charterValue, 48) : 'Assignment updated'
    summary = 'Assignment updated.'
    icon = FileCheck2
  } else if (updatesSchedule) {
    label = 'Schedule updated'
    caption = scheduleCaption
    summary = scheduleSummaryText
    icon = CalendarClock
    iconBgClass = 'bg-sky-100'
    iconColorClass = 'text-sky-600'
  }

  return {
    id: buildSqliteSyntheticId(entry.id, 'agent-config', Math.min(...statementIndexes)),
    clusterCursor,
    cursor: entry.cursor,
    toolName: entry.toolName ?? 'sqlite_batch',
    label,
    caption,
    timestamp: entry.timestamp ?? null,
    status: entry.status ?? null,
    icon,
    iconBgClass,
    iconColorClass,
    parameters:
      entry.parameters && typeof entry.parameters === 'object' && !Array.isArray(entry.parameters)
        ? (entry.parameters as Record<string, unknown>)
        : null,
    rawParameters: entry.parameters,
    result: extractSqliteGroupedResult(entry.result, statementIndexes),
    summary,
    charterText: charterValue ?? null,
    sqlStatements: statements,
    detailComponent: AgentConfigUpdateDetail,
    meta: entry.meta,
    sourceEntry: entry,
    separateFromPreview: true,
  }
}
