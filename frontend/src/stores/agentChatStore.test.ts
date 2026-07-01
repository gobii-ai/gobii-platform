import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TimelineEvent } from '../types/agentChat'

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
}))

import { sendAgentMessage } from '../api/agentChat'
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
      todayUsage: { used: 1, limit: 3, percentUsed: 33, unlimited: false },
      monthUsage: { used: 2, limit: 10, percentUsed: 20, unlimited: false },
    },
    dismissible: true,
  }
}

const initialState = useAgentChatStore.getState()

describe('agentChatStore insight state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAgentChatStore.setState(initialState, true)
  })

  it('applies insight query results for the current agent', () => {
    useAgentChatStore.getState().setAgentId('agent-1')
    useAgentChatStore.getState().setInsightsForAgent('agent-1', [makeInsight('insight-1', 'First insight')])

    const state = useAgentChatStore.getState()
    expect(state.insights).toEqual([makeInsight('insight-1', 'First insight')])
    expect(state.currentInsightIndex).toBe(0)
  })

  it('ignores stale insight results after switching agents', () => {
    useAgentChatStore.getState().setAgentId('agent-1')
    useAgentChatStore.getState().setAgentId('agent-2')
    useAgentChatStore.getState().setInsightsForAgent('agent-1', [makeInsight('stale-insight', 'Stale insight')])

    expect(useAgentChatStore.getState().insights).toEqual([])

    useAgentChatStore.getState().setInsightsForAgent('agent-2', [makeInsight('fresh-insight', 'Fresh insight')])

    const state = useAgentChatStore.getState()
    expect(state.insights).toEqual([makeInsight('fresh-insight', 'Fresh insight')])
    expect(state.agentId).toBe('agent-2')
  })
})

describe('agentChatStore message sending', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAgentChatStore.setState(initialState, true)
  })

  it('adds an optimistic sending message before the backend send resolves', async () => {
    let resolveSend: (event: TimelineEvent) => void = () => {}
    vi.mocked(sendAgentMessage).mockReturnValue(new Promise<TimelineEvent>((resolve) => {
      resolveSend = resolve
    }))
    useAgentChatStore.getState().setAgentId('agent-1')
    useAgentChatStore.getState().setAutoScrollPinned(false)

    const sendResult = useAgentChatStore.getState().sendMessage('hello backend')

    expect(sendAgentMessage).toHaveBeenCalledWith('agent-1', 'hello backend', [])
    const pendingEvents = useAgentChatStore.getState().pendingEvents
    expect(pendingEvents).toHaveLength(1)
    expect(pendingEvents[0]).toMatchObject({
      kind: 'message',
      message: {
        bodyText: 'hello backend',
        status: 'sending',
      },
    })
    expect(useAgentChatStore.getState().awaitingResponse).toBe(true)

    resolveSend({
      kind: 'message',
      cursor: 'server-message-cursor',
      message: {
        id: 'server-message',
        bodyText: 'hello backend',
        isOutbound: false,
        channel: 'web',
        timestamp: new Date().toISOString(),
        relativeTimestamp: null,
      },
    })
    await sendResult
  })
})
