import type {
  PendingActionRequest,
  PendingContactRequest,
  PendingHumanInputRequest,
  PendingHumanInputRequestInputMode,
  PendingHumanInputRequestStatus,
  ProcessingSnapshot,
  RequestedSecret,
  TimelineEvent,
} from '../types/agentChat'
import type { SignupPreviewState } from '../types/agentRoster'
import type { InsightsResponse } from '../types/insight'
import { jsonFetch, jsonRequest } from './http'

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
  signup_preview_state?: SignupPreviewState | null
  pending_human_input_requests?: PendingHumanInputRequest[]
  pending_action_requests?: PendingActionRequest[]
}

export type AgentWebSessionSnapshot = {
  session_key: string
  ttl_seconds: number
  expires_at: string
  last_seen_at: string
  last_seen_source: string | null
  is_visible: boolean
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
    pending_action_requests?: unknown[]
  }>(url)
  return {
    ...response,
    pending_human_input_requests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
    pending_action_requests: normalizePendingActionRequests(response.pending_action_requests),
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

type PendingActionRequestWire = {
  id?: unknown
  kind?: unknown
  count?: unknown
  requests?: unknown
  secrets?: unknown
  requestId?: unknown
  request_id?: unknown
  requestedCharter?: unknown
  requested_charter?: unknown
  handoffMessage?: unknown
  handoff_message?: unknown
  requestReason?: unknown
  request_reason?: unknown
  requestedAt?: unknown
  requested_at?: unknown
  expiresAt?: unknown
  expires_at?: unknown
  decisionApiUrl?: unknown
  decision_api_url?: unknown
  fulfillApiUrl?: unknown
  fulfill_api_url?: unknown
  removeApiUrl?: unknown
  remove_api_url?: unknown
  resolveApiUrl?: unknown
  resolve_api_url?: unknown
}

type RequestedSecretWire = {
  id?: unknown
  name?: unknown
  key?: unknown
  secretType?: unknown
  secret_type?: unknown
  domainPattern?: unknown
  domain_pattern?: unknown
  description?: unknown
  createdAt?: unknown
  created_at?: unknown
  updatedAt?: unknown
  updated_at?: unknown
}

type PendingContactRequestWire = {
  id?: unknown
  channel?: unknown
  address?: unknown
  name?: unknown
  reason?: unknown
  purpose?: unknown
  allowInbound?: unknown
  allow_inbound?: unknown
  allowOutbound?: unknown
  allow_outbound?: unknown
  canConfigure?: unknown
  can_configure?: unknown
  requestedAt?: unknown
  requested_at?: unknown
  expiresAt?: unknown
  expires_at?: unknown
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

function normalizeRequestedSecret(raw: unknown): RequestedSecret | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }
  const secret = raw as RequestedSecretWire
  const id = asNonEmptyString(secret.id)
  const name = asNonEmptyString(secret.name)
  const key = asNonEmptyString(secret.key)
  const secretType = (
    asNonEmptyString(secret.secretType)
    ?? asNonEmptyString(secret.secret_type)
  ) as RequestedSecret['secretType'] | null
  const domainPattern =
    asNonEmptyString(secret.domainPattern)
    ?? asNonEmptyString(secret.domain_pattern)
  if (!id || !name || !key || !secretType || !domainPattern) {
    return null
  }
  return {
    id,
    name,
    key,
    secretType,
    domainPattern,
    description: asNonEmptyString(secret.description),
    createdAt: asNonEmptyString(secret.createdAt) ?? asNonEmptyString(secret.created_at),
    updatedAt: asNonEmptyString(secret.updatedAt) ?? asNonEmptyString(secret.updated_at),
  }
}

function normalizePendingContactRequest(raw: unknown): PendingContactRequest | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }
  const request = raw as PendingContactRequestWire
  const id = asNonEmptyString(request.id)
  const channel = asNonEmptyString(request.channel)
  const address = asNonEmptyString(request.address)
  if (!id || !channel || !address) {
    return null
  }
  return {
    id,
    channel,
    address,
    name: asNonEmptyString(request.name),
    reason: asNonEmptyString(request.reason),
    purpose: asNonEmptyString(request.purpose),
    allowInbound: Boolean(request.allowInbound ?? request.allow_inbound),
    allowOutbound: Boolean(request.allowOutbound ?? request.allow_outbound),
    canConfigure: Boolean(request.canConfigure ?? request.can_configure),
    requestedAt: asNonEmptyString(request.requestedAt) ?? asNonEmptyString(request.requested_at),
    expiresAt: asNonEmptyString(request.expiresAt) ?? asNonEmptyString(request.expires_at),
  }
}

export function normalizePendingHumanInputRequests(raw: unknown): PendingHumanInputRequest[] {
  if (!Array.isArray(raw)) {
    return []
  }
  return raw
    .map(normalizePendingHumanInputRequest)
    .filter((value): value is PendingHumanInputRequest => Boolean(value))
}

export function normalizePendingActionRequests(raw: unknown): PendingActionRequest[] {
  if (!Array.isArray(raw)) {
    return []
  }
  const normalized: PendingActionRequest[] = []

  raw.forEach((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return
    }
    const request = item as PendingActionRequestWire
    const id = asNonEmptyString(request.id)
    const kind = asNonEmptyString(request.kind)
    const count = asPositiveInteger(request.count) ?? 0
    if (!id || !kind) {
      return
    }
    if (kind === 'human_input') {
      const requests = normalizePendingHumanInputRequests(request.requests)
      normalized.push({ id, kind, requests, count: count || requests.length })
      return
    }
    if (kind === 'spawn_request') {
      const requestId = asNonEmptyString(request.requestId) ?? asNonEmptyString(request.request_id)
      const requestedCharter =
        asNonEmptyString(request.requestedCharter)
        ?? asNonEmptyString(request.requested_charter)
      if (!requestId || !requestedCharter) {
        return
      }
      normalized.push({
        id,
        kind,
        requestId,
        requestedCharter,
        handoffMessage: asNonEmptyString(request.handoffMessage) ?? asNonEmptyString(request.handoff_message),
        requestReason: asNonEmptyString(request.requestReason) ?? asNonEmptyString(request.request_reason),
        requestedAt: asNonEmptyString(request.requestedAt) ?? asNonEmptyString(request.requested_at),
        expiresAt: asNonEmptyString(request.expiresAt) ?? asNonEmptyString(request.expires_at),
        decisionApiUrl: asNonEmptyString(request.decisionApiUrl) ?? asNonEmptyString(request.decision_api_url),
      })
      return
    }
    if (kind === 'requested_secrets') {
      const secrets = Array.isArray(request.secrets)
        ? request.secrets.map(normalizeRequestedSecret).filter((value): value is RequestedSecret => Boolean(value))
        : []
      normalized.push({
        id,
        kind,
        secrets,
        count: count || secrets.length,
        fulfillApiUrl: asNonEmptyString(request.fulfillApiUrl) ?? asNonEmptyString(request.fulfill_api_url),
        removeApiUrl: asNonEmptyString(request.removeApiUrl) ?? asNonEmptyString(request.remove_api_url),
      })
      return
    }
    if (kind === 'contact_requests') {
      const requests = Array.isArray(request.requests)
        ? request.requests.map(normalizePendingContactRequest).filter((value): value is PendingContactRequest => Boolean(value))
        : []
      normalized.push({
        id,
        kind,
        requests,
        count: count || requests.length,
        resolveApiUrl: asNonEmptyString(request.resolveApiUrl) ?? asNonEmptyString(request.resolve_api_url),
      })
    }
  })

  return normalized
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
  pendingActionRequests: PendingActionRequest[]
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
    pending_action_requests?: unknown[]
  }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return {
    event: response.event,
    pendingHumanInputRequests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
    pendingActionRequests: normalizePendingActionRequests(response.pending_action_requests),
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
    pending_action_requests?: unknown[]
  }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return {
    event: response.event,
    pendingHumanInputRequests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
    pendingActionRequests: normalizePendingActionRequests(response.pending_action_requests),
  }
}

export type RequestedSecretsFulfillPayload = {
  values: Record<string, string>
  make_global?: boolean
}

export type RequestedSecretsRemovePayload = {
  secret_ids: string[]
}

export type ContactRequestResolvePayload = {
  responses: Array<{
    request_id: string
    decision: 'approve' | 'decline'
    allow_inbound?: boolean
    allow_outbound?: boolean
    can_configure?: boolean
  }>
}

export type SpawnRequestDecisionPayload = {
  decision: 'approve' | 'decline'
}

export type PendingActionMutationResult = {
  message?: string
  pendingHumanInputRequests: PendingHumanInputRequest[]
  pendingActionRequests: PendingActionRequest[]
}

async function postPendingActionMutation(
  url: string,
  payload: unknown,
): Promise<PendingActionMutationResult> {
  const response = await jsonRequest<{
    message?: string
    pending_human_input_requests?: unknown[]
    pending_action_requests?: unknown[]
  }>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
  return {
    message: asNonEmptyString(response.message) ?? undefined,
    pendingHumanInputRequests: normalizePendingHumanInputRequests(response.pending_human_input_requests),
    pendingActionRequests: normalizePendingActionRequests(response.pending_action_requests),
  }
}

export function fulfillRequestedSecrets(
  agentId: string,
  payload: RequestedSecretsFulfillPayload,
): Promise<PendingActionMutationResult> {
  return postPendingActionMutation(`/console/api/agents/${agentId}/requested-secrets/fulfill/`, payload)
}

export function removeRequestedSecrets(
  agentId: string,
  payload: RequestedSecretsRemovePayload,
): Promise<PendingActionMutationResult> {
  return postPendingActionMutation(`/console/api/agents/${agentId}/requested-secrets/remove/`, payload)
}

export function resolveContactRequests(
  agentId: string,
  payload: ContactRequestResolvePayload,
): Promise<PendingActionMutationResult> {
  return postPendingActionMutation(`/console/api/agents/${agentId}/contact-requests/resolve/`, payload)
}

export function resolveSpawnRequest(
  decisionApiUrl: string,
  payload: SpawnRequestDecisionPayload,
): Promise<PendingActionMutationResult> {
  return postPendingActionMutation(decisionApiUrl, payload)
}

export type ProcessingStatusResponse = {
  processing_active: boolean
  processing_snapshot?: ProcessingSnapshot
  signup_preview_state?: SignupPreviewState | null
}

export type StopAgentResponse = {
  stopping: boolean
  cancelledWebTaskCount: number
  processing_active: boolean
  processing_snapshot?: ProcessingSnapshot
}

export async function fetchProcessingStatus(agentId: string): Promise<ProcessingStatusResponse> {
  const url = `/console/api/agents/${agentId}/processing/`
  return jsonFetch<ProcessingStatusResponse>(url)
}

export async function stopAgentProcessing(agentId: string): Promise<StopAgentResponse> {
  const url = `/console/api/agents/${agentId}/stop/`
  return jsonFetch<StopAgentResponse>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}

type WebSessionPayload = {
  session_key?: string
  ttl_seconds?: number
  is_visible?: boolean
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

export function startAgentWebSession(
  agentId: string,
  ttlSeconds?: number,
  isVisible?: boolean,
): Promise<AgentWebSessionSnapshot> {
  const payload: WebSessionPayload = {}
  if (ttlSeconds) payload.ttl_seconds = ttlSeconds
  if (typeof isVisible === 'boolean') payload.is_visible = isVisible
  return postWebSession(agentId, 'start', payload)
}

export function heartbeatAgentWebSession(
  agentId: string,
  sessionKey: string,
  ttlSeconds?: number,
  isVisible?: boolean,
): Promise<AgentWebSessionSnapshot> {
  const payload: WebSessionPayload = { session_key: sessionKey }
  if (ttlSeconds) payload.ttl_seconds = ttlSeconds
  if (typeof isVisible === 'boolean') payload.is_visible = isVisible
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
