import { Brain, LayoutGrid, Waypoints } from 'lucide-react'
import { resolveDetailComponent } from '../toolDetails'
import { isPlainObject, parseResultObject } from '../../../util/objectUtils'
import { compareTimelineCursors } from '../../../util/timelineCursor'
import type { KanbanEvent, ThinkingEvent, ToolCallEntry, ToolClusterEvent } from '../../../types/agentChat'
import type {
  ToolClusterTransform,
  ToolDescriptor,
  ToolEntryDisplay,
} from './types'
import {
  CHAT_SKIP_TOOL_NAMES,
  buildToolDescriptorMap,
  coerceString,
  getFriendlyToolInfo,
  truncate,
} from '../../tooling/toolMetadata'
import { classifySqliteStatements } from '../../tooling/agentConfigSql'
import {
  extractSqlStatementsFromParameters,
  extractSqliteStatementResult,
  extractSqliteGroupedResult,
  getSqliteInternalTableDisplay,
} from '../../tooling/sqliteDisplay'
import { ThinkingDetail } from '../toolDetails/details/common'
import { KanbanUpdateDetail } from '../toolDetails/details/kanban'
import { buildAgentConfigEntry, buildSqliteSyntheticId } from './sqliteEntries'

const TOOL_DESCRIPTORS = buildToolDescriptorMap(resolveDetailComponent)

function compareEntryOrder(left: ToolEntryDisplay, right: ToolEntryDisplay): number {
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

function toTitleCase(value: string): string {
  return value
    .split(/[\s_\-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function pickString(value: unknown): string | null {
  if (typeof value === 'string' && value.trim().length) {
    return value.trim()
  }
  return null
}

function deriveMcpInfo(toolName: string | null | undefined, rawResult: unknown): ToolEntryDisplay['mcpInfo'] | null {
  if (!toolName) {
    return null
  }

  const normalizedName = toolName.trim()
  const lower = normalizedName.toLowerCase()

  let serverSlug: string | null = null
  let toolId: string | null = null

  if (lower.startsWith('mcp_')) {
    const remainder = normalizedName.slice(4)
    const parts = remainder.split('_')
    if (parts.length > 1) {
      serverSlug = parts[0]
      toolId = parts.slice(1).join('_')
    }
  }

  const resultObject = parseResultObject(rawResult)
  if (!serverSlug && resultObject) {
    serverSlug =
      pickString(resultObject['server']) ??
      pickString(resultObject['server_name']) ??
      pickString(resultObject['provider']) ??
      (isPlainObject(resultObject['metadata'])
        ? pickString((resultObject['metadata'] as Record<string, unknown>)['server'])
        : null)
  }

  if (!toolId && resultObject) {
    toolId =
      pickString(resultObject['tool']) ??
      pickString(resultObject['tool_name']) ??
      pickString(resultObject['toolName']) ??
      (isPlainObject(resultObject['metadata'])
        ? pickString((resultObject['metadata'] as Record<string, unknown>)['tool'])
        : null)
  }

  if (!toolId) {
    toolId = normalizedName.startsWith('mcp_') && serverSlug
      ? normalizedName.slice(4 + serverSlug.length + 1)
      : normalizedName
  }

  if (!serverSlug) {
    return null
  }

  return {
    serverSlug,
    serverLabel: toTitleCase(serverSlug),
    toolId,
    toolLabel: toTitleCase(toolId),
  }
}

function descriptorFor(toolName: string | null | undefined): ToolDescriptor {
  const normalized = (toolName ?? '').toLowerCase()
  return TOOL_DESCRIPTORS.get(normalized) || TOOL_DESCRIPTORS.get('default')!
}

function deriveCaptionFallback(parameters: Record<string, unknown> | null): string | null {
  if (!parameters) return null
  const summary = coerceString((parameters as Record<string, unknown>).summary)
  if (summary) return truncate(summary, 60)
  return null
}

function deriveCustomToolSummary(entry: ToolCallEntry): Pick<ToolEntryDisplay, 'label' | 'caption' | 'summary' | 'detailComponent' | 'icon' | 'iconBgClass' | 'iconColorClass'> {
  const friendly = getFriendlyToolInfo(entry.toolName ?? 'custom_tool')
  const resultObject = parseResultObject(entry.result)
  const nestedResult =
    resultObject && isPlainObject(resultObject['result'])
      ? (resultObject['result'] as Record<string, unknown>)
      : null
  const resultMessage = nestedResult ? pickString(nestedResult['message']) : null
  const topLevelMessage = resultObject ? pickString(resultObject['message']) : null
  const summary = topLevelMessage ?? resultMessage ?? null

  return {
    label: friendly.label,
    caption: 'Custom Tool Call',
    summary: summary ? truncate(summary, 120) : null,
    detailComponent: resolveDetailComponent('customToolRun'),
    icon: friendly.icon,
    iconBgClass: friendly.iconBgClass,
    iconColorClass: friendly.iconColorClass,
  }
}

function buildToolEntryDisplay(
  clusterCursor: string,
  entry: ToolCallEntry,
  descriptor: ToolDescriptor,
): ToolEntryDisplay | null {
  const toolName = entry.toolName ?? entry.meta?.label ?? 'tool'
  if (descriptor.skip) {
    return null
  }

  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  // Timeline captions come from backend step descriptions and are often debug-oriented.
  // For the end-user chat UI, derive captions from structured fields and known tool metadata instead.
  const entryForDerive: ToolCallEntry = {
    ...entry,
    caption: null,
    summary: undefined,
  }
  const transform = descriptor.derive?.(entryForDerive, parameters) || {}
  const customToolTransform =
    descriptor.name === 'default' && toolName.toLowerCase().startsWith('custom_')
      ? deriveCustomToolSummary(entry)
      : null
  const mergedTransform = customToolTransform ? { ...transform, ...customToolTransform } : transform

  // Check if the derive function requested this entry be skipped (e.g., kanban-only SQL)
  if (mergedTransform.skip) {
    return null
  }

  const isDefaultDescriptor = descriptor.name === 'default'
  const derivedCaption = mergedTransform.caption ?? deriveCaptionFallback(parameters)
  // Backend captions are often debug-oriented; only use them as a last-resort fallback when
  // we don't recognize the tool (default descriptor) or don't have structured parameters.
  const backendCaption = pickString(entry.caption)
  const caption = derivedCaption ?? ((isDefaultDescriptor || !parameters) ? backendCaption : null)
  const mcpInfo = deriveMcpInfo(toolName, entry.result)

  const baseLabel = mergedTransform.label ?? descriptor.label
  const baseCaption = caption ?? descriptor.label
  const detailComponent = mergedTransform.detailComponent ?? descriptor.detailComponent
  const shouldUseGenericMcpDisplay = Boolean(mcpInfo && isDefaultDescriptor)

  const finalLabel = shouldUseGenericMcpDisplay ? 'MCP Tool' : baseLabel
  const finalCaption =
    shouldUseGenericMcpDisplay && (mcpInfo?.serverLabel || mcpInfo?.toolLabel)
      ? [mcpInfo?.serverLabel, mcpInfo?.toolLabel].filter(Boolean).join(' • ')
      : baseCaption
  const finalDetailComponent = shouldUseGenericMcpDisplay ? resolveDetailComponent('mcpTool') : detailComponent
  const finalIcon = shouldUseGenericMcpDisplay ? Waypoints : transform.icon ?? descriptor.icon

  return {
    id: entry.id,
    clusterCursor,
    cursor: entry.cursor,
    toolName,
    label: finalLabel,
    caption: finalCaption,
    timestamp: entry.timestamp ?? null,
    status: entry.status ?? null,
    icon: shouldUseGenericMcpDisplay ? finalIcon : mergedTransform.icon ?? finalIcon,
    iconBgClass: mergedTransform.iconBgClass ?? descriptor.iconBgClass,
    iconColorClass: mergedTransform.iconColorClass ?? descriptor.iconColorClass,
    parameters,
    rawParameters: entry.parameters,
    result: entry.result,
    summary: mergedTransform.summary ?? entry.summary ?? null,
    charterText: mergedTransform.charterText ?? entry.charterText ?? null,
    sqlStatements: mergedTransform.sqlStatements ?? entry.sqlStatements,
    detailComponent: finalDetailComponent,
    meta: entry.meta,
    sourceEntry: entry,
    mcpInfo: mcpInfo ?? undefined,
    separateFromPreview: mergedTransform.separateFromPreview ?? false,
    sqliteInfo: mergedTransform.sqliteInfo,
  }
}

function buildSqliteEntries(clusterCursor: string, entry: ToolCallEntry): ToolEntryDisplay[] {
  const sqliteToolName = entry.toolName ?? entry.meta?.label ?? 'sqlite_batch'
  const descriptor = descriptorFor(sqliteToolName)
  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const statements = extractSqlStatementsFromParameters(parameters)
  if (!statements.length) {
    const fallback = buildToolEntryDisplay(clusterCursor, entry, descriptor)
    return fallback ? [fallback] : []
  }

  const classifications = classifySqliteStatements(statements)
  const configStatementIndexes = classifications
    .filter((classification) => classification.reservedTableKind === 'agentConfig')
    .map((classification) => classification.index)
  const kanbanStatementIndexes = new Set(
    classifications
      .filter((classification) => classification.reservedTableKind === 'kanban')
      .map((classification) => classification.index),
  )
  const nonKanbanCount = classifications.filter((classification) => !kanbanStatementIndexes.has(classification.index)).length

  if (nonKanbanCount === 0) {
    return []
  }

  const entries: ToolEntryDisplay[] = []
  let handledAgentConfigIndexes = new Set<number>()
  if (configStatementIndexes.length) {
    const configStatements = configStatementIndexes.map((index) => statements[index]).filter(Boolean)
    const configEntry = buildAgentConfigEntry(clusterCursor, entry, configStatements, configStatementIndexes)
    if (configEntry) {
      entries.push(configEntry)
      handledAgentConfigIndexes = new Set(configStatementIndexes)
    }
  }

  const leftoverStatements: string[] = []
  const leftoverIndexes: number[] = []

  for (const classification of classifications) {
    if (kanbanStatementIndexes.has(classification.index) || handledAgentConfigIndexes.has(classification.index)) {
      continue
    }

    if (!classification.internalTableKind || !classification.tableName) {
      leftoverStatements.push(classification.statement)
      leftoverIndexes.push(classification.index)
      continue
    }

    const display = getSqliteInternalTableDisplay(
      classification.internalTableKind,
      classification.operation,
      classification.statement,
      extractSqliteStatementResult(entry.result, classification.index),
    )

    entries.push({
      id: buildSqliteSyntheticId(entry.id, display.tableName.replace(/^_+/, ''), classification.index),
      clusterCursor,
      cursor: entry.cursor,
      toolName: entry.toolName ?? 'sqlite_batch',
      label: display.label,
      caption: display.caption,
      timestamp: entry.timestamp ?? null,
      status: entry.status ?? null,
      icon: display.icon,
      iconBgClass: display.iconBgClass,
      iconColorClass: display.iconColorClass,
      parameters,
      rawParameters: entry.parameters,
      result: extractSqliteStatementResult(entry.result, classification.index),
      summary: display.summary,
      charterText: null,
      sqlStatements: [classification.statement],
      detailComponent: resolveDetailComponent(display.detailKind),
      meta: entry.meta,
      sourceEntry: entry,
      sqliteInfo: {
        kind: classification.internalTableKind,
        tableName: display.tableName,
        operation: classification.operation,
        operationLabel: display.operationLabel,
        purpose: display.purpose,
        instructionsText: display.instructionsText,
        statementIndex: classification.index,
      },
    })
  }

  if (leftoverStatements.length) {
    entries.push({
      id: buildSqliteSyntheticId(entry.id, 'generic', statements.length),
      clusterCursor,
      cursor: entry.cursor,
      toolName: entry.toolName ?? 'sqlite_batch',
      label: descriptor.label,
      caption: leftoverStatements.length === 1
        ? '1 statement'
        : `${leftoverStatements.length} statements`,
      timestamp: entry.timestamp ?? null,
      status: entry.status ?? null,
      icon: descriptor.icon,
      iconBgClass: descriptor.iconBgClass,
      iconColorClass: descriptor.iconColorClass,
      parameters,
      rawParameters: entry.parameters,
      result: extractSqliteGroupedResult(entry.result, leftoverIndexes),
      summary: entry.summary ?? null,
      charterText: null,
      sqlStatements: leftoverStatements,
      detailComponent: descriptor.detailComponent,
      meta: entry.meta,
      sourceEntry: entry,
    })
  }

  return entries
}

function buildToolEntries(clusterCursor: string, entry: ToolCallEntry): ToolEntryDisplay[] {
  const toolName = entry.toolName ?? entry.meta?.label ?? 'tool'
  const normalizedName = (toolName || '').toLowerCase()
  if (CHAT_SKIP_TOOL_NAMES.has(normalizedName as string)) {
    return []
  }

  if (normalizedName === 'sqlite_batch') {
    return buildSqliteEntries(clusterCursor, entry)
  }

  const descriptor = descriptorFor(toolName)
  const transformed = buildToolEntryDisplay(clusterCursor, entry, descriptor)
  return transformed ? [transformed] : []
}

function buildThinkingEntry(
  clusterCursor: string,
  entry: ThinkingEvent,
  suppressedThinkingCursor?: string | null,
): ToolEntryDisplay | null {
  const reasoning = entry.reasoning?.trim() || ''
  if (!reasoning) {
    return null
  }
  if (suppressedThinkingCursor && entry.cursor === suppressedThinkingCursor) {
    return null
  }
  return {
    id: `thinking:${entry.cursor}`,
    clusterCursor,
    cursor: entry.cursor,
    toolName: 'thinking',
    label: 'Thinking',
    caption: null,
    timestamp: entry.timestamp ?? null,
    icon: Brain,
    iconBgClass: 'bg-indigo-100',
    iconColorClass: 'text-indigo-600',
    parameters: null,
    rawParameters: null,
    result: reasoning,
    summary: null,
    charterText: null,
    sqlStatements: undefined,
    detailComponent: ThinkingDetail,
    meta: undefined,
    sourceEntry: undefined,
  }
}

function buildKanbanEntry(clusterCursor: string, entry: KanbanEvent): ToolEntryDisplay | null {
  if (!entry?.cursor) {
    return null
  }
  return {
    id: `kanban:${entry.cursor}`,
    clusterCursor,
    cursor: entry.cursor,
    toolName: 'kanban',
    label: 'Kanban update',
    caption: entry.displayText || 'Kanban update',
    timestamp: entry.timestamp ?? null,
    icon: LayoutGrid,
    iconBgClass: 'bg-amber-100',
    iconColorClass: 'text-amber-700',
    parameters: null,
    rawParameters: entry,
    result: entry,
    summary: null,
    charterText: null,
    sqlStatements: undefined,
    detailComponent: KanbanUpdateDetail,
    meta: undefined,
    sourceEntry: undefined,
    separateFromPreview: true,
  }
}

export function transformToolCluster(
  cluster: ToolClusterEvent,
  options?: { suppressedThinkingCursor?: string | null },
): ToolClusterTransform {
  const entries: ToolEntryDisplay[] = []
  let skippedCount = 0
  const thinkingEntries = cluster.thinkingEntries ?? []
  const kanbanEntries = cluster.kanbanEntries ?? []
  const suppressedThinkingCursor = options?.suppressedThinkingCursor ?? null

  for (const entry of cluster.entries) {
    const transformedEntries = buildToolEntries(cluster.cursor, entry)
    if (!transformedEntries.length) {
      skippedCount += 1
      continue
    }
    entries.push(...transformedEntries)
  }

  for (const entry of thinkingEntries) {
    const transformed = buildThinkingEntry(cluster.cursor, entry, suppressedThinkingCursor)
    if (transformed) {
      entries.push(transformed)
    }
  }

  for (const entry of kanbanEntries) {
    const transformed = buildKanbanEntry(cluster.cursor, entry)
    if (transformed) {
      entries.push(transformed)
    }
  }

  entries.sort(compareEntryOrder)

  const entryCount = entries.length
  const collapsible = entryCount >= cluster.collapseThreshold

  return {
    cursor: cluster.cursor,
    entryCount,
    collapseThreshold: cluster.collapseThreshold,
    collapsible,
    entries,
    latestTimestamp: cluster.latestTimestamp,
    earliestTimestamp: cluster.earliestTimestamp,
    skippedCount,
  }
}

export function isClusterRenderable(cluster: ToolClusterTransform): boolean {
  return cluster.entries.length > 0
}
