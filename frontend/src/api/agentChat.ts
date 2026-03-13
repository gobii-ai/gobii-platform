import type {
  PendingHumanInputRequest,
  PendingHumanInputRequestInputMode,
  PendingHumanInputRequestStatus,
  ProcessingSnapshot,
  TimelineEvent,
} from '../types/agentChat'
import type { InsightsResponse } from '../types/insight'
import { jsonFetch } from './http'

export type TimelineDirection = 'initial' | 'older' | 'newer'
export type SuggestionCategory = 'capabilities' | 'deliverables' | 'integrations' | 'planning'
export type AgentSuggestion = {
  id: string
  text: string
  category: SuggestionCategory
}
export type AgentSuggestionsResponse = {
  suggestions: AgentSuggestion[]
  source?: 'none' | 'static' | 'dynamic'
}

export type TimelineResponse = {
  events: TimelineEvent[]
  oldest_cursor: string | null
  newest_cursor: string | null
  has_more_older: boolean
  has_more_newer: boolean
  processing_active: boolean
  processing_snapshot?: ProcessingSnapshot
  agent_color_hex?: string | null
  agent_name?: string | null
  agent_avatar_url?: string | null
  pending_human_input_requests?: PendingHumanInputRequest[]
}

export type AgentWebSessionSnapshot = {
  session_key: string
  ttl_seconds: number
  expires_at: string
  last_seen_at: string
  last_seen_source: string | null
  ended_at?: string
}

export async function fetchAgentTimeline(
  agentId: string,
  params: { cursor?: string | null; direction?: TimelineDirection; limit?: number } = {},
): Promise<TimelineResponse> {
  const query = new URLSearchParams()
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.direction) query.set('direction', params.direction)
  if (params.limit) query.set('limit', params.limit.toString())

  const url = `/console/api/agents/${agentId}/timeline/${query.toString() ? `?${query.toString()}` : ''}`
  const response = await jsonFetch<TimelineResponse & {
    pending_human_input_requests?: unknown[]
  }>(url)
  return {
    ...response,
    pending_human_input_requests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
  }
}

type PendingHumanInputRequestWire = {
  id?: unknown
  question?: unknown
  options?: unknown
  createdAt?: unknown
  created_at?: unknown
  status?: unknown
  activeConversationChannel?: unknown
  active_conversation_channel?: unknown
  inputMode?: unknown
  input_mode?: unknown
  batchId?: unknown
  batch_id?: unknown
  batchPosition?: unknown
  batch_position?: unknown
  batchSize?: unknown
  batch_size?: unknown
}

type HumanInputOptionWire = {
  key?: unknown
  optionKey?: unknown
  option_key?: unknown
  title?: unknown
  description?: unknown
}

function asNonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
}

function asPositiveInteger(value: unknown): number | null {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10)
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null
  }
  return null
}

function normalizeHumanInputOption(raw: unknown): PendingHumanInputRequest['options'][number] | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }
  const option = raw as HumanInputOptionWire
  const key =
    asNonEmptyString(option.key)
    ?? asNonEmptyString(option.optionKey)
    ?? asNonEmptyString(option.option_key)
  const title = asNonEmptyString(option.title)
  const description = asNonEmptyString(option.description)
  if (!key || !title || !description) {
    return null
  }
  return { key, title, description }
}

function normalizePendingHumanInputRequest(raw: unknown): PendingHumanInputRequest | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }
  const request = raw as PendingHumanInputRequestWire
  const id = asNonEmptyString(request.id)
  const question = asNonEmptyString(request.question)
  if (!id || !question) {
    return null
  }

  const options = Array.isArray(request.options)
    ? request.options.map(normalizeHumanInputOption).filter((value): value is NonNullable<typeof value> => Boolean(value))
    : []

  const status = (
    asNonEmptyString(request.status)
    ?? 'pending'
  ) as PendingHumanInputRequestStatus
  const inputMode = (
    asNonEmptyString(request.inputMode)
    ?? asNonEmptyString(request.input_mode)
    ?? (options.length > 0 ? 'options_plus_text' : 'free_text_only')
  ) as PendingHumanInputRequestInputMode
  const batchId =
    asNonEmptyString(request.batchId)
    ?? asNonEmptyString(request.batch_id)
    ?? id
  const batchPosition =
    asPositiveInteger(request.batchPosition)
    ?? asPositiveInteger(request.batch_position)
    ?? 1
  const batchSize =
    asPositiveInteger(request.batchSize)
    ?? asPositiveInteger(request.batch_size)
    ?? 1

  return {
    id,
    question,
    options,
    createdAt: asNonEmptyString(request.createdAt) ?? asNonEmptyString(request.created_at),
    status,
    activeConversationChannel:
      asNonEmptyString(request.activeConversationChannel)
      ?? asNonEmptyString(request.active_conversation_channel),
    inputMode,
    batchId,
    batchPosition,
    batchSize,
  }
}

function normalizePendingHumanInputRequests(raw: unknown): PendingHumanInputRequest[] {
  if (!Array.isArray(raw)) {
    return []
  }
  return raw
    .map(normalizePendingHumanInputRequest)
    .filter((value): value is PendingHumanInputRequest => Boolean(value))
}

export async function sendAgentMessage(agentId: string, body: string, attachments: File[] = []): Promise<TimelineEvent> {
  const url = `/console/api/agents/${agentId}/messages/`
  if (attachments.length > 0) {
    const formData = new FormData()
    if (body) {
      formData.append('body', body)
    }
    attachments.forEach((file) => {
      formData.append('attachments', file)
    })
    const response = await jsonFetch<{ event: TimelineEvent }>(url, {
      method: 'POST',
      body: formData,
    })
    return response.event
  }
  const response = await jsonFetch<{ event: TimelineEvent }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  })
  return response.event
}

export type HumanInputResponsePayload =
  | { selected_option_key: string; free_text?: never }
  | { free_text: string; selected_option_key?: never }

export type HumanInputResponseResult = {
  event?: TimelineEvent
  pendingHumanInputRequests: PendingHumanInputRequest[]
}

export type HumanInputBatchResponsePayload = {
  responses: Array<
    | { request_id: string; selected_option_key: string; free_text?: never }
    | { request_id: string; free_text: string; selected_option_key?: never }
  >
}

export async function respondToHumanInputRequest(
  agentId: string,
  requestId: string,
  payload: HumanInputResponsePayload,
): Promise<HumanInputResponseResult> {
  const url = `/console/api/agents/${agentId}/human-input-requests/${requestId}/respond/`
  const response = await jsonFetch<{
    event?: TimelineEvent
    pending_human_input_requests?: unknown[]
  }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return {
    event: response.event,
    pendingHumanInputRequests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
  }
}

export async function respondToHumanInputRequestsBatch(
  agentId: string,
  payload: HumanInputBatchResponsePayload,
): Promise<HumanInputResponseResult> {
  const url = `/console/api/agents/${agentId}/human-input-requests/respond-batch/`
  const response = await jsonFetch<{
    event?: TimelineEvent
    pending_human_input_requests?: unknown[]
  }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return {
    event: response.event,
    pendingHumanInputRequests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
  }
}

export type ProcessingStatusResponse = {
  processing_active: boolean
  processing_snapshot?: ProcessingSnapshot
}

export async function fetchProcessingStatus(agentId: string): Promise<ProcessingStatusResponse> {
  const url = `/console/api/agents/${agentId}/processing/`
  return jsonFetch<ProcessingStatusResponse>(url)
}

type WebSessionPayload = {
  session_key?: string
  ttl_seconds?: number
}

async function postWebSession(
  agentId: string,
  endpoint: 'start' | 'heartbeat' | 'end',
  payload: WebSessionPayload,
  init?: RequestInit,
): Promise<AgentWebSessionSnapshot> {
  const url = `/console/api/agents/${agentId}/web-sessions/${endpoint}/`
  return jsonFetch<AgentWebSessionSnapshot>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    ...init,
  })
}

export function startAgentWebSession(agentId: string, ttlSeconds?: number): Promise<AgentWebSessionSnapshot> {
  return postWebSession(agentId, 'start', ttlSeconds ? { ttl_seconds: ttlSeconds } : {})
}

export function heartbeatAgentWebSession(
  agentId: string,
  sessionKey: string,
  ttlSeconds?: number,
): Promise<AgentWebSessionSnapshot> {
  const payload: WebSessionPayload = { session_key: sessionKey }
  if (ttlSeconds) payload.ttl_seconds = ttlSeconds
  return postWebSession(agentId, 'heartbeat', payload)
}

export function endAgentWebSession(
  agentId: string,
  sessionKey: string,
  { keepalive = false }: { keepalive?: boolean } = {},
): Promise<AgentWebSessionSnapshot> {
  return postWebSession(agentId, 'end', { session_key: sessionKey }, { keepalive })
}

export async function fetchAgentInsights(agentId: string): Promise<InsightsResponse> {
  const url = `/console/api/agents/${agentId}/insights/`
  return jsonFetch<InsightsResponse>(url)
}

export async function fetchAgentSuggestions(
  agentId: string,
  params: { promptCount?: number; signal?: AbortSignal } = {},
): Promise<AgentSuggestionsResponse> {
  const query = new URLSearchParams()
  if (params.promptCount) {
    query.set('prompt_count', String(params.promptCount))
  }
  const url = `/console/api/agents/${agentId}/suggestions/${query.toString() ? `?${query.toString()}` : ''}`
  return jsonFetch<AgentSuggestionsResponse>(url, { signal: params.signal })
}
