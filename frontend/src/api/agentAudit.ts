import type { AuditEvent, AuditTimelineBucket, PromptArchive } from '../types/agentAudit'
import { jsonFetch, jsonRequest } from './http'

type EventsResponse = {
  events: AuditEvent[]
  has_more: boolean
  next_cursor: string | null
  processing_active: boolean
  agent: {
    id: string
    name: string
    color: string | null
  }
}

type TimelineResponse = {
  buckets: AuditTimelineBucket[]
  latest: string | null
  days: number
}

export type StaffAgentSearchResult = {
  id: string
  name: string
}

type StaffAgentSearchResponse = {
  agents: StaffAgentSearchResult[]
}

export async function fetchAuditEvents(
  agentId: string,
  params: { cursor?: string | null; limit?: number; at?: string | null; day?: string | null; tzOffsetMinutes?: number | null } = {},
): Promise<EventsResponse> {
  const query = new URLSearchParams()
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.limit) query.set('limit', params.limit.toString())
  if (params.at) query.set('at', params.at)
  if (params.day) query.set('day', params.day)
  if (typeof params.tzOffsetMinutes === 'number') query.set('tz_offset_minutes', params.tzOffsetMinutes.toString())
  const url = `/console/api/staff/agents/${agentId}/audit/${query.toString() ? `?${query.toString()}` : ''}`
  return jsonFetch<EventsResponse>(url)
}

export async function fetchPromptArchive(archiveId: string): Promise<PromptArchive> {
  const url = `/console/api/staff/prompt-archives/${archiveId}/`
  return jsonFetch<PromptArchive>(url)
}

export async function fetchAuditTimeline(agentId: string, params: { days?: number } = {}): Promise<TimelineResponse> {
  const query = new URLSearchParams()
  if (params.days) query.set('days', params.days.toString())
  const tzOffset = -new Date().getTimezoneOffset()
  query.set('tz_offset_minutes', tzOffset.toString())
  const url = `/console/api/staff/agents/${agentId}/audit/timeline/${query.toString() ? `?${query.toString()}` : ''}`
  return jsonFetch<TimelineResponse>(url)
}

export async function searchStaffAgents(query: string, params: { limit?: number } = {}): Promise<StaffAgentSearchResponse> {
  const search = new URLSearchParams()
  search.set('q', query)
  if (params.limit) search.set('limit', params.limit.toString())
  const url = `/console/api/staff/agents/search/${search.toString() ? `?${search.toString()}` : ''}`
  return jsonFetch<StaffAgentSearchResponse>(url)
}

export async function triggerProcessEvents(agentId: string): Promise<{ queued: boolean; processing_active: boolean }> {
  const url = `/console/api/staff/agents/${agentId}/audit/process/`
  return jsonRequest(url, { method: 'POST', includeCsrf: true })
}

export async function createSystemMessage(
  agentId: string,
  payload: { body: string; is_active?: boolean },
): Promise<AuditEvent> {
  const url = `/console/api/staff/agents/${agentId}/system-messages/`
  return jsonRequest(url, { method: 'POST', includeCsrf: true, json: payload })
}

export async function updateSystemMessage(
  agentId: string,
  messageId: string,
  payload: { body?: string; is_active?: boolean },
): Promise<AuditEvent> {
  const url = `/console/api/staff/agents/${agentId}/system-messages/${messageId}/`
  return jsonRequest(url, { method: 'PATCH', includeCsrf: true, json: payload })
}
