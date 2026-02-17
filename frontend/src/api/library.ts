import { jsonFetch, jsonRequest } from './http'

export type LibraryAgent = {
  id: string
  name: string
  tagline: string
  description: string
  category: string
  publicProfileHandle: string
  templateSlug: string
  templateUrl: string
  likeCount: number
  isLiked: boolean
}

export type LibraryCategory = {
  name: string
  count: number
}

export type LibraryAgentsPayload = {
  agents: LibraryAgent[]
  topCategories: LibraryCategory[]
  totalAgents: number
  libraryTotalAgents: number
  libraryTotalLikes: number
  offset: number
  limit: number
  hasMore: boolean
}

export type LibraryAgentLikePayload = {
  agentId: string
  isLiked: boolean
  likeCount: number
}

type FetchLibraryAgentsOptions = {
  offset?: number
  limit?: number
  category?: string | null
  query?: string | null
  signal?: AbortSignal
}

export function fetchLibraryAgents(listUrl: string, options: FetchLibraryAgentsOptions = {}): Promise<LibraryAgentsPayload> {
  const params = new URLSearchParams()
  params.set('offset', String(options.offset ?? 0))
  params.set('limit', String(options.limit ?? 24))
  if (options.category) {
    params.set('category', options.category)
  }
  if (options.query?.trim()) {
    params.set('q', options.query.trim())
  }

  const separator = listUrl.includes('?') ? '&' : '?'
  const requestUrl = `${listUrl}${separator}${params.toString()}`
  return jsonFetch<LibraryAgentsPayload>(requestUrl, { signal: options.signal })
}

export function toggleLibraryAgentLike(likeUrl: string, agentId: string): Promise<LibraryAgentLikePayload> {
  return jsonRequest<LibraryAgentLikePayload>(likeUrl, {
    method: 'POST',
    includeCsrf: true,
    json: { agentId },
  })
}
