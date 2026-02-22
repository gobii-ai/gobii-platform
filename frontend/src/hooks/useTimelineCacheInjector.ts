import type { QueryClient, InfiniteData } from '@tanstack/react-query'

import type { TimelineEvent } from '../types/agentChat'
import { mergeTimelineEvents } from '../stores/agentChatTimeline'
import { timelineQueryKey, type TimelinePage } from './useAgentTimeline'

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
