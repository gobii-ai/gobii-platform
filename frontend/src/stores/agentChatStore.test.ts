import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
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
