import { resolveDetailComponent } from '../toolDetails'
import { isPlainObject } from '../../../util/objectUtils'
import type { ToolCallEntry, ToolClusterEvent } from '../../../types/agentChat'
import type {
  ToolClusterTransform,
  ToolDescriptor,
  ToolEntryDisplay,
} from './types'
import {
  SKIP_TOOL_NAMES,
  buildToolDescriptorMap,
  coerceString,
  truncate,
} from '../../tooling/toolMetadata'

const TOOL_DESCRIPTORS = buildToolDescriptorMap(resolveDetailComponent)

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
  if (SKIP_TOOL_NAMES.has(normalizedName as string)) {
    return null
  }

  const descriptor = descriptorFor(toolName)
  if (descriptor.skip) {
    return null
  }

  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const transform = descriptor.derive?.(entry, parameters) || {}

  const caption = transform.caption ?? deriveCaptionFallback(entry, parameters)

  return {
    id: entry.id,
    clusterCursor,
    cursor: entry.cursor,
    toolName,
    label: transform.label ?? descriptor.label,
    caption: caption ?? descriptor.label,
    timestamp: entry.timestamp ?? null,
    iconPaths: transform.iconPaths ?? descriptor.iconPaths,
    iconBgClass: transform.iconBgClass ?? descriptor.iconBgClass,
    iconColorClass: transform.iconColorClass ?? descriptor.iconColorClass,
    parameters,
    rawParameters: entry.parameters,
    result: entry.result,
    summary: transform.summary ?? entry.summary ?? null,
    charterText: transform.charterText ?? entry.charterText ?? null,
    sqlStatements: transform.sqlStatements ?? entry.sqlStatements,
    detailComponent: transform.detailComponent ?? descriptor.detailComponent,
    meta: entry.meta,
    sourceEntry: entry,
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
