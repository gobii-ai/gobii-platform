import { QueryClient, type InfiniteData } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TimelineResponse } from '../api/agentChat'
import { refreshTimelineLatestInCache } from './useTimelineCacheInjector'
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
})
