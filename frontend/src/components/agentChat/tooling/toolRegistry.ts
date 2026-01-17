import { Brain, Waypoints } from 'lucide-react'
import { resolveDetailComponent } from '../toolDetails'
import { isPlainObject, parseResultObject } from '../../../util/objectUtils'
import type { ThinkingEvent, ToolCallEntry, ToolClusterEvent } from '../../../types/agentChat'
import type {
  ToolClusterTransform,
  ToolDescriptor,
  ToolEntryDisplay,
} from './types'
import {
  CHAT_SKIP_TOOL_NAMES,
  buildToolDescriptorMap,
  coerceString,
  truncate,
} from '../../tooling/toolMetadata'
import { ThinkingDetail } from '../toolDetails/details/common'

const TOOL_DESCRIPTORS = buildToolDescriptorMap(resolveDetailComponent)

type ParsedTimelineCursor = {
  value: number
  kind: string
  identifier: string
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

function deriveCaptionFallback(entry: ToolCallEntry, parameters: Record<string, unknown> | null): string | null {
  if (entry.caption) return entry.caption
  if (!parameters) return null
  const summary = coerceString((parameters as Record<string, unknown>).summary)
  if (summary) return truncate(summary, 60)
  return null
}

function buildToolEntry(clusterCursor: string, entry: ToolCallEntry): ToolEntryDisplay | null {
  const toolName = entry.toolName ?? entry.meta?.label ?? 'tool'
  const normalizedName = (toolName || '').toLowerCase()
  if (CHAT_SKIP_TOOL_NAMES.has(normalizedName as string)) {
    return null
  }

  const descriptor = descriptorFor(toolName)
  if (descriptor.skip) {
    return null
  }

  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const transform = descriptor.derive?.(entry, parameters) || {}

  // Check if the derive function requested this entry be skipped (e.g., kanban-only SQL)
  if (transform.skip) {
    return null
  }

  const caption = transform.caption ?? deriveCaptionFallback(entry, parameters)
  const mcpInfo = deriveMcpInfo(toolName, entry.result)
  const isDefaultDescriptor = descriptor.name === 'default'

  const baseLabel = transform.label ?? descriptor.label
  const baseCaption = caption ?? descriptor.label
  const detailComponent = transform.detailComponent ?? descriptor.detailComponent
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
    icon: finalIcon,
    iconBgClass: transform.iconBgClass ?? descriptor.iconBgClass,
    iconColorClass: transform.iconColorClass ?? descriptor.iconColorClass,
    parameters,
    rawParameters: entry.parameters,
    result: entry.result,
    summary: transform.summary ?? entry.summary ?? null,
    charterText: transform.charterText ?? entry.charterText ?? null,
    sqlStatements: transform.sqlStatements ?? entry.sqlStatements,
    detailComponent: finalDetailComponent,
    meta: entry.meta,
    sourceEntry: entry,
    mcpInfo: mcpInfo ?? undefined,
  }
}

function buildThinkingEntry(clusterCursor: string, entry: ThinkingEvent): ToolEntryDisplay | null {
  const reasoning = entry.reasoning?.trim() || ''
  if (!reasoning) {
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

export function transformToolCluster(cluster: ToolClusterEvent): ToolClusterTransform {
  const entries: ToolEntryDisplay[] = []
  let skippedCount = 0
  const thinkingEntries = cluster.thinkingEntries ?? []

  for (const entry of cluster.entries) {
    const transformed = buildToolEntry(cluster.cursor, entry)
    if (!transformed) {
      skippedCount += 1
      continue
    }
    entries.push(transformed)
  }

  for (const entry of thinkingEntries) {
    const transformed = buildThinkingEntry(cluster.cursor, entry)
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
