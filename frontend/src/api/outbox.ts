import { jsonFetch, jsonRequest } from './http'

export type OutboxStatus = 'needs_review' | 'sending' | 'failed' | 'sent' | 'discarded' | 'expired'
export type EmailSendingMode = 'review_all_external' | 'review_new_contacts' | 'send_automatically'

export type OutboxAgentFile = {
  id: string
  name: string
  path: string
  nodeType: 'dir' | 'file'
  sizeBytes: number | null
}

export type OutboxItem = {
  id: string
  messageId: string
  agent: { id: string; name: string }
  sender: string
  to: string
  cc: string[]
  subject: string
  bodyPreview: string
  bodyHtml?: string
  status: OutboxStatus
  reviewStatus: 'pending' | 'approved' | 'discarded' | 'expired'
  deliveryStatus: string
  version: number
  queuedAt: string
  expiresAt: string
  decidedAt?: string | null
  warnings: Array<{ code: string; label: string }>
  allowedActions: { edit: boolean; approve: boolean; discard: boolean; retry: boolean }
  lastError?: string | null
  attachments?: Array<{
    id: string
    nodeId?: string | null
    filename: string
    contentType: string
    size: number
    sha256: string
  }>
  threadContext?: Array<{
    id: string
    body: string
    isOutbound: boolean
    timestamp: string
  }>
}

export type OutboxCounts = {
  needsReview: number
  sending: number
  failed: number
  recent: number
}

export type OutboxListResponse = {
  featureEnabled: boolean
  available: boolean
  items: OutboxItem[]
  counts: OutboxCounts
  nextCursor?: string | null
}

export type EmailSendingPolicy = {
  defaultMode: EmailSendingMode
  minimumMode: EmailSendingMode | null
  canSetMinimum: boolean
  emailNotificationsEnabled: boolean
  agents: Array<{
    id: string
    name: string
    requestedMode: EmailSendingMode
    effectiveMode: EmailSendingMode
  }>
}

export function fetchOutbox(status: string, search: string): Promise<OutboxListResponse> {
  const params = new URLSearchParams({ status })
  if (search.trim()) params.set('search', search.trim())
  return jsonFetch(`/console/api/outbox/?${params.toString()}`)
}

export async function fetchOutboxItem(id: string): Promise<OutboxItem> {
  const response = await jsonFetch<{ item: OutboxItem }>(`/console/api/outbox/${id}/`)
  return response.item
}

export async function updateOutboxItem(id: string, payload: Record<string, unknown>): Promise<OutboxItem> {
  const response = await jsonRequest<{ item: OutboxItem }>(`/console/api/outbox/${id}/`, {
    method: 'PATCH',
    includeCsrf: true,
    json: payload,
  })
  return response.item
}

export async function decideOutboxItem(
  id: string,
  action: 'approve' | 'discard' | 'retry',
  payload: Record<string, unknown> = {},
): Promise<OutboxItem> {
  const response = await jsonRequest<{ item: OutboxItem }>(`/console/api/outbox/${id}/${action}/`, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
  return response.item
}

export function bulkDiscardOutbox(items: Array<{ id: string; expectedVersion: number }>): Promise<{ discardedIds: string[] }> {
  return jsonRequest('/console/api/outbox/bulk-discard/', {
    method: 'POST',
    includeCsrf: true,
    json: { items },
  })
}

export function fetchEmailSendingPolicy(): Promise<EmailSendingPolicy> {
  return jsonFetch('/console/api/email-sending-policy/')
}

export async function fetchOutboxAgentFiles(agentId: string): Promise<OutboxAgentFile[]> {
  const response = await jsonFetch<{ nodes: OutboxAgentFile[] }>(`/console/api/agents/${agentId}/files/`)
  return response.nodes.filter((node) => node.nodeType === 'file')
}

export function updateEmailSendingPolicy(payload: Record<string, unknown>): Promise<EmailSendingPolicy> {
  return jsonRequest('/console/api/email-sending-policy/', {
    method: 'PATCH',
    includeCsrf: true,
    json: payload,
  })
}
