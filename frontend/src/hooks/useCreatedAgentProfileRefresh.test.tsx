import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgentRosterEntry } from '../types/agentRoster'
import { useCreatedAgentProfileRefresh } from './useCreatedAgentProfileRefresh'

const { fetchAgentProfileMock } = vi.hoisted(() => ({
  fetchAgentProfileMock: vi.fn(),
}))

vi.mock('../api/agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agents')>()),
  fetchAgentProfile: fetchAgentProfileMock,
}))

function profile(avatarUrl: string | null): AgentRosterEntry {
  return {
    id: 'agent-1',
    name: 'Agent',
    avatarUrl,
    isActive: true,
    processingActive: false,
    lastInteractionAt: null,
    miniDescription: '',
    shortDescription: '',
    listingDescription: '',
    listingDescriptionSource: null,
    displayTags: [],
    detailUrl: null,
    dailyCreditRemaining: null,
    dailyCreditLow: false,
    last24hCreditBurn: null,
  }
}

describe('useCreatedAgentProfileRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    fetchAgentProfileMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('backs off targeted refreshes and stops when the avatar arrives', async () => {
    const onProfile = vi.fn()
    fetchAgentProfileMock
      .mockResolvedValueOnce(profile(null))
      .mockResolvedValueOnce(profile('/avatar.png'))

    renderHook(() => useCreatedAgentProfileRefresh({
      agentId: 'agent-1',
      avatarUrl: null,
      onProfile,
    }))

    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(fetchAgentProfileMock).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(fetchAgentProfileMock).toHaveBeenCalledTimes(2)
    expect(onProfile).toHaveBeenLastCalledWith(expect.objectContaining({ avatarUrl: '/avatar.png' }))

    await act(() => vi.advanceTimersByTimeAsync(90_000))
    expect(fetchAgentProfileMock).toHaveBeenCalledTimes(2)
  })

  it('cancels pending refreshes when the selected agent changes', async () => {
    const onProfile = vi.fn()
    const { rerender } = renderHook(
      ({ agentId }) => useCreatedAgentProfileRefresh({ agentId, avatarUrl: null, onProfile }),
      { initialProps: { agentId: 'agent-1' as string | null } },
    )

    rerender({ agentId: null })
    await act(() => vi.advanceTimersByTimeAsync(90_000))

    expect(fetchAgentProfileMock).not.toHaveBeenCalled()
  })
})

