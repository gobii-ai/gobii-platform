import type { TimelineEvent } from '../types/agentChat'
import { jsonFetch } from './http'

export type TimelineDirection = 'initial' | 'older' | 'newer'

export type TimelineResponse = {
  events: TimelineEvent[]
  oldest_cursor: string | null
  newest_cursor: string | null
  has_more_older: boolean
  has_more_newer: boolean
  processing_active: boolean
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

export async function sendAgentMessage(agentId: string, body: string): Promise<TimelineEvent> {
  const url = `/console/api/agents/${agentId}/messages/`
  const response = await jsonFetch<{ event: TimelineEvent }>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  })
  return response.event
}

export async function fetchProcessingStatus(agentId: string): Promise<{ processing_active: boolean }> {
  const url = `/console/api/agents/${agentId}/processing/`
  return jsonFetch<{ processing_active: boolean }>(url)
}
