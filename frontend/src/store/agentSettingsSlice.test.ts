import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { updateAgent } from '../api/agents'
import { createAppStore } from './appStore'
import { agentSettingsActions, selectAgentSettingsState, updateAgentIntelligenceTier } from './agentSettingsSlice'

vi.mock('../api/agents', () => ({
  updateAgent: vi.fn(),
}))

vi.mock('../api/agentChat', () => ({
  fetchProcessingStatus: vi.fn(),
  sendAgentMessage: vi.fn(),
}))

describe('agentSettingsSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('stores draft tier for new-agent workflow', () => {
    const store = createAppStore()

    store.dispatch(agentSettingsActions.draftTierSet('advanced'))

    expect(selectAgentSettingsState(store.getState())).toMatchObject({
      draftTier: 'advanced',
      draftTierOverride: 'advanced',
    })

    store.dispatch(agentSettingsActions.draftTierReset())

    expect(selectAgentSettingsState(store.getState())).toMatchObject({
      draftTier: 'standard',
      draftTierOverride: null,
    })
  })

  it('optimistically saves intelligence tier and clears saving state', async () => {
    const queryClient = new QueryClient()
    vi.mocked(updateAgent).mockResolvedValue(undefined)
    const store = createAppStore({ queryClient })

    await store.dispatch(updateAgentIntelligenceTier({
      agentId: 'agent-1',
      tier: 'advanced',
      previousTier: 'standard',
    }))

    expect(updateAgent).toHaveBeenCalledWith('agent-1', { preferred_llm_tier: 'advanced' })
    expect(selectAgentSettingsState(store.getState())).toMatchObject({
      tierOverridesByAgentId: { 'agent-1': 'advanced' },
      savingByAgentId: { 'agent-1': false },
      errorByAgentId: { 'agent-1': null },
    })
  })

  it('rolls back intelligence tier on failure', async () => {
    vi.mocked(updateAgent).mockRejectedValue(new Error('nope'))
    const store = createAppStore()

    const result = await store.dispatch(updateAgentIntelligenceTier({
      agentId: 'agent-1',
      tier: 'advanced',
      previousTier: 'standard',
    }))

    expect(result).toBe(false)
    expect(selectAgentSettingsState(store.getState())).toMatchObject({
      tierOverridesByAgentId: { 'agent-1': 'standard' },
      savingByAgentId: { 'agent-1': false },
      errorByAgentId: { 'agent-1': 'Unable to update intelligence level.' },
    })
  })
})
