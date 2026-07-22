import { CalendarClock, FileCheck2, Workflow } from 'lucide-react'

import type { ToolCallEntry } from '../../../types/agentChat'
import { parseResultObject } from '../../../util/objectUtils'
import { summarizeSchedule } from '../../../util/schedule'
import { parseAgentConfigCharterChange } from '../../tooling/agentConfigSql'
import { extractSqliteGroupedResult } from '../../tooling/sqliteDisplay'
import { AgentConfigUpdateDetail } from '../toolDetails'
import type { AgentConfigUpdateConfirmation, ToolDisplayOptions, ToolEntryDisplay } from './types'

function truncate(value: string, max = 60): string {
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

function parseAgentConfigUpdateConfirmation(result: unknown): AgentConfigUpdateConfirmation | null {
  const resultObject = parseResultObject(result)
  const status = typeof resultObject?.status === 'string' ? resultObject.status.trim().toLowerCase() : ''
  const update = parseResultObject(resultObject?.agent_config_update)
  if (!update || ['error', 'failed', 'failure'].includes(status)) return null

  const updated = new Set(Array.isArray(update.updated_fields) ? update.updated_fields : [])
  const unchanged = new Set(Array.isArray(update.unchanged_fields) ? update.unchanged_fields : [])
  const errors = parseResultObject(update.errors)
  const confirmation: AgentConfigUpdateConfirmation = {}
  for (const field of ['charter', 'schedule'] as const) {
    if (!errors?.[field] && updated.has(field)) confirmation[field] = 'updated'
    else if (!errors?.[field] && unchanged.has(field)) confirmation[field] = 'unchanged'
  }
  return Object.keys(confirmation).length ? confirmation : null
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
  const confirmation = entry.status === 'complete'
    ? parseAgentConfigUpdateConfirmation(entry.result)
    : null
  if (!confirmation) {
    return null
  }

  const charterConfirmation = confirmation.charter ?? null
  const scheduleConfirmation = confirmation.schedule ?? null
  const updatesCharter = charterConfirmation !== null
  const updatesSchedule = scheduleConfirmation !== null
  if (!updatesCharter && !updatesSchedule) {
    return null
  }

  const charterChange = charterConfirmation === 'updated'
    ? parseAgentConfigCharterChange(statements)
    : null
  const scheduleKnown = entry.scheduleValue !== undefined
  const hasCharterSnapshot = typeof entry.charterText === 'string'
  const resolvedCharter = hasCharterSnapshot ? entry.charterText : null
  const charterCaption = resolvedCharter?.trim() || charterChange?.replacementText?.trim() || null
  const scheduleSummary = scheduleKnown
    ? summarizeSchedule(entry.scheduleValue ?? null, { timeZone: options.timeZone })
    : null
  const scheduleCaption = scheduleKnown && entry.scheduleValue === null
    ? 'Disabled'
    : scheduleSummary ?? 'Schedule updated'
  const scheduleSummaryText = scheduleKnown && entry.scheduleValue === null
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
    caption = `${assignmentLabel} • ${scheduleKnown && entry.scheduleValue === null ? 'Schedule disabled' : scheduleSummary ?? scheduleLabel}`
    summary = `${assignmentLabel}. ${confirmedScheduleSummary}`
  } else if (updatesCharter) {
    label = charterConfirmation === 'updated' ? 'Assignment updated' : 'Assignment already current'
    caption = charterCaption
      ? truncate(charterCaption, 48)
      : hasCharterSnapshot
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
    scheduleValue: updatesSchedule ? entry.scheduleValue : undefined,
    agentConfigCharterChange: charterChange,
    agentConfigConfirmation: confirmation,
    sqlStatements: statements,
    detailComponent: AgentConfigUpdateDetail,
    meta: entry.meta,
    sourceEntry: entry,
    separateFromPreview: true,
  }
}
