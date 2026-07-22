import { QueryClient, type InfiniteData } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TimelineResponse } from '../api/agentChat'
import { injectRealtimeEventIntoCache, refreshLoadedTimelineVariantsInCache, refreshTimelineLatestInCache, replacePendingActionRequestsInCache } from './useTimelineCacheInjector'
import { timelineQueryKey, timelineResponseToPage, type TimelinePage } from './useAgentTimeline'

const { fetchAgentTimelineMock } = vi.hoisted(() => ({
  fetchAgentTimelineMock: vi.fn(),
}))

vi.mock('../api/agentChat', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agentChat')>()),
  fetchAgentTimeline: fetchAgentTimelineMock,
}))

const emptyTimelineResponse: TimelineResponse = {
  events: [],
  has_more_older: false,
  has_more_newer: false,
  processing_active: false,
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('refreshTimelineLatestInCache', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    fetchAgentTimelineMock.mockReset()
  })

  it('coalesces concurrent latest refreshes for one agent', async () => {
    const request = deferred<TimelineResponse>()
    fetchAgentTimelineMock.mockReturnValue(request.promise)

    const first = refreshTimelineLatestInCache(queryClient, 'agent-1')
    const second = refreshTimelineLatestInCache(queryClient, 'agent-1')

    expect(fetchAgentTimelineMock).toHaveBeenCalledTimes(1)
    request.resolve(emptyTimelineResponse)
    await expect(Promise.all([first, second])).resolves.toEqual([
      { newerPagesFetched: 0, remainingNewerGap: false },
      { newerPagesFetched: 0, remainingNewerGap: false },
    ])
  })

  it('skips recovery when the cache is newer than the triggering stream state', async () => {
    queryClient.setQueryData(timelineQueryKey('agent-1'), {
      pages: [],
      pageParams: [],
    })
    const updatedAt = queryClient.getQueryState(timelineQueryKey('agent-1'))?.dataUpdatedAt ?? 0

    await expect(refreshTimelineLatestInCache(queryClient, 'agent-1', {
      minimumUpdatedAt: updatedAt,
    })).resolves.toEqual({ newerPagesFetched: 0, remainingNewerGap: false })

    expect(fetchAgentTimelineMock).not.toHaveBeenCalled()
  })

  it('does not overlap an initial timeline query', async () => {
    const initialRequest = deferred<string>()
    const initial = queryClient.fetchQuery({
      queryKey: timelineQueryKey('agent-1'),
      queryFn: () => initialRequest.promise,
    })

    await expect(refreshTimelineLatestInCache(queryClient, 'agent-1')).resolves.toEqual({
      newerPagesFetched: 0,
      remainingNewerGap: false,
    })
    expect(fetchAgentTimelineMock).not.toHaveBeenCalled()

    initialRequest.resolve('loaded')
    await initial
  })

  it('refreshes and merges the staff-context developer timeline', async () => {
    const staffContext = { type: 'organization' as const, id: 'org-1' }
    const key = timelineQueryKey('agent-1', true, staffContext)
    queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
      pages: [timelineResponseToPage(emptyTimelineResponse)],
      pageParams: [undefined],
    })
    fetchAgentTimelineMock.mockResolvedValue({
      ...emptyTimelineResponse,
      events: [{
        kind: 'developer_error',
        cursor: '200:error:error-1',
        id: 'error-1',
        timestamp: '2026-07-15T12:00:00Z',
        category: 'OTHER',
        source: 'tests.realtime',
        level: 'ERROR',
        message: 'Realtime failure',
        exception_class: '',
        traceback: '',
        context: {},
        completion_id: null,
      }],
    } satisfies TimelineResponse)

    await refreshTimelineLatestInCache(queryClient, 'agent-1', {
      developerMode: true,
      staffContext,
      allowDuringQueryFetch: true,
    })

    expect(fetchAgentTimelineMock).toHaveBeenCalledWith('agent-1', expect.objectContaining({
      developerMode: true,
      staffContext,
    }))
    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(key)?.pages[0].events).toEqual([
      expect.objectContaining({ kind: 'developer_error', id: 'error-1' }),
    ])
  })

  it('preserves a realtime pending action received while an older timeline request is in flight', async () => {
    const key = timelineQueryKey('agent-1')
    queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
      pages: [timelineResponseToPage(emptyTimelineResponse)],
      pageParams: [undefined],
    })
    const request = deferred<TimelineResponse>()
    fetchAgentTimelineMock.mockReturnValue(request.promise)

    const refresh = refreshTimelineLatestInCache(queryClient, 'agent-1')
    replacePendingActionRequestsInCache(queryClient, 'agent-1', [{
      id: 'spawn:request-1',
      kind: 'spawn_request',
      requestId: 'request-1',
      requestedCharter: 'Research the market',
    }])
    request.resolve({ ...emptyTimelineResponse, pending_action_requests: [] })
    await refresh

    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(key)?.pages[0].raw.pending_action_requests).toEqual([
      expect.objectContaining({ id: 'spawn:request-1' }),
    ])
  })

  it('updates pending metadata in every developer and staff timeline scope for an agent', () => {
    const staffContext = { type: 'organization' as const, id: 'org-1' }
    const standardKey = timelineQueryKey('agent-1')
    const scopedKey = timelineQueryKey('agent-1', true, staffContext)
    for (const key of [standardKey, scopedKey]) {
      queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
        pages: [timelineResponseToPage(emptyTimelineResponse)],
        pageParams: [undefined],
      })
    }

    replacePendingActionRequestsInCache(queryClient, 'agent-1', [{
      id: 'spawn:request-1',
      kind: 'spawn_request',
      requestId: 'request-1',
      requestedCharter: 'Research the market',
    }])

    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(standardKey)?.pages[0].raw.pending_action_requests).toHaveLength(1)
    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(scopedKey)?.pages[0].raw.pending_action_requests).toHaveLength(1)
  })

  it('rejects an older mutation snapshot in timeline and roster caches', () => {
    const key = timelineQueryKey('agent-1')
    queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
      pages: [timelineResponseToPage(emptyTimelineResponse, 1)],
      pageParams: [undefined],
    })
    queryClient.setQueryData(['agent-roster'], {
      agents: [{ id: 'agent-1', pendingActionRequestCount: 0 }],
    })
    const newerPendingActions = [{
      id: 'spawn:request-1',
      kind: 'spawn_request' as const,
      requestId: 'request-1',
      requestedCharter: 'Research the market',
    }]

    replacePendingActionRequestsInCache(queryClient, 'agent-1', newerPendingActions, 20)
    replacePendingActionRequestsInCache(queryClient, 'agent-1', [], 10)

    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(key)?.pages[0].raw.pending_action_requests).toEqual(newerPendingActions)
    expect(queryClient.getQueryData<{ agents: Array<{ pendingActionRequestCount: number }> }>(['agent-roster'])?.agents[0].pendingActionRequestCount).toBe(1)
  })

  it('injects finalized events into every loaded variant for the addressed agent', () => {
    const standardKey = timelineQueryKey('agent-1')
    const developerKey = timelineQueryKey('agent-1', true)
    const staffKey = timelineQueryKey('agent-1', false, { type: 'organization', id: 'org-1' })
    const otherAgentKey = timelineQueryKey('agent-2')
    for (const key of [standardKey, developerKey, staffKey, otherAgentKey]) {
      queryClient.setQueryData<InfiniteData<TimelinePage>>(key, {
        pages: [timelineResponseToPage(emptyTimelineResponse)],
        pageParams: [undefined],
      })
    }
    const event = {
      kind: 'steps' as const,
      cursor: '100:step:config-step',
      entryCount: 1,
      collapsible: false,
      collapseThreshold: 3,
      entries: [{
        id: 'config-step',
        cursor: '100:step:config-step',
        meta: { label: 'Database query' },
        status: 'complete' as const,
        charterText: 'Persisted assignment',
      }],
    }

    injectRealtimeEventIntoCache(queryClient, 'agent-1', event)

    for (const key of [standardKey, developerKey, staffKey]) {
      expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(key)?.pages[0].events).toEqual([
        expect.objectContaining({ cursor: event.cursor }),
      ])
    }
    expect(queryClient.getQueryData<InfiniteData<TimelinePage>>(otherAgentKey)?.pages[0].events).toEqual([])
  })

  it('refreshes every loaded timeline variant after processing completes', async () => {
    const staffContext = { type: 'personal' as const, id: 'user-1' }
    const agentKeys = [
      timelineQueryKey('agent-1'),
      timelineQueryKey('agent-1', true),
      timelineQueryKey('agent-1', true, staffContext),
    ]
    const otherAgentKey = timelineQueryKey('agent-2')
    const fetchedKeys: string[] = []
    for (const key of [...agentKeys, otherAgentKey]) {
      await queryClient.fetchInfiniteQuery({
        queryKey: key,
        queryFn: async () => {
          fetchedKeys.push(JSON.stringify(key))
          return timelineResponseToPage(emptyTimelineResponse)
        },
        initialPageParam: undefined,
        getNextPageParam: () => undefined,
      })
    }
    fetchedKeys.length = 0

    await refreshLoadedTimelineVariantsInCache(queryClient, 'agent-1')

    expect(fetchedKeys).toHaveLength(3)
    expect(fetchedKeys).toEqual(expect.arrayContaining(agentKeys.map((key) => JSON.stringify(key))))
    expect(fetchedKeys).not.toContain(JSON.stringify(otherAgentKey))
  })
})
