import type { ProcessingSnapshot, TimelineEvent } from '../types/agentChat'
import type { InsightsResponse } from '../types/insight'
import { jsonFetch } from './http'

export type TimelineDirection = 'initial' | 'older' | 'newer'

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
  return jsonFetch<TimelineResponse>(url)
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
