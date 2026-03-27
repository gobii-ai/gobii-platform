import { describe, it, expect } from 'vitest'
import { QueryClient, type InfiniteData } from '@tanstack/react-query'

import { mergeLatestPageIntoTailAndDetectGap } from './useTimelineCacheInjector'
import { timelineQueryKey, type TimelinePage } from './useAgentTimeline'
import type { TimelineResponse } from '../api/agentChat'
import type { MessageEvent } from '../types/agentChat'

function makeMessageEvent(cursor: string): MessageEvent {
  return {
    kind: 'message',
    cursor,
    message: {
      id: cursor,
      role: 'assistant',
      content: 'test',
      timestamp: new Date().toISOString(),
      status: 'sent',
      error: null,
      metadata: null,
    } as unknown as MessageEvent['message'],
  }
}

function makePage(overrides: Partial<TimelinePage> = {}): TimelinePage {
  return {
    events: [],
    oldestCursor: null,
    newestCursor: null,
    hasMoreOlder: false,
    hasMoreNewer: false,
    raw: {} as TimelineResponse,
    ...overrides,
  }
}

function seedCache(queryClient: QueryClient, agentId: string, pages: TimelinePage[]) {
  const key = timelineQueryKey(agentId)
  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
    pages,
    pageParams: pages.map((_, i) => (i === 0 ? undefined : i)),
  })
}

describe('mergeLatestPageIntoTailAndDetectGap', () => {
  const agentId = 'agent-1'

  it('preserves server-reported hasMoreNewer=false even when cursors suggest a gap', () => {
    const queryClient = new QueryClient()
    const key = timelineQueryKey(agentId)

    // Existing cache page has cursor range 1-5
    const existingEvent = makeMessageEvent('5:message:1')
    const cachedPage = makePage({
      events: [existingEvent],
      oldestCursor: '1:message:1',
      newestCursor: '5:message:1',
      hasMoreNewer: false,
    })
    seedCache(queryClient, agentId, [cachedPage])

    // Server returns a page with cursor range 10-15, creating a "gap"
    // but server says hasMoreNewer=false (no more newer content)
    const latestEvent = makeMessageEvent('15:message:2')
    const latestPage = makePage({
      events: [latestEvent],
      oldestCursor: '10:message:2',
      newestCursor: '15:message:2',
      hasMoreNewer: false,
    })

    const result = mergeLatestPageIntoTailAndDetectGap(queryClient, key, latestPage)

    // The gap IS detected
    expect(result.hasNewerGap).toBe(true)

    // But the cached page should use the server value, not the gap heuristic
    const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(key)
    expect(data?.pages[0].hasMoreNewer).toBe(false)
  })

  it('preserves server-reported hasMoreNewer=true when server says there is more', () => {
    const queryClient = new QueryClient()
    const key = timelineQueryKey(agentId)

    const existingEvent = makeMessageEvent('5:message:1')
    const cachedPage = makePage({
      events: [existingEvent],
      oldestCursor: '1:message:1',
      newestCursor: '5:message:1',
      hasMoreNewer: false,
    })
    seedCache(queryClient, agentId, [cachedPage])

    // Server says there IS more newer content
    const latestEvent = makeMessageEvent('15:message:2')
    const latestPage = makePage({
      events: [latestEvent],
      oldestCursor: '10:message:2',
      newestCursor: '15:message:2',
      hasMoreNewer: true,
    })

    const result = mergeLatestPageIntoTailAndDetectGap(queryClient, key, latestPage)

    expect(result.hasNewerGap).toBe(true)
    const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(key)
    expect(data?.pages[0].hasMoreNewer).toBe(true)
  })

  it('handles empty cache by creating initial page with hasMoreNewer=false', () => {
    const queryClient = new QueryClient()
    const key = timelineQueryKey(agentId)

    const latestEvent = makeMessageEvent('5:message:1')
    const latestPage = makePage({
      events: [latestEvent],
      oldestCursor: '1:message:1',
      newestCursor: '5:message:1',
      hasMoreNewer: true,
    })

    mergeLatestPageIntoTailAndDetectGap(queryClient, key, latestPage)

    const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(key)
    // Empty cache path always sets hasMoreNewer=false
    expect(data?.pages[0].hasMoreNewer).toBe(false)
  })

  it('sets hasNewerGap=false when cursors overlap (no gap)', () => {
    const queryClient = new QueryClient()
    const key = timelineQueryKey(agentId)

    const existingEvent = makeMessageEvent('5:message:1')
    const cachedPage = makePage({
      events: [existingEvent],
      oldestCursor: '1:message:1',
      newestCursor: '5:message:1',
      hasMoreNewer: false,
    })
    seedCache(queryClient, agentId, [cachedPage])

    // Server returns overlapping cursor range (3-8), no gap
    const latestEvent = makeMessageEvent('8:message:2')
    const latestPage = makePage({
      events: [latestEvent],
      oldestCursor: '3:message:2',
      newestCursor: '8:message:2',
      hasMoreNewer: false,
    })

    const result = mergeLatestPageIntoTailAndDetectGap(queryClient, key, latestPage)

    expect(result.hasNewerGap).toBe(false)
    const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(key)
    expect(data?.pages[0].hasMoreNewer).toBe(false)
  })
})
