import type { AuditEvent, AuditTimelineBucket, PromptArchive } from '../types/agentAudit'
import { jsonFetch } from './http'

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

export async function fetchAuditEvents(
  agentId: string,
  params: { cursor?: string | null; limit?: number; at?: string | null } = {},
): Promise<EventsResponse> {
  const query = new URLSearchParams()
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.limit) query.set('limit', params.limit.toString())
  if (params.at) query.set('at', params.at)
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
  const url = `/console/api/staff/agents/${agentId}/audit/timeline/${query.toString() ? `?${query.toString()}` : ''}`
  return jsonFetch<TimelineResponse>(url)
}
