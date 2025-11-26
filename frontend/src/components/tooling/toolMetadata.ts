import {
  Workflow,
  FileCheck2,
  CalendarClock,
  Database,
  DatabaseZap,
  ClipboardList,
  BrainCircuit,
  Search,
  Network,
  FileText,
  FilePen,
  Globe,
  ContactRound,
  Mail,
  MessageSquareText,
  MessageCircle,
  MessageSquareDot,
  BotMessageSquare,
  Webhook,
  KeyRound,
  ScanText,
  BrainCog,
  type LucideIcon,
} from 'lucide-react'
import { summarizeSchedule } from '../../util/schedule'
import { parseResultObject } from '../../util/objectUtils'
import type { ToolCallEntry } from '../agentChat/types'
import type { ToolDescriptor, ToolDescriptorTransform } from '../agentChat/tooling/types'
import { summarizeToolSearchForCaption } from '../agentChat/tooling/searchUtils'

const COMMUNICATION_TOOL_NAMES = [
  'send_email',
  'send_sms',
  'send_web_message',
  'send_chat_message',
  'send_agent_message',
] as const

const BASE_SKIP_TOOL_NAMES = ['sleep', 'sleep_until_next_trigger', 'action', '', null] as const

export const CHAT_SKIP_TOOL_NAMES = new Set<string | null>([
  ...COMMUNICATION_TOOL_NAMES,
  ...BASE_SKIP_TOOL_NAMES,
])

export const USAGE_SKIP_TOOL_NAMES = new Set<string | null>(BASE_SKIP_TOOL_NAMES)

export const SKIP_TOOL_NAMES = CHAT_SKIP_TOOL_NAMES

export type ToolMetadataConfig = {
  name: string
  aliases?: string[]
  label: string
  icon: LucideIcon
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
    icon: FileCheck2,
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
    icon: CalendarClock,
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
    icon: Database,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-600',
    detailKind: 'sqliteBatch',
    derive(_, parameters) {
      const queriesParam = parameters?.queries
      let rawQueries: unknown[] = [];
      if (queriesParam) {
        rawQueries = Array.isArray(queriesParam) ? queriesParam : [queriesParam];
      } else if (Array.isArray(parameters?.operations)) {
        // Fallback for backward compatibility with older tool calls
        rawQueries = parameters.operations;
      }

      return {
        caption: rawQueries.length ? `${rawQueries.length} statement${rawQueries.length === 1 ? '' : 's'}` : 'SQL batch',
        sqlStatements: rawQueries.map(String),
      }
    },
  },
  {
    name: 'enable_database',
    label: 'Database enabled',
    icon: DatabaseZap,
    iconBgClass: 'bg-emerald-50',
    iconColorClass: 'text-emerald-600',
    detailKind: 'enableDatabase',
    derive(entry) {
      const resultObject = parseResultObject(entry.result)
      const messageValue = resultObject?.['message']
      const statusValue = resultObject?.['status']
      const managerValue = resultObject?.['tool_manager']

      const message = coerceString(messageValue)
      const status = coerceString(statusValue)
      const manager =
        managerValue && typeof managerValue === 'object' && !Array.isArray(managerValue)
          ? (managerValue as Record<string, unknown>)
          : null

      const toStringList = (value: unknown): string[] => {
        if (!Array.isArray(value)) return []
        return (value as unknown[])
          .map((item) => (typeof item === 'string' && item.trim().length > 0 ? item : null))
          .filter((item): item is string => Boolean(item))
      }

      const enabledList = toStringList(manager?.['enabled'])
      const alreadyEnabledList = toStringList(manager?.['already_enabled'])

      const summaryPieces: string[] = []
      if (status) {
        summaryPieces.push(status === 'ok' ? 'Enabled' : status)
      }
      if (enabledList.length) {
        summaryPieces.push(`Enabled: ${enabledList.join(', ')}`)
      } else if (alreadyEnabledList.length) {
        summaryPieces.push(`Already enabled: ${alreadyEnabledList.join(', ')}`)
      }

      const summaryText = summaryPieces.length ? truncate(summaryPieces.join(' • '), 96) : null
      const label = message && /already/i.test(message) ? 'Database already enabled' : 'Database enabled'

      return {
        label,
        caption: message ? truncate(message, 56) : entry.caption ?? label,
        summary: summaryText ?? message ?? entry.summary ?? null,
      }
    },
  },
  {
    name: 'api_task',
    label: 'API task',
    icon: ClipboardList,
    iconBgClass: 'bg-slate-100',
    iconColorClass: 'text-slate-600',
    detailKind: 'default',
    derive() {
      return {
        caption: 'Agentless task triggered via API',
      }
    },
  },
  {
    name: 'agent_runtime',
    label: 'Agent runtime',
    icon: BrainCircuit,
    iconBgClass: 'bg-slate-100',
    iconColorClass: 'text-slate-600',
    detailKind: 'default',
    derive() {
      return {
        caption: 'Internal agent workflow and reasoning',
      }
    },
  },
  {
    name: 'search_tools',
    aliases: ['search_web', 'web_search', 'search'],
    label: 'Web search',
    icon: Search,
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
    icon: Network,
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
    icon: FileText,
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
    icon: FilePen,
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
    icon: Globe,
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
    icon: ContactRound,
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
    name: 'send_email',
    label: 'Email sent',
    icon: Mail,
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const subject = coerceString(parameters?.['subject'])
      const toAddress = coerceString(parameters?.['to_address']) || coerceString(parameters?.['to'])
      const ccRaw = parameters?.['cc_addresses']
      const ccEntries = Array.isArray(ccRaw)
        ? (ccRaw as unknown[])
            .map((value) => coerceString(value))
            .filter((value): value is string => Boolean(value))
        : []

      const summaryParts: string[] = []
      if (toAddress) {
        summaryParts.push(`To ${toAddress}`)
      }
      if (ccEntries.length) {
        summaryParts.push(`CC ${ccEntries.join(', ')}`)
      }

      const caption = subject ? truncate(subject, 56) : toAddress ? `Email to ${truncate(toAddress, 42)}` : null
      const summaryText = summaryParts.length ? truncate(summaryParts.join(' • '), 96) : entry.summary ?? null

      return {
        caption: caption ?? entry.caption ?? 'Email sent',
        summary: summaryText,
      }
    },
  },
  {
    name: 'send_sms',
    label: 'SMS sent',
    icon: MessageSquareText,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const toNumber = coerceString(parameters?.['to_number'])
      const body = coerceString(parameters?.['body'])

      const caption = body ? truncate(body, 56) : toNumber ? `SMS to ${truncate(toNumber, 42)}` : null
      const summaryParts: string[] = []
      if (toNumber) {
        summaryParts.push(`To ${toNumber}`)
      }

      const ccRaw = parameters?.['cc_numbers']
      const ccList = Array.isArray(ccRaw)
        ? (ccRaw as unknown[])
            .map((value) => coerceString(value))
            .filter((value): value is string => Boolean(value))
        : []
      if (ccList.length) {
        summaryParts.push(`CC ${ccList.join(', ')}`)
      }

      return {
        caption: caption ?? entry.caption ?? 'SMS sent',
        summary: summaryParts.length ? truncate(summaryParts.join(' • '), 96) : entry.summary ?? null,
      }
    },
  },
  {
    name: 'send_web_message',
    label: 'Web message sent',
    icon: MessageCircle,
    iconBgClass: 'bg-violet-100',
    iconColorClass: 'text-violet-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const body = coerceString(parameters?.['body']) || coerceString(parameters?.['message'])
      const recipient =
        coerceString(parameters?.['to_address']) ||
        coerceString(parameters?.['to']) ||
        coerceString(parameters?.['recipient'])

      const caption = body ? truncate(body, 56) : recipient ? `Web message to ${truncate(recipient, 42)}` : null
      const summary = recipient ? truncate(`To ${recipient}`, 96) : entry.summary ?? null

      return {
        caption: caption ?? entry.caption ?? 'Web message sent',
        summary,
      }
    },
  },
  {
    name: 'send_chat_message',
    label: 'Chat message sent',
    icon: MessageSquareDot,
    iconBgClass: 'bg-sky-100',
    iconColorClass: 'text-sky-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const body = coerceString(parameters?.['body'])
      const toAddress = coerceString(parameters?.['to_address'])

      const caption = body ? truncate(body, 56) : toAddress ? `Chat to ${truncate(toAddress, 42)}` : null
      const summary = toAddress ? truncate(`To ${toAddress}`, 96) : entry.summary ?? null

      return {
        caption: caption ?? entry.caption ?? 'Chat message sent',
        summary,
      }
    },
  },
  {
    name: 'send_agent_message',
    label: 'Peer message sent',
    icon: BotMessageSquare,
    iconBgClass: 'bg-amber-100',
    iconColorClass: 'text-amber-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const peerId = coerceString(parameters?.['peer_agent_id'])
      const message = coerceString(parameters?.['message'])

      const caption = message ? truncate(message, 56) : peerId ? `Message to ${truncate(peerId, 42)}` : null
      const summary = peerId ? truncate(`Peer agent ${peerId}`, 96) : entry.summary ?? null

      return {
        caption: caption ?? entry.caption ?? 'Peer message sent',
        summary,
      }
    },
  },
  {
    name: 'send_webhook_event',
    label: 'Webhook sent',
    icon: Webhook,
    iconBgClass: 'bg-orange-100',
    iconColorClass: 'text-orange-600',
    detailKind: 'default',
    derive(entry, parameters) {
      const resultData = parseResultObject(entry.result)
      const webhookName =
        coerceString(resultData?.['webhook_name']) || coerceString(parameters?.['webhook_id'])
      const statusValue = resultData?.['response_status']
      const status =
        typeof statusValue === 'number' || typeof statusValue === 'string' ? String(statusValue) : null

      const payload = parameters?.['payload']
      const payloadKeyCount =
        payload && typeof payload === 'object' && !Array.isArray(payload)
          ? Object.keys(payload as Record<string, unknown>).length
          : null

      const summaryParts: string[] = []
      if (webhookName) {
        summaryParts.push(webhookName)
      }
      if (status) {
        summaryParts.push(`Status ${status}`)
      }
      if (payloadKeyCount) {
        summaryParts.push(`${payloadKeyCount} field${payloadKeyCount === 1 ? '' : 's'}`)
      }

      const caption = webhookName ? `Webhook: ${truncate(webhookName, 40)}` : null

      return {
        caption: caption ?? entry.caption ?? 'Webhook triggered',
        summary: summaryParts.length ? truncate(summaryParts.join(' • '), 96) : entry.summary ?? null,
      }
    },
  },
  {
    name: 'secure_credentials_request',
    label: 'Credentials request',
    icon: KeyRound,
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
    icon: ScanText,
    iconBgClass: 'bg-fuchsia-100',
    iconColorClass: 'text-fuchsia-600',
    detailKind: 'brightDataSnapshot',
  },
  {
    name: 'think',
    aliases: ['reasoning'],
    label: 'Analysis',
    icon: BrainCog,
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
  icon: Workflow,
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
      icon: config.icon,
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
