import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchAgentInsightsMock } = vi.hoisted(() => ({
  fetchAgentInsightsMock: vi.fn(),
}))

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
  fetchAgentInsights: fetchAgentInsightsMock,
}))

import { useAgentChatStore } from './agentChatStore'

function makeInsight(insightId: string, title: string) {
  return {
    insightId,
    insightType: 'burn_rate' as const,
    priority: 5,
    title,
    body: `${title} body`,
    metadata: {
      agentName: 'Agent',
      agentCreditsPerHour: 1,
      allAgentsCreditsPerDay: 2,
      dailyLimit: 3,
      percentUsed: 4,
    },
    dismissible: true,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const initialState = useAgentChatStore.getState()

describe('agentChatStore insight fetching', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAgentChatStore.setState(initialState, true)
  })

  it('dedupes concurrent insight fetches for the same agent', async () => {
    const pending = deferred<{ insights: ReturnType<typeof makeInsight>[]; refreshAfterSeconds: number }>()
    fetchAgentInsightsMock.mockReturnValueOnce(pending.promise)

    useAgentChatStore.getState().setAgentId('agent-1')

    const first = useAgentChatStore.getState().fetchInsights()
    const second = useAgentChatStore.getState().fetchInsights()

    expect(fetchAgentInsightsMock).toHaveBeenCalledTimes(1)

    pending.resolve({
      insights: [makeInsight('insight-1', 'First insight')],
      refreshAfterSeconds: 300,
    })

    await Promise.all([first, second])

    const state = useAgentChatStore.getState()
    expect(state.insights).toEqual([makeInsight('insight-1', 'First insight')])
    expect(state.insightsFetchInFlight).toBeNull()
  })

  it('ignores stale insight responses after switching agents', async () => {
    const firstPending = deferred<{ insights: ReturnType<typeof makeInsight>[]; refreshAfterSeconds: number }>()
    const secondPending = deferred<{ insights: ReturnType<typeof makeInsight>[]; refreshAfterSeconds: number }>()
    fetchAgentInsightsMock
      .mockReturnValueOnce(firstPending.promise)
      .mockReturnValueOnce(secondPending.promise)

    useAgentChatStore.getState().setAgentId('agent-1')
    const first = useAgentChatStore.getState().fetchInsights()

    useAgentChatStore.getState().setAgentId('agent-2')
    const second = useAgentChatStore.getState().fetchInsights()

    firstPending.resolve({
      insights: [makeInsight('stale-insight', 'Stale insight')],
      refreshAfterSeconds: 300,
    })
    await first

    expect(useAgentChatStore.getState().insights).toEqual([])

    secondPending.resolve({
      insights: [makeInsight('fresh-insight', 'Fresh insight')],
      refreshAfterSeconds: 300,
    })
    await second

    const state = useAgentChatStore.getState()
    expect(state.insights).toEqual([makeInsight('fresh-insight', 'Fresh insight')])
    expect(state.agentId).toBe('agent-2')
    expect(state.insightsFetchInFlight).toBeNull()
  })
})
