import { CalendarClock, FileCheck2, Workflow } from 'lucide-react'

import type { ToolCallEntry } from '../../../types/agentChat'
import { summarizeSchedule } from '../../../util/schedule'
import { parseAgentConfigUpdates } from '../../tooling/agentConfigSql'
import { parseAgentConfigUpdateConfirmation } from '../../tooling/agentConfigResult'
import { extractSqliteGroupedResult } from '../../tooling/sqliteDisplay'
import { AgentConfigUpdateDetail } from '../toolDetails'
import type { ToolDisplayOptions, ToolEntryDisplay } from './types'

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
  options: ToolDisplayOptions = {},
): ToolEntryDisplay | null {
  const parsedUpdate = parseAgentConfigUpdates(statements)
  const confirmation = entry.status === 'complete'
    ? parseAgentConfigUpdateConfirmation(entry.result)
    : null
  if (!parsedUpdate || !confirmation) {
    return null
  }

  const {
    charterValue,
    charterChange,
    scheduleValue,
    scheduleCleared,
  } = parsedUpdate
  const charterConfirmation = parsedUpdate.updatesCharter ? confirmation.charter ?? null : null
  const scheduleConfirmation = parsedUpdate.updatesSchedule ? confirmation.schedule ?? null : null
  const updatesCharter = charterConfirmation !== null
  const updatesSchedule = scheduleConfirmation !== null
  if (!updatesCharter && !updatesSchedule) {
    return null
  }

  const confirmedUpdate = {
    ...parsedUpdate,
    updatesCharter,
    updatesSchedule,
    charterValue: updatesCharter ? charterValue : null,
    charterChange: charterConfirmation === 'updated' ? charterChange : null,
  }
  const scheduleKnown = scheduleCleared || scheduleValue !== null
  const hasCharterSnapshot = typeof entry.charterText === 'string'
  const resolvedCharter = hasCharterSnapshot ? entry.charterText : charterValue
  const charterCaption = resolvedCharter?.trim() || charterChange?.replacementText?.trim() || null
  const normalizedSchedule = scheduleCleared ? null : scheduleValue
  const scheduleSummary = scheduleKnown ? summarizeSchedule(normalizedSchedule, { timeZone: options.timeZone }) : null
  const scheduleCaption = scheduleCleared
    ? 'Disabled'
    : scheduleSummary ?? 'Schedule updated'
  const scheduleSummaryText = scheduleCleared
    ? 'Schedule disabled.'
    : scheduleSummary
      ? `Schedule set to ${scheduleSummary}.`
      : 'Schedule updated.'
  const confirmedScheduleSummary = scheduleConfirmation === 'updated'
    ? scheduleSummaryText
    : 'Schedule already current.'

  let label = 'Database query'
  let caption: string | null = null
  let summary: string | null = null
  let icon = Workflow
  let iconBgClass = 'bg-indigo-100'
  let iconColorClass = 'text-indigo-600'

  if (updatesCharter && updatesSchedule) {
    const assignmentLabel = charterConfirmation === 'updated' ? 'Assignment updated' : 'Assignment already current'
    const scheduleLabel = scheduleConfirmation === 'updated' ? 'schedule updated' : 'schedule already current'
    label = `${assignmentLabel} and ${scheduleLabel}`
    caption = `${assignmentLabel} • ${scheduleCleared ? 'Schedule disabled' : scheduleSummary ?? scheduleLabel}`
    summary = `${assignmentLabel}. ${confirmedScheduleSummary}`
  } else if (updatesCharter) {
    label = charterConfirmation === 'updated' ? 'Assignment updated' : 'Assignment already current'
    caption = charterCaption
      ? truncate(charterCaption, 48)
      : hasCharterSnapshot || charterValue === ''
        ? 'Assignment cleared'
        : label
    summary = `${label}.`
    icon = FileCheck2
  } else if (updatesSchedule) {
    label = scheduleConfirmation === 'updated' ? 'Schedule updated' : 'Schedule already current'
    caption = scheduleCaption
    summary = confirmedScheduleSummary
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
    charterText: updatesCharter ? resolvedCharter ?? null : null,
    agentConfigUpdate: confirmedUpdate,
    agentConfigConfirmation: confirmation,
    sqlStatements: statements,
    detailComponent: AgentConfigUpdateDetail,
    meta: entry.meta,
    sourceEntry: entry,
    separateFromPreview: true,
  }
}
