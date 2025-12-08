import type { AuditRun, PromptArchive } from '../types/agentAudit'
import { jsonFetch } from './http'

type RunsResponse = {
  runs: AuditRun[]
  has_more: boolean
  next_cursor: string | null
  processing_active: boolean
  agent: {
    id: string
    name: string
    color: string | null
  }
}

export async function fetchAuditRuns(agentId: string, params: { cursor?: string | null; limit?: number } = {}): Promise<RunsResponse> {
  const query = new URLSearchParams()
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.limit) query.set('limit', params.limit.toString())
  const url = `/console/api/staff/agents/${agentId}/audit/${query.toString() ? `?${query.toString()}` : ''}`
  return jsonFetch<RunsResponse>(url)
}

export async function fetchPromptArchive(archiveId: string): Promise<PromptArchive> {
  const url = `/console/api/staff/prompt-archives/${archiveId}/`
  return jsonFetch<PromptArchive>(url)
}
