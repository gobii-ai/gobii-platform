import { describe, expect, it, vi } from 'vitest'

import {
  confirmAgentChatSocketSubscription,
  syncAgentChatSocketSubscriptions,
} from './agentChatSocketProtocol'

describe('agent chat socket subscription state', () => {
  it('does not send duplicate subscriptions for an already requested mode', () => {
    const currentSubscriptions = new Map([['agent-1', 'active' as const]])
    const sendSocketMessage = vi.fn(() => true)

    syncAgentChatSocketSubscriptions({
      currentSubscriptions,
      desiredSubscriptions: [{ agentId: 'agent-1', mode: 'active' }],
      contextOverride: null,
      sendSocketMessage,
      handleSendFailure: vi.fn(),
    })

    expect(sendSocketMessage).not.toHaveBeenCalled()
  })

  it('backfills once for each newly confirmed active subscription', () => {
    const requestedSubscriptions = new Map([['agent-1', 'active' as const]])
    const confirmedSubscriptions = new Map<string, 'active' | 'background'>()

    expect(confirmAgentChatSocketSubscription({
      requestedSubscriptions,
      confirmedSubscriptions,
      agentId: 'agent-1',
      mode: 'active',
    })).toEqual({ confirmed: true, shouldBackfill: true })
    expect(confirmAgentChatSocketSubscription({
      requestedSubscriptions,
      confirmedSubscriptions,
      agentId: 'agent-1',
      mode: 'active',
    })).toEqual({ confirmed: true, shouldBackfill: false })

    confirmedSubscriptions.clear()
    expect(confirmAgentChatSocketSubscription({
      requestedSubscriptions,
      confirmedSubscriptions,
      agentId: 'agent-1',
      mode: 'active',
    })).toEqual({ confirmed: true, shouldBackfill: true })
  })

  it('ignores a stale confirmation after the requested mode changes', () => {
    const requestedSubscriptions = new Map([['agent-1', 'background' as const]])
    const confirmedSubscriptions = new Map<string, 'active' | 'background'>()

    expect(confirmAgentChatSocketSubscription({
      requestedSubscriptions,
      confirmedSubscriptions,
      agentId: 'agent-1',
      mode: 'active',
    })).toEqual({ confirmed: false, shouldBackfill: false })
    expect(confirmedSubscriptions.size).toBe(0)
  })
})
