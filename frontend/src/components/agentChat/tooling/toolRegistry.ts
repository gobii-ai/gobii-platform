import { Waypoints } from 'lucide-react'
import { resolveDetailComponent } from '../toolDetails'
import { isPlainObject, parseResultObject } from '../../../util/objectUtils'
import type { ToolCallEntry, ToolClusterEvent } from '../../../types/agentChat'
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

const TOOL_DESCRIPTORS = buildToolDescriptorMap(resolveDetailComponent)

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

export function transformToolCluster(cluster: ToolClusterEvent): ToolClusterTransform {
  const entries: ToolEntryDisplay[] = []
  let skippedCount = 0

  for (const entry of cluster.entries) {
    const transformed = buildToolEntry(cluster.cursor, entry)
    if (!transformed) {
      skippedCount += 1
      continue
    }
    entries.push(transformed)
  }

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
