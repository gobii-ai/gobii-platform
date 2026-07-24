import { jsonFetch } from './http'

export type MessageAttachmentFilter = 'any' | 'attachment' | 'image' | 'file'

export type MessageSearchExcerptSegment = {
  text: string
  highlighted: boolean
}

export type AgentMessageSearchResult = {
  message_id: string
  timestamp: string
  excerpt: MessageSearchExcerptSegment[]
  attachment_count: number
  has_images: boolean
  agent: {
    id: string
    name: string
    avatar_url: string | null
  }
}

export type AgentMessageSearchResponse = {
  results: AgentMessageSearchResult[]
  next_cursor: string | null
}

export type AgentMessageSearchFilters = {
  q: string
  agentId: string | null
  attachment: MessageAttachmentFilter
}

export async function fetchAgentMessageSearch(
  filters: AgentMessageSearchFilters,
  options: { cursor?: string | null; signal?: AbortSignal } = {},
): Promise<AgentMessageSearchResponse> {
  const query = new URLSearchParams()
  if (filters.q.trim()) query.set('q', filters.q.trim())
  if (filters.agentId) query.set('agent_id', filters.agentId)
  query.set('attachment', filters.attachment)
  if (options.cursor) query.set('cursor', options.cursor)
  query.set('limit', '30')
  return jsonFetch<AgentMessageSearchResponse>(
    `/console/api/agent-messages/search/?${query.toString()}`,
    options.signal ? { signal: options.signal } : undefined,
  )
}
