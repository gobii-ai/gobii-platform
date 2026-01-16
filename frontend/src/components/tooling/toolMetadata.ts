import {
  Workflow,
  FileCheck2,
  CalendarClock,
  Database,
  DatabaseZap,
  ShoppingBag,
  ClipboardList,
  BrainCircuit,
  Linkedin,
  Home,
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
  BarChart3,
  type LucideIcon,
} from 'lucide-react'
import { summarizeSchedule } from '../../util/schedule'
import { parseResultObject } from '../../util/objectUtils'
import type { ToolCallEntry } from '../agentChat/types'
import type { ToolDescriptor, ToolDescriptorTransform } from '../agentChat/tooling/types'
import { summarizeToolSearchForCaption } from '../agentChat/tooling/searchUtils'
import { AgentConfigUpdateDetail } from '../agentChat/toolDetails'
import { parseAgentConfigUpdates } from './agentConfigSql'
import { extractBrightDataArray, extractBrightDataResultCount, extractBrightDataSearchQuery } from './brightdata'

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

const LINKEDIN_ICON_BG_CLASS = 'bg-sky-100'
const LINKEDIN_ICON_COLOR_CLASS = 'text-sky-700'

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

function coerceNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]+/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function formatCount(value: number | null): string | null {
  if (value === null) return null
  return value.toLocaleString()
}

function pickFirstParameter(
  parameters: Record<string, unknown> | null | undefined,
  keys: string[],
): string | null {
  if (!parameters) return null
  for (const key of keys) {
    const value = coerceString(parameters[key])
    if (value) {
      return value
    }
  }
  return null
}

function deriveLinkedInCaption(
  parameters: Record<string, unknown> | null | undefined,
  keys: string[],
  fallback?: string | null,
): string | null {
  const value = pickFirstParameter(parameters, keys)
  if (value) {
    return truncate(value, 56)
  }
  return fallback ?? null
}


function deriveFileExport(
  entry: ToolCallEntry,
  parameters: Record<string, unknown> | null,
  fallbackLabel: string,
): ToolDescriptorTransform {
  const resultObject = parseResultObject(entry.result)
  const status = coerceString(resultObject?.['status'])
  const message = coerceString(resultObject?.['message'])
  const paramPath = coerceString(parameters?.['file_path']) || coerceString(parameters?.['path'])
  const filename = coerceString(resultObject?.['filename']) || paramPath || coerceString(parameters?.['filename'])
  const path = coerceString(resultObject?.['path']) || paramPath
  const isError = status?.toLowerCase() === 'error'

  const caption = message ? truncate(message, 56) : filename ? truncate(filename, 56) : path ? truncate(path, 56) : null
  const summaryParts: string[] = []
  if (path) {
    summaryParts.push(path)
  }
  if (filename && filename !== path) {
    summaryParts.push(filename)
  }

  return {
    label: isError ? `${fallbackLabel} failed` : fallbackLabel,
    caption: caption ?? entry.caption ?? fallbackLabel,
    summary: summaryParts.length ? truncate(summaryParts.join(' • '), 96) : entry.summary ?? null,
  }
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
    derive(entry, parameters) {
      const sqlParam = parameters?.sql
      const queryParam = parameters?.query
      const queriesParam = parameters?.queries
      let rawQueries: unknown[] = []
      if (sqlParam !== undefined && sqlParam !== null) {
        rawQueries = Array.isArray(sqlParam) ? sqlParam : [sqlParam]
      } else if (queryParam !== undefined && queryParam !== null) {
        rawQueries = Array.isArray(queryParam) ? queryParam : [queryParam]
      } else if (queriesParam !== undefined && queriesParam !== null) {
        rawQueries = Array.isArray(queriesParam) ? queriesParam : [queriesParam]
      } else if (Array.isArray(parameters?.operations)) {
        // Fallback for backward compatibility with older tool calls
        rawQueries = parameters.operations
      }

      const statements = rawQueries.map(String)

      // Detect kanban-only SQL batches and transform them into a nice display
      // instead of showing raw SQL (the KanbanEventCard handles the detailed view)
      const isKanbanOnlyBatch = statements.length > 0 && statements.every((stmt) => {
        const normalized = stmt.trim().toUpperCase()
        // Match statements that operate on __kanban_cards table
        return (
          normalized.includes('__KANBAN_CARDS') ||
          normalized.includes('__KANBAN_') ||
          // Also match common kanban operations by pattern
          /^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+['"`]?__kanban/i.test(stmt)
        )
      })

      if (isKanbanOnlyBatch) {
        return { skip: true }
      }

      const agentConfigUpdate = parseAgentConfigUpdates(statements)
      if (agentConfigUpdate) {
        const {
          updatesCharter,
          updatesSchedule,
          charterValue,
          scheduleValue,
          scheduleCleared,
        } = agentConfigUpdate
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
        const baseTransform = {
          detailComponent: AgentConfigUpdateDetail,
          charterText: charterValue ?? undefined,
          sqlStatements: statements,
        }

        if (updatesCharter && updatesSchedule) {
          const combinedScheduleCaption = scheduleCleared
            ? 'Schedule disabled'
            : scheduleSummary ?? 'Schedule updated'
          const combinedSummary = scheduleCleared
            ? 'Assignment updated. Schedule disabled.'
            : scheduleSummary
              ? `Assignment updated. Schedule set to ${scheduleSummary}.`
              : 'Assignment and schedule updated.'
          return {
            label: 'Assignment and schedule updated',
            caption: `Assignment updated • ${combinedScheduleCaption}`,
            icon: Workflow,
            iconBgClass: 'bg-indigo-100',
            iconColorClass: 'text-indigo-600',
            summary: combinedSummary,
            ...baseTransform,
          }
        }

        if (updatesCharter) {
          const charterCaption = charterValue ? truncate(charterValue, 48) : null
          return {
            label: 'Assignment updated',
            caption: charterCaption ?? entry.caption ?? 'Assignment updated',
            icon: FileCheck2,
            iconBgClass: 'bg-indigo-100',
            iconColorClass: 'text-indigo-600',
            summary: 'Assignment updated.',
            ...baseTransform,
          }
        }

        if (updatesSchedule) {
          return {
            label: 'Schedule updated',
            caption: scheduleCaption,
            icon: CalendarClock,
            iconBgClass: 'bg-sky-100',
            iconColorClass: 'text-sky-600',
            summary: scheduleSummaryText,
            ...baseTransform,
          }
        }
      }

      return {
        caption: rawQueries.length ? `${rawQueries.length} statement${rawQueries.length === 1 ? '' : 's'}` : 'SQL batch',
        sqlStatements: statements,
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
    name: 'create_csv',
    label: 'CSV export',
    icon: ClipboardList,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-600',
    detailKind: 'fileExport',
    derive(entry, parameters) {
      return deriveFileExport(entry, parameters, 'CSV export')
    },
  },
  {
    name: 'create_file',
    label: 'File export',
    icon: FileText,
    iconBgClass: 'bg-slate-100',
    iconColorClass: 'text-slate-600',
    detailKind: 'fileExport',
    derive(entry, parameters) {
      return deriveFileExport(entry, parameters, 'File export')
    },
  },
  {
    name: 'create_pdf',
    label: 'PDF export',
    icon: FileText,
    iconBgClass: 'bg-rose-100',
    iconColorClass: 'text-rose-600',
    detailKind: 'fileExport',
    derive(entry, parameters) {
      return deriveFileExport(entry, parameters, 'PDF export')
    },
  },
  {
    name: 'create_chart',
    label: 'Chart',
    icon: BarChart3,
    iconBgClass: 'bg-indigo-100',
    iconColorClass: 'text-indigo-600',
    detailKind: 'chart',
    derive(_entry, parameters) {
      const chartType = coerceString(parameters?.type)
      const title = coerceString(parameters?.title)
      const caption = title || (chartType ? `${chartType} chart` : 'Chart')
      return { caption: truncate(caption, 40) }
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
    name: 'mcp_brightdata_search_engine',
    aliases: ['mcp_brightdata_search_engine_batch', 'search_engine', 'search_engine_batch'],
    label: 'Web search',
    icon: Search,
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-600',
    detailKind: 'brightDataSearch',
    derive(entry, parameters) {
      const query = extractBrightDataSearchQuery(parameters ?? null)
      const resultCount = extractBrightDataResultCount(entry.result)
      const captionParts: string[] = []

      if (query) {
        captionParts.push(`“${truncate(query, 52)}”`)
      }
      if (resultCount !== null) {
        captionParts.push(`${resultCount} result${resultCount === 1 ? '' : 's'}`)
      }

      const caption = captionParts.length ? captionParts.join(' • ') : entry.caption ?? 'Web search'
      const summary =
        entry.summary ??
        (resultCount !== null ? `${resultCount} result${resultCount === 1 ? '' : 's'}` : null)

      return {
        caption,
        summary,
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_linkedin_person_profile',
    aliases: ['web_data_linkedin_person_profile'],
    label: 'LinkedIn profile',
    icon: Linkedin,
    iconBgClass: LINKEDIN_ICON_BG_CLASS,
    iconColorClass: LINKEDIN_ICON_COLOR_CLASS,
    detailKind: 'linkedinPerson',
    derive(entry, parameters) {
      const caption = deriveLinkedInCaption(parameters, [
        'profile_url',
        'profile_id',
        'public_id',
        'person_url',
        'url',
        'username',
        'vanity',
        'name',
      ])
      return {
        caption: caption ?? entry.caption ?? 'LinkedIn profile',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_linkedin_company_profile',
    aliases: ['web_data_linkedin_company_profile'],
    label: 'LinkedIn company',
    icon: Linkedin,
    iconBgClass: LINKEDIN_ICON_BG_CLASS,
    iconColorClass: LINKEDIN_ICON_COLOR_CLASS,
    detailKind: 'linkedinCompany',
    derive(entry, parameters) {
      const caption = deriveLinkedInCaption(parameters, [
        'company_name',
        'company',
        'organization',
        'company_url',
        'url',
        'profile_url',
        'name',
      ])
      return {
        caption: caption ?? entry.caption ?? 'LinkedIn company',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_linkedin_job_listings',
    aliases: ['web_data_linkedin_job_listings'],
    label: 'LinkedIn jobs',
    icon: Linkedin,
    iconBgClass: LINKEDIN_ICON_BG_CLASS,
    iconColorClass: LINKEDIN_ICON_COLOR_CLASS,
    detailKind: 'linkedinJobListings',
    derive(entry, parameters) {
      const query = deriveLinkedInCaption(parameters, [
        'query',
        'keywords',
        'keyword',
        'company_name',
        'company',
        'title',
        'role',
        'location',
      ])

      const items = extractBrightDataArray(entry.result)
      const first = items[0]
      const title = coerceString(first?.['job_title']) || coerceString(first?.['title'])
      const company = coerceString(first?.['company_name']) || coerceString(first?.['company'])
      const fallback = [title, company].filter(Boolean).join(' • ') || coerceString(parameters?.['url'])
      const caption = query || fallback

      return {
        caption: caption ? truncate(caption, 56) : entry.caption ?? 'LinkedIn jobs',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_linkedin_posts',
    aliases: ['web_data_linkedin_posts'],
    label: 'LinkedIn posts',
    icon: Linkedin,
    iconBgClass: LINKEDIN_ICON_BG_CLASS,
    iconColorClass: LINKEDIN_ICON_COLOR_CLASS,
    detailKind: 'linkedinPosts',
    derive(entry, parameters) {
      const items = extractBrightDataArray(entry.result)
      const first = items[0]
      const title = coerceString(first?.['title']) || coerceString(first?.['headline'])
      const author = coerceString(first?.['user_name']) || coerceString(first?.['user_id'])
      const url = coerceString(first?.['url']) || coerceString(first?.['post_url'])

      const caption = deriveLinkedInCaption(parameters, [
        'query',
        'keywords',
        'keyword',
        'profile_url',
        'company_name',
        'hashtag',
        'url',
      ]) || title || author || url
      return {
        caption: caption ?? entry.caption ?? 'LinkedIn posts',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_linkedin_people_search',
    aliases: ['web_data_linkedin_people_search'],
    label: 'LinkedIn search',
    icon: Linkedin,
    iconBgClass: LINKEDIN_ICON_BG_CLASS,
    iconColorClass: LINKEDIN_ICON_COLOR_CLASS,
    detailKind: 'linkedinPeopleSearch',
    derive(entry, parameters) {
      const firstName = coerceString(parameters?.['first_name'])
      const lastName = coerceString(parameters?.['last_name'])
      const nameCaption = [firstName, lastName].filter(Boolean).join(' ')
      const caption = nameCaption || deriveLinkedInCaption(parameters, [
        'query',
        'keywords',
        'keyword',
        'company',
        'title',
        'role',
        'location',
      ])
      return {
        caption: caption ?? entry.caption ?? 'LinkedIn search',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_yahoo_finance_business',
    aliases: ['web_data_yahoo_finance_business'],
    label: 'Yahoo Finance',
    icon: BarChart3,
    iconBgClass: 'bg-amber-100',
    iconColorClass: 'text-amber-700',
    detailKind: 'yahooFinanceBusiness',
    derive(entry, parameters) {
      const ticker = coerceString(parameters?.['stock_ticker']) || coerceString(parameters?.['symbol'])
      const name = coerceString(parameters?.['name'])
      const captionPieces = [ticker ? ticker.toUpperCase() : null, name ? truncate(name, 44) : null].filter(Boolean)
      return {
        caption: captionPieces.length ? captionPieces.join(' • ') : entry.caption ?? 'Yahoo Finance',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_reuter_news',
    aliases: ['web_data_reuter_news'],
    label: 'Reuters news',
    icon: Globe,
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-700',
    detailKind: 'reutersNews',
    derive(entry, parameters) {
      const articles = extractBrightDataArray(entry.result)
      const first = articles[0]
      const headline = coerceString(first?.['headline']) || coerceString(first?.['title'])
      const keyword = coerceString(first?.['keyword']) || coerceString(parameters?.['keyword'])
      const url = coerceString(first?.['url']) || coerceString(parameters?.['url'])
      const caption = headline || keyword || url
      return {
        caption: caption ? truncate(caption, 56) : entry.caption ?? 'Reuters news',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_zillow_properties_listing',
    aliases: ['web_data_zillow_properties_listing'],
    label: 'Zillow listing',
    icon: Home,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-700',
    detailKind: 'zillowListing',
    derive(entry, parameters) {
      const properties = extractBrightDataArray(entry.result)
      const first = properties[0]
      const addressRecord =
        first && typeof first === 'object' && 'address' in first && first.address && typeof first.address === 'object'
          ? (first.address as Record<string, unknown>)
          : null
      const street = coerceString(first?.['streetAddress']) || coerceString(addressRecord?.['streetAddress'])
      const city = coerceString(first?.['city']) || coerceString(addressRecord?.['city'])
      const state = coerceString(first?.['state']) || coerceString(addressRecord?.['state'])
      const location = [street, city, state].filter(Boolean).join(', ')
      const price = coerceNumber(first?.['price'])
      const priceCaption = price !== null ? `$${price.toLocaleString()}` : null
      const url = coerceString(parameters?.['url'])
      const baseCaption = location || url
      const combined = baseCaption && priceCaption ? `${baseCaption} • ${priceCaption}` : baseCaption ?? priceCaption
      return {
        caption: combined ? truncate(combined, 56) : entry.caption ?? 'Zillow listing',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_crunchbase_company',
    aliases: ['web_data_crunchbase_company'],
    label: 'Crunchbase company',
    icon: Database,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-700',
    detailKind: 'crunchbaseCompany',
    derive(entry, parameters) {
      const caption = pickFirstParameter(parameters, ['company', 'company_id', 'name', 'organization', 'slug', 'url'])
      return {
        caption: caption ? truncate(caption, 56) : entry.caption ?? 'Crunchbase company',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_amazon_product',
    aliases: ['web_data_amazon_product'],
    label: 'Amazon product',
    icon: ShoppingBag,
    iconBgClass: 'bg-orange-100',
    iconColorClass: 'text-orange-700',
    detailKind: 'amazonProduct',
    derive(entry, parameters) {
      const caption = pickFirstParameter(parameters, ['title', 'asin', 'url', 'product', 'name'])
      return {
        caption: caption ? truncate(caption, 56) : entry.caption ?? 'Amazon product',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_amazon_product_search',
    aliases: ['web_data_amazon_product_search'],
    label: 'Amazon search',
    icon: ShoppingBag,
    iconBgClass: 'bg-orange-100',
    iconColorClass: 'text-orange-700',
    detailKind: 'amazonProductSearch',
    derive(entry, parameters) {
      const items = extractBrightDataArray(entry.result)
      const first = items[0]
      const queryFromItem = coerceString(first?.['keyword']) ||
        (first && typeof first === 'object' && 'input' in first && first.input && typeof first.input === 'object'
          ? coerceString((first.input as Record<string, unknown>)['keyword'])
          : null)
      const query = extractBrightDataSearchQuery(parameters) || coerceString(parameters?.['keyword']) || queryFromItem
      const name = coerceString(first?.['name']) || coerceString(first?.['title']) || coerceString(first?.['asin'])
      const count = extractBrightDataResultCount(entry.result) ?? (items.length ? items.length : null)
      const countLabel = count ? `${count} result${count === 1 ? '' : 's'}` : null
      const caption = query || name
      const combined = caption && countLabel ? `${caption} • ${countLabel}` : caption ?? countLabel
      return {
        caption: combined ? truncate(combined, 56) : entry.caption ?? 'Amazon search',
      }
    },
  },
  {
    name: 'mcp_brightdata_web_data_amazon_product_reviews',
    aliases: ['web_data_amazon_product_reviews'],
    label: 'Amazon reviews',
    icon: ShoppingBag,
    iconBgClass: 'bg-orange-100',
    iconColorClass: 'text-orange-700',
    detailKind: 'amazonProductReviews',
    derive(entry, parameters) {
      const urlCaption = pickFirstParameter(parameters, ['url', 'product_url'])
      const firstRecord = extractBrightDataArray(entry.result)[0]
      const productName = coerceString(firstRecord?.['product_name'])
      const ratingValue = coerceNumber(firstRecord?.['product_rating'])
      const ratingLabel =
        ratingValue !== null ? `${Number.isInteger(ratingValue) ? ratingValue : ratingValue.toFixed(1)}/5` : null
      const ratingCount = formatCount(coerceNumber(firstRecord?.['product_rating_count']))
      const ratingSummary = ratingLabel
        ? `${ratingLabel}${ratingCount ? ` (${ratingCount})` : ''}`
        : ratingCount
          ? `${ratingCount} ratings`
          : null
      const caption = productName || urlCaption
      const combined = caption && ratingSummary ? `${caption} • ${ratingSummary}` : caption ?? ratingSummary
      return {
        caption: combined ? truncate(combined, 56) : entry.caption ?? 'Amazon reviews',
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
    derive(entry, parameters) {
      const url =
        coerceString(parameters?.['url']) ||
        coerceString(parameters?.['start_url']) ||
        coerceString(parameters?.['target_url']) ||
        null
      const caption = url ? truncate(url, 64) : null
      return {
        caption: caption ?? entry.caption ?? 'Web snapshot',
      }
    },
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
