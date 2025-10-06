import { summarizeSchedule } from '../../util/schedule'
import { parseResultObject } from '../../util/objectUtils'
import type { ToolCallEntry } from '../agentChat/types'
import type { ToolDescriptor, ToolDescriptorTransform } from '../agentChat/tooling/types'
import { summarizeToolSearchForCaption } from '../agentChat/tooling/searchUtils'

export const SKIP_TOOL_NAMES = new Set([
  'send_email',
  'send_sms',
  'send_web_message',
  'send_chat_message',
  'send_agent_message',
  'sleep',
  'sleep_until_next_trigger',
  'action',
  '',
  null,
])

export type ToolMetadataConfig = {
  name: string
  aliases?: string[]
  label: string
  iconPaths: string[]
  iconBgClass: string
  iconColorClass: string
  detailKind: string
  skip?: boolean
  derive?(entry: ToolCallEntry, parameters: Record<string, unknown> | null): ToolDescriptorTransform | void
}

export function truncate(value: string, max = 60): string {
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

export function coerceString(value: unknown): string | null {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value
  }
  return null
}

export const TOOL_METADATA_CONFIGS: ToolMetadataConfig[] = [
  {
    name: 'update_charter',
    label: 'Assignment updated',
    iconPaths: [
      'M9 12h5M9 16h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
      'M9.5 13.75l2 2L17 10',
    ],
    iconBgClass: 'bg-indigo-100',
    iconColorClass: 'text-indigo-600',
    detailKind: 'updateCharter',
    derive(entry, parameters) {
      const charterText = coerceString(parameters?.new_charter) || coerceString(parameters?.charter) || coerceString(entry.result)
      return {
        charterText,
        caption: charterText ? truncate(charterText, 48) : entry.caption ?? 'Assignment updated',
      }
    },
  },
  {
    name: 'update_schedule',
    label: 'Schedule updated',
    iconPaths: [
      'M8 7V3m8 4V3m-9 8h10m-12 8h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
      'M15.5 16.5a3.5 3.5 0 11-7 0 3.5 3.5 0 017 0z',
      'M15.5 16.5L13.75 15.25',
    ],
    iconBgClass: 'bg-sky-100',
    iconColorClass: 'text-sky-600',
    detailKind: 'updateSchedule',
    derive(_, parameters) {
      const scheduleValue = coerceString(parameters?.new_schedule)
      const summary = summarizeSchedule(scheduleValue)
      return {
        caption: summary ?? (scheduleValue ? truncate(scheduleValue, 40) : 'Disabled'),
      }
    },
  },
  {
    name: 'sqlite_batch',
    label: 'Database query',
    iconPaths: [
      'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7',
      'M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4',
      'M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4',
    ],
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-600',
    detailKind: 'sqliteBatch',
    derive(_, parameters) {
      const operations = Array.isArray(parameters?.operations) ? (parameters?.operations as unknown[]) : []
      return {
        caption: operations.length ? `${operations.length} statement${operations.length === 1 ? '' : 's'}` : 'SQL batch',
        sqlStatements: operations.map(String),
      }
    },
  },
  {
    name: 'search_tools',
    aliases: ['search_web', 'web_search', 'search'],
    label: 'Web search',
    iconPaths: ['M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z'],
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-600',
    detailKind: 'search',
    derive(entry, parameters) {
      const rawQuery = coerceString(parameters?.query) || coerceString(parameters?.prompt)
      const truncatedQuery = rawQuery ? truncate(rawQuery, 48) : null
      const isToolSearch = entry.toolName?.toLowerCase() === 'search_tools'

      if (isToolSearch) {
        const { caption, summary } = summarizeToolSearchForCaption(entry, truncatedQuery)
        const safeCaption = caption ? truncate(caption, 56) : null
        return {
          label: 'Tool search',
          caption: safeCaption ?? (truncatedQuery ? `“${truncatedQuery}”` : 'Tool search'),
          summary,
        }
      }

      const fallbackCaption = truncatedQuery ? `“${truncatedQuery}”` : null
      return {
        label: 'Web search',
        caption: fallbackCaption ?? 'Search',
      }
    },
  },
  {
    name: 'api_call',
    aliases: ['http_request', 'http'],
    label: 'API request',
    iconPaths: ['M8 12h.01', 'M12 12h.01', 'M16 12h.01', 'M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z'],
    iconBgClass: 'bg-cyan-100',
    iconColorClass: 'text-cyan-600',
    detailKind: 'apiRequest',
    derive(_, parameters) {
      const url = coerceString(parameters?.url) || coerceString(parameters?.endpoint)
      const method = coerceString(parameters?.method)
      const captionPieces = [method ? method.toUpperCase() : null, url ? truncate(url, 36) : null].filter(Boolean)
      return {
        caption: captionPieces.length ? captionPieces.join(' • ') : 'API request',
      }
    },
  },
  {
    name: 'read_file',
    aliases: ['file_read'],
    label: 'File access',
    iconPaths: ['M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'],
    iconBgClass: 'bg-orange-100',
    iconColorClass: 'text-orange-600',
    detailKind: 'fileRead',
    derive(_, parameters) {
      const path = coerceString(parameters?.path) || coerceString(parameters?.file_path) || coerceString(parameters?.filename)
      return { caption: path ? truncate(path, 40) : 'Read file' }
    },
  },
  {
    name: 'write_file',
    aliases: ['file_write'],
    label: 'File update',
    iconPaths: ['M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z'],
    iconBgClass: 'bg-green-100',
    iconColorClass: 'text-green-600',
    detailKind: 'fileWrite',
    derive(_, parameters) {
      const path = coerceString(parameters?.path) || coerceString(parameters?.file_path) || coerceString(parameters?.filename)
      return { caption: path ? truncate(path, 40) : 'Update file' }
    },
  },
  {
    name: 'spawn_web_task',
    label: 'Browser task',
    iconPaths: ['M4 5h16', 'M4 9h16', 'M8 13h8', 'M8 17h5', 'M4 19h12a2 2 0 002-2V5a2 2 0 00-2-2H4a2 2 0 00-2 2v12a2 2 0 002 2z'],
    iconBgClass: 'bg-violet-100',
    iconColorClass: 'text-violet-600',
    detailKind: 'browserTask',
    derive(_, parameters) {
      let prompt = coerceString(parameters?.prompt)
      if (prompt?.toLowerCase().startsWith('task:')) {
        prompt = prompt.slice(5).trim()
      }
      return {
        caption: prompt ? truncate(prompt, 52) : null,
      }
    },
  },
  {
    name: 'request_contact_permission',
    label: 'Contact permission',
    iconPaths: [
      'M8.5 11A3.5 3.5 0 1112 7.5 3.5 3.5 0 018.5 11z',
      'M15.5 11A3.5 3.5 0 1119 7.5 3.5 3.5 0 0115.5 11z',
      'M5 18a5 5 0 015-5h0a5 5 0 014.472 2.778M19 21l-3-3 3-3 3 3-3 3z',
    ],
    iconBgClass: 'bg-rose-100',
    iconColorClass: 'text-rose-600',
    detailKind: 'contactPermission',
    derive(entry, parameters) {
      const result = parseResultObject(entry.result)
      const contactsRaw = parameters?.contacts
      const contacts = Array.isArray(contactsRaw) ? contactsRaw : []
      const createdCountRaw = result?.['created_count']
      const alreadyAllowedRaw = result?.['already_allowed_count']
      const alreadyPendingRaw = result?.['already_pending_count']
      const message = result ? coerceString(result['message']) : null
      const status = result ? coerceString(result['status']) : null
      const createdCount = typeof createdCountRaw === 'number' ? createdCountRaw : null
      const alreadyAllowedCount = typeof alreadyAllowedRaw === 'number' ? alreadyAllowedRaw : null
      const alreadyPendingCount = typeof alreadyPendingRaw === 'number' ? alreadyPendingRaw : null

      let caption: string | null = null
      if (createdCount && createdCount > 0) {
        caption = `Awaiting approval for ${createdCount} contact${createdCount === 1 ? '' : 's'}`
      } else if (contacts.length) {
        caption = `Requested permission for ${contacts.length} contact${contacts.length === 1 ? '' : 's'}`
      } else if (message) {
        caption = truncate(message, 48)
      } else if (status) {
        caption = status
      }

      const summaryPieces: string[] = []
      if (message) {
        summaryPieces.push(message)
      }
      if (createdCount && createdCount > 0) {
        summaryPieces.push(`Created: ${createdCount}`)
      }
      if (alreadyAllowedCount && alreadyAllowedCount > 0) {
        summaryPieces.push(`Already allowed: ${alreadyAllowedCount}`)
      }
      if (alreadyPendingCount && alreadyPendingCount > 0) {
        summaryPieces.push(`Already pending: ${alreadyPendingCount}`)
      }

      return {
        caption: caption ?? entry.caption ?? 'Contact permission',
        summary: summaryPieces.length ? summaryPieces.join(' • ') : entry.summary ?? null,
      }
    },
  },
  {
    name: 'secure_credentials_request',
    label: 'Credentials request',
    iconPaths: [
      'M8 11V7a4 4 0 118 0v4',
      'M5 11h14v10H5z',
      'M12 15v2',
    ],
    iconBgClass: 'bg-amber-100',
    iconColorClass: 'text-amber-600',
    detailKind: 'secureCredentials',
    derive(entry, parameters) {
      const result = parseResultObject(entry.result)
      const credentialsRaw = parameters?.credentials
      const credentials = Array.isArray(credentialsRaw) ? credentialsRaw : []
      const createdCountRaw = result?.['created_count']
      const message = result ? coerceString(result['message']) : null
      const status = result ? coerceString(result['status']) : null
      const errorsRaw = result && Array.isArray(result['errors']) ? (result['errors'] as unknown[]) : null
      const createdCount = typeof createdCountRaw === 'number' ? createdCountRaw : null
      const firstCredential = credentials.length ? (credentials[0] as Record<string, unknown>) : null
      const firstName = firstCredential ? coerceString(firstCredential['name']) : null

      let caption: string | null = null
      if (createdCount && createdCount > 0) {
        caption = `Awaiting ${createdCount} credential${createdCount === 1 ? '' : 's'}`
      } else if (firstName) {
        caption = `Requesting ${firstName}`
      } else if (credentials.length) {
        caption = `Requesting ${credentials.length} credential${credentials.length === 1 ? '' : 's'}`
      } else if (status) {
        caption = status
      }

      const summaryPieces: string[] = []
      if (message) {
        summaryPieces.push(message)
      }
      if (errorsRaw && errorsRaw.length) {
        summaryPieces.push(`Errors: ${errorsRaw.length}`)
      }

      const summaryText = summaryPieces.length ? truncate(summaryPieces.join(' • '), 120) : entry.summary ?? null

      return {
        caption: caption ?? entry.caption ?? 'Credentials request',
        summary: summaryText,
      }
    },
  },
  {
    name: 'mcp_brightdata_scrape_as_markdown',
    label: 'Web snapshot',
    iconPaths: ['M4 4h16v12H4z', 'M8 20h8', 'M10 16v4', 'M14 16v4'],
    iconBgClass: 'bg-fuchsia-100',
    iconColorClass: 'text-fuchsia-600',
    detailKind: 'brightDataSnapshot',
  },
  {
    name: 'think',
    aliases: ['reasoning'],
    label: 'Analysis',
    iconPaths: ['M9.663 17h4.673', 'M12 3v1', 'M18.364 5.636l-.707.707', 'M21 12h-1', 'M4 12H3', 'M6.343 6.343l-.707-.707', 'M9.172 15.243a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z'],
    iconBgClass: 'bg-yellow-100',
    iconColorClass: 'text-yellow-600',
    detailKind: 'analysis',
    derive(entry) {
      const summary = coerceString(entry.summary) || coerceString(entry.caption) || coerceString(entry.result)
      return {
        caption: summary ? truncate(summary, 64) : 'Analysis',
        summary,
      }
    },
  },
]

export const DEFAULT_TOOL_METADATA: ToolMetadataConfig = {
  name: 'default',
  label: 'Agent action',
  iconPaths: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  iconBgClass: 'bg-slate-100',
  iconColorClass: 'text-slate-600',
  detailKind: 'default',
}

const TOOL_METADATA_MAP: Map<string, ToolMetadataConfig> = (() => {
  const map = new Map<string, ToolMetadataConfig>()
  const register = (config: ToolMetadataConfig) => {
    map.set(config.name, config)
    config.aliases?.forEach((alias) => map.set(alias, config))
  }
  TOOL_METADATA_CONFIGS.forEach(register)
  register(DEFAULT_TOOL_METADATA)
  return map
})()

export function getSharedToolMetadata(toolName: string | null | undefined): ToolMetadataConfig {
  const normalized = (toolName ?? '').toLowerCase()
  return TOOL_METADATA_MAP.get(normalized) ?? DEFAULT_TOOL_METADATA
}

export function buildToolDescriptorMap(
  resolveDetailComponent: (detailKind: string) => ToolDescriptor['detailComponent'],
): Map<string, ToolDescriptor> {
  const map: Map<string, ToolDescriptor> = new Map()
  const register = (config: ToolMetadataConfig) => {
    const descriptor: ToolDescriptor = {
      name: config.name,
      aliases: config.aliases,
      label: config.label,
      iconPaths: config.iconPaths,
      iconBgClass: config.iconBgClass,
      iconColorClass: config.iconColorClass,
      skip: config.skip,
      derive: config.derive,
      detailComponent: resolveDetailComponent(config.detailKind),
    }
    map.set(config.name, descriptor)
    config.aliases?.forEach((alias) => map.set(alias, descriptor))
  }
  TOOL_METADATA_CONFIGS.forEach(register)
  register(DEFAULT_TOOL_METADATA)
  return map
}
