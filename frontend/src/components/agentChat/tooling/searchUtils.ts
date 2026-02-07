import type { ToolCallEntry } from '../../../types/agentChat'
import { toFriendlyToolName } from '../../tooling/toolMetadata'

export type NormalizedToolSuggestion = {
  name: string
  description?: string | null
  note?: string | null
  source?: string | null
}

export type ExternalResource = {
  name: string
  description: string | null
  url: string
}

export type ToolSearchOutcome = {
  status: string | null
  message: string | null
  toolCount: number | null
  tools: NormalizedToolSuggestion[]
  enabledTools: string[]
  alreadyEnabledTools: string[]
  evictedTools: string[]
  invalidTools: string[]
  externalResources: ExternalResource[]
}

const EMPTY_OUTCOME: ToolSearchOutcome = {
  status: null,
  message: null,
  toolCount: null,
  tools: [],
  enabledTools: [],
  alreadyEnabledTools: [],
  evictedTools: [],
  invalidTools: [],
  externalResources: [],
}

function softenToolTerminology(value: string): string {
  let output = value.replace(/No MCP tools available/gi, 'No tools available right now')
  output = output.replace(/MCP tools/gi, 'tools')
  return output
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function cleanCopy(value: string | null): string | null {
  if (!value) {
    return null
  }
  return softenToolTerminology(value.trim())
}

function parseJsonMaybe(value: string): unknown {
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function collectStrings(value: unknown): string[] {
  if (!value) return []
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (isNonEmptyString(item)) {
          return softenToolTerminology(item.trim())
        }
        if (isRecord(item) && isNonEmptyString(item.name)) {
          return softenToolTerminology(item.name.trim())
        }
        return null
      })
      .filter((item): item is string => Boolean(item && item.length))
  }
  if (isNonEmptyString(value)) {
    return value
      .split(',')
      .map((item) => softenToolTerminology(item.trim()))
      .filter(Boolean)
  }
  return []
}

function normalizeToolSuggestion(value: unknown): NormalizedToolSuggestion | null {
  if (isNonEmptyString(value)) {
    return { name: softenToolTerminology(value.trim()) }
  }
  if (!isRecord(value)) {
    return null
  }
  const name =
    (isNonEmptyString(value.name) && value.name.trim()) ||
    (isNonEmptyString(value.full_name) && value.full_name.trim()) ||
    (isNonEmptyString(value.title) && value.title.trim()) ||
    null
  if (!name) {
    return null
  }
  const description = cleanCopy(
    (isNonEmptyString(value.description) && value.description.trim()) ||
      (isNonEmptyString(value.summary) && value.summary.trim()) ||
      null,
  )
  const note = cleanCopy(
    (isNonEmptyString((value as { note?: unknown }).note) &&
      ((value as { note: unknown }).note as string).trim()) ||
      (isNonEmptyString((value as { reason?: unknown }).reason) &&
        ((value as { reason: unknown }).reason as string).trim()) ||
      null,
  )
  const source =
    (isNonEmptyString((value as { server_name?: unknown }).server_name) &&
      ((value as { server_name: unknown }).server_name as string).trim()) ||
    (isNonEmptyString((value as { provider?: unknown }).provider) &&
      ((value as { provider: unknown }).provider as string).trim()) ||
    null
  return { name, description, note, source }
}

export function parseToolSearchResult(input: unknown): ToolSearchOutcome {
  if (input === null || input === undefined) {
    return { ...EMPTY_OUTCOME }
  }

  if (typeof input === 'string') {
    const trimmed = input.trim()
    if (!trimmed) {
      return { ...EMPTY_OUTCOME }
    }
    const parsed = parseJsonMaybe(trimmed)
    if (parsed !== trimmed) {
      return parseToolSearchResult(parsed)
    }
    return {
      ...EMPTY_OUTCOME,
      message: softenToolTerminology(trimmed),
    }
  }

  if (Array.isArray(input)) {
    const tools = input.map(normalizeToolSuggestion).filter((item): item is NormalizedToolSuggestion => Boolean(item))
    return {
      ...EMPTY_OUTCOME,
      tools,
      toolCount: tools.length,
    }
  }

  if (!isRecord(input)) {
    return { ...EMPTY_OUTCOME }
  }

  const status = cleanCopy(isNonEmptyString(input.status) ? input.status.trim() : null)
  const message = cleanCopy(isNonEmptyString(input.message) ? input.message.trim() : null)
  const toolsRaw = Array.isArray(input.tools) ? input.tools : null
  const tools = toolsRaw
    ? toolsRaw.map(normalizeToolSuggestion).filter((item): item is NormalizedToolSuggestion => Boolean(item))
    : []
  const toolCountField = input.tool_count ?? input.count ?? input.total
  const toolCountValue = typeof toolCountField === 'number' && Number.isFinite(toolCountField)
    ? toolCountField
    : null
  const toolCount = toolCountValue !== null ? toolCountValue : tools.length ? tools.length : null

  const enabledTools = collectStrings((input as { enabled_tools?: unknown }).enabled_tools ?? (input as { enabled?: unknown }).enabled)
  const alreadyEnabledTools = collectStrings((input as { already_enabled?: unknown }).already_enabled)
  const evictedTools = collectStrings((input as { evicted?: unknown }).evicted)
  const invalidTools = collectStrings((input as { invalid?: unknown }).invalid)

  // Parse external resources (APIs, websites, datasets)
  const externalResourcesRaw = (input as { external_resources?: unknown }).external_resources
  const externalResources: ExternalResource[] = []
  if (Array.isArray(externalResourcesRaw)) {
    for (const item of externalResourcesRaw) {
      if (isRecord(item) && isNonEmptyString(item.name) && isNonEmptyString(item.url)) {
        const url = item.url.trim()
        // Only include if URL looks valid
        if (url.startsWith('http://') || url.startsWith('https://')) {
          externalResources.push({
            name: item.name.trim(),
            description: isNonEmptyString(item.description) ? item.description.trim() : null,
            url,
          })
        }
      }
    }
  }

  return {
    status,
    message,
    toolCount,
    tools,
    enabledTools,
    alreadyEnabledTools,
    evictedTools,
    invalidTools,
    externalResources,
  }
}

export function summarizeToolSearchForCaption(entry: ToolCallEntry, query: string | null): { caption: string | null; summary: string | null } {
  const outcome = parseToolSearchResult(entry.result)
  const baseSummary = outcome.message || (isNonEmptyString(entry.summary) ? entry.summary.trim() : null)

  let caption: string | null = null
  if (outcome.toolCount !== null) {
    caption = outcome.toolCount === 0 ? 'No tools found' : `${outcome.toolCount} tool${outcome.toolCount === 1 ? '' : 's'} ready`
  }
  if (!caption && outcome.enabledTools.length) {
    const enabledPreview = outcome.enabledTools.slice(0, 2).map(toFriendlyToolName).join(', ')
    caption = outcome.enabledTools.length > 1 ? `Enabled ${outcome.enabledTools.length} tools` : `Enabled ${enabledPreview}`
  }
  if (!caption && query) {
    caption = `“${query}”`
  }
  if (!caption && baseSummary) {
    caption = baseSummary.length > 60 ? `${baseSummary.slice(0, 59)}…` : baseSummary
  }

  const summary = baseSummary

  return {
    caption,
    summary,
  }
}
