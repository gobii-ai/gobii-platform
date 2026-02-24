import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query'

import { fetchAgentTimeline, type TimelineResponse } from '../api/agentChat'
import type { TimelineEvent } from '../types/agentChat'
import { mergeTimelineEvents, prepareTimelineEvents } from '../stores/agentChatTimeline'

export const TIMELINE_PAGE_SIZE = 100

export type TimelinePage = {
  events: TimelineEvent[]
  oldestCursor: string | null
  newestCursor: string | null
  hasMoreOlder: boolean
  hasMoreNewer: boolean
  raw: TimelineResponse
}

export function timelineResponseToPage(response: TimelineResponse): TimelinePage {
  const events = prepareTimelineEvents(response.events)
  return {
    events,
    oldestCursor: events.length ? events[0].cursor : null,
    newestCursor: events.length ? events[events.length - 1].cursor : null,
    hasMoreOlder: response.has_more_older,
    hasMoreNewer: response.has_more_newer,
    raw: response,
  }
}

type PageParam = { direction: 'initial' } | { direction: 'older'; cursor: string } | { direction: 'newer'; cursor: string }

export function timelineQueryKey(agentId: string | null) {
  return ['agent-timeline', agentId] as const
}

export function useAgentTimeline(agentId: string | null, options?: { enabled?: boolean }) {
  return useInfiniteQuery<TimelinePage, Error, InfiniteData<TimelinePage>, ReturnType<typeof timelineQueryKey>, PageParam | undefined>({
    queryKey: timelineQueryKey(agentId),
    queryFn: async ({ pageParam }) => {
      if (!agentId) {
        throw new Error('No agentId')
      }

      const direction = pageParam?.direction ?? 'initial'

      if (direction === 'initial') {
        const response = await fetchAgentTimeline(agentId, { direction: 'initial', limit: TIMELINE_PAGE_SIZE })
        return timelineResponseToPage(response)
      }

      if (direction === 'older' && 'cursor' in pageParam!) {
        const response = await fetchAgentTimeline(agentId, { direction: 'older', cursor: pageParam.cursor, limit: TIMELINE_PAGE_SIZE })
        return timelineResponseToPage(response)
      }

      if (direction === 'newer' && 'cursor' in pageParam!) {
        const response = await fetchAgentTimeline(agentId, { direction: 'newer', cursor: pageParam.cursor, limit: TIMELINE_PAGE_SIZE })
        return timelineResponseToPage(response)
      }

      throw new Error(`Invalid page param direction: ${direction}`)
    },
    initialPageParam: undefined,
    getPreviousPageParam: (firstPage) => {
      if (!firstPage.hasMoreOlder || !firstPage.oldestCursor) {
        return undefined
      }
      return { direction: 'older' as const, cursor: firstPage.oldestCursor }
    },
    getNextPageParam: (lastPage) => {
      if (!lastPage.hasMoreNewer || !lastPage.newestCursor) {
        return undefined
      }
      return { direction: 'newer' as const, cursor: lastPage.newestCursor }
    },
    enabled: Boolean(agentId) && (options?.enabled !== false),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })
}

export function flattenTimelinePages(data: InfiniteData<TimelinePage> | undefined): TimelineEvent[] {
  if (!data?.pages?.length) {
    return []
  }
  if (data.pages.length === 1) {
    return data.pages[0].events
  }
  // Merge all pages' events together to handle dedup and sorting
  let merged: TimelineEvent[] = []
  for (const page of data.pages) {
    merged = mergeTimelineEvents(merged, page.events)
  }
  return merged
}

/**
 * Get the initial page's raw response (for processing snapshot, agent metadata etc.)
 */
export function getInitialPageResponse(data: InfiniteData<TimelinePage> | undefined): TimelineResponse | null {
  if (!data?.pages?.length) {
    return null
  }
  // The initial page is the first page fetched (middle of pages array after prepending older pages)
  // But for metadata, the most recent fetch is most authoritative — use the last page
  const lastPage = data.pages[data.pages.length - 1]
  return lastPage?.raw ?? null
}
