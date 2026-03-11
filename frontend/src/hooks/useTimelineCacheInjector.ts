import type { QueryClient, InfiniteData } from '@tanstack/react-query'

import { fetchAgentTimeline } from '../api/agentChat'
import type { PendingHumanInputRequest, TimelineEvent } from '../types/agentChat'
import { compareTimelineCursors } from '../util/timelineCursor'
import { mergeTimelineEvents } from '../stores/agentChatTimeline'
import {
  timelineQueryKey,
  timelineResponseToPage,
  TIMELINE_PAGE_SIZE,
  type TimelinePage,
} from './useAgentTimeline'

export const DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES = 20

export type RefreshTimelineMode = 'fast' | 'contiguous'

export type RefreshTimelineOptions = {
  mode?: RefreshTimelineMode
  maxNewerPages?: number
}

export type RefreshTimelineResult = {
  newerPagesFetched: number
  remainingNewerGap: boolean
}

/**
 * Inject a single real-time event into the last page of the react-query timeline cache.
 * Uses mergeTimelineEvents for dedup and ordering.
 */
export function injectRealtimeEventIntoCache(
  queryClient: QueryClient,
  agentId: string,
  event: TimelineEvent,
) {
  injectEventsIntoCache(queryClient, agentId, [event])
}

/**
 * Batch inject events (e.g. pending events flush on repin) into the last page.
 */
export function flushPendingEventsToCache(
  queryClient: QueryClient,
  agentId: string,
  events: TimelineEvent[],
) {
  if (!events.length) {
    return
  }
  injectEventsIntoCache(queryClient, agentId, events)
}

export function replacePendingHumanInputRequestsInCache(
  queryClient: QueryClient,
  agentId: string,
  pendingHumanInputRequests: PendingHumanInputRequest[],
) {
  const key = timelineQueryKey(agentId)
  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return old
    }

    const pages = [...old.pages]
    const lastIndex = pages.length - 1
    const lastPage = pages[lastIndex]
    pages[lastIndex] = {
      ...lastPage,
      raw: {
        ...lastPage.raw,
        pending_human_input_requests: pendingHumanInputRequests,
      },
    }

    return {
      ...old,
      pages,
    }
  })
}

function injectEventsIntoCache(
  queryClient: QueryClient,
  agentId: string,
  incoming: TimelineEvent[],
) {
  const key = timelineQueryKey(agentId)

  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return old
    }

    const pages = [...old.pages]
    const lastIndex = pages.length - 1
    const lastPage = pages[lastIndex]

    const merged = mergeTimelineEvents(lastPage.events, incoming)
    const newestCursor = merged.length ? merged[merged.length - 1].cursor : lastPage.newestCursor
    const oldestCursor = merged.length ? merged[0].cursor : lastPage.oldestCursor

    pages[lastIndex] = {
      ...lastPage,
      events: merged,
      newestCursor,
      oldestCursor,
    }

    return {
      ...old,
      pages,
    }
  })
}

/**
 * Remove an optimistic event from the cache by clientId.
 * Returns true if found and removed.
 */
export function updateOptimisticEventInCache(
  queryClient: QueryClient,
  agentId: string,
  clientId: string,
  status: 'sending' | 'failed',
  error?: string,
): boolean {
  const key = timelineQueryKey(agentId)
  let found = false

  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return old
    }

    const pages = [...old.pages]
    for (let pageIdx = pages.length - 1; pageIdx >= 0; pageIdx--) {
      const page = pages[pageIdx]
      const eventIdx = page.events.findIndex(
        (event) => event.kind === 'message' && event.message.clientId === clientId,
      )
      if (eventIdx < 0) {
        continue
      }

      found = true
      const target = page.events[eventIdx]
      if (target.kind !== 'message') {
        break
      }

      const nextEvents = [...page.events]
      nextEvents[eventIdx] = {
        ...target,
        message: {
          ...target.message,
          status,
          error: error ?? target.message.error ?? null,
        },
      }
      pages[pageIdx] = { ...page, events: nextEvents }
      break
    }

    return found ? { ...old, pages } : old
  })

  return found
}

function hasCursorAdvanced(previous: string | null, next: string | null): boolean {
  if (!next) {
    return false
  }
  if (!previous) {
    return true
  }
  return compareTimelineCursors(next, previous) > 0
}

function mergeLatestPageIntoTailAndDetectGap(
  queryClient: QueryClient,
  key: ReturnType<typeof timelineQueryKey>,
  latestPage: TimelinePage,
): { hasNewerGap: boolean; newestCursor: string | null } {
  let hasNewerGap = false
  let newestCursor: string | null = latestPage.newestCursor

  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return {
        pages: [{ ...latestPage, hasMoreNewer: false }],
        pageParams: [undefined],
      }
    }

    const pages = [...old.pages]
    const lastIndex = pages.length - 1
    const lastPage = pages[lastIndex]
    const merged = mergeTimelineEvents(lastPage.events, latestPage.events)

    hasNewerGap = Boolean(
      lastPage.newestCursor
      && latestPage.oldestCursor
      && compareTimelineCursors(lastPage.newestCursor, latestPage.oldestCursor) < 0,
    )

    const nextNewestCursor = merged.length
      ? merged[merged.length - 1].cursor
      : latestPage.newestCursor ?? lastPage.newestCursor
    const nextOldestCursor = merged.length
      ? merged[0].cursor
      : latestPage.oldestCursor ?? lastPage.oldestCursor
    newestCursor = nextNewestCursor

    pages[lastIndex] = {
      ...lastPage,
      events: merged,
      newestCursor: nextNewestCursor,
      oldestCursor: nextOldestCursor,
      hasMoreNewer: hasNewerGap,
      raw: latestPage.raw,
    }

    return {
      ...old,
      pages,
    }
  })

  return { hasNewerGap, newestCursor }
}

function mergeNewerPageIntoTail(
  queryClient: QueryClient,
  key: ReturnType<typeof timelineQueryKey>,
  newerPage: TimelinePage,
): { newestCursor: string | null; advanced: boolean } {
  let newestCursor: string | null = newerPage.newestCursor
  let advanced = false

  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return {
        pages: [{ ...newerPage, hasMoreNewer: newerPage.hasMoreNewer }],
        pageParams: [undefined],
      }
    }

    const pages = [...old.pages]
    const lastIndex = pages.length - 1
    const lastPage = pages[lastIndex]
    const previousNewestCursor = lastPage.newestCursor
    const merged = mergeTimelineEvents(lastPage.events, newerPage.events)
    const nextNewestCursor = merged.length
      ? merged[merged.length - 1].cursor
      : newerPage.newestCursor ?? lastPage.newestCursor
    const nextOldestCursor = merged.length
      ? merged[0].cursor
      : newerPage.oldestCursor ?? lastPage.oldestCursor

    newestCursor = nextNewestCursor
    advanced = hasCursorAdvanced(previousNewestCursor, nextNewestCursor)

    pages[lastIndex] = {
      ...lastPage,
      events: merged,
      newestCursor: nextNewestCursor,
      oldestCursor: nextOldestCursor,
      hasMoreNewer: newerPage.hasMoreNewer,
      raw: newerPage.raw,
    }

    return {
      ...old,
      pages,
    }
  })

  return { newestCursor, advanced }
}

/**
 * Refresh the latest timeline slice from the server and merge it into the cache tail.
 * This avoids infinite-query refetch drift when older pages are currently loaded.
 */
export async function refreshTimelineLatestInCache(
  queryClient: QueryClient,
  agentId: string,
  options?: RefreshTimelineOptions,
): Promise<RefreshTimelineResult> {
  const mode = options?.mode ?? 'fast'
  const maxNewerPages = Math.max(1, options?.maxNewerPages ?? DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES)
  const key = timelineQueryKey(agentId)
  let newerPagesFetched = 0
  let remainingNewerGap = false

  try {
    const response = await fetchAgentTimeline(agentId, {
      direction: 'initial',
      limit: TIMELINE_PAGE_SIZE,
    })
    const latestPage = timelineResponseToPage(response)
    const latestMerge = mergeLatestPageIntoTailAndDetectGap(queryClient, key, latestPage)
    remainingNewerGap = latestMerge.hasNewerGap
    let cursor: string | null = latestMerge.newestCursor

    if (mode === 'contiguous' && remainingNewerGap && cursor) {
      while (remainingNewerGap && cursor && newerPagesFetched < maxNewerPages) {
        const previousCursor: string = cursor
        const newerResponse = await fetchAgentTimeline(agentId, {
          direction: 'newer',
          cursor,
          limit: TIMELINE_PAGE_SIZE,
        })
        const newerPage = timelineResponseToPage(newerResponse)
        const newerMerge = mergeNewerPageIntoTail(queryClient, key, newerPage)

        newerPagesFetched += 1
        cursor = newerMerge.newestCursor
        remainingNewerGap = newerPage.hasMoreNewer

        if (!newerMerge.advanced || !cursor || cursor === previousCursor) {
          break
        }
      }
    }

    return {
      newerPagesFetched,
      remainingNewerGap,
    }
  } catch (error) {
    console.error('Failed to refresh latest timeline cache:', error)
    return {
      newerPagesFetched,
      remainingNewerGap: true,
    }
  }
}
