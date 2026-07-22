import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import type { PendingActionRequest, ProcessingSnapshot, StreamEventPayload, TimelineEvent } from '../types/agentChat'
import { routeAgentChatSocketMessage } from './agentChatSocketMessageRouter'

function createCallbacks() {
  return {
    receiveRealtimeEvent: vi.fn<(agentId: string, event: TimelineEvent) => void>(),
    updateProcessing: vi.fn<(agentId: string, snapshot: ProcessingSnapshot) => void>(),
    updateAgentIdentity: vi.fn(),
    updateUsageInsight: vi.fn(),
    receiveStreamEvent: vi.fn<(agentId: string, payload: StreamEventPayload) => void>(),
    replacePendingActions: vi.fn<(
      agentId: string,
      pendingActions: PendingActionRequest[],
      stateOrder: number,
    ) => void>(),
  }
}

describe('routeAgentChatSocketMessage', () => {
  it('applies a ready snapshot and returns the confirmed subscription mode', () => {
    const callbacks = createCallbacks()
    const processingSnapshot = { active: true, webTasks: [], nextScheduledAt: null }
    const outcome = routeAgentChatSocketMessage({
      payload: {
        type: 'subscription.ready',
        agent_id: 'agent-2',
        mode: 'background',
        payload: {
          processing_snapshot: processingSnapshot,
          pending_action_requests: [{
            id: 'spawn:request-1',
            kind: 'spawn_request',
            requestId: 'request-1',
            requestedCharter: 'Research the market',
          }],
        },
      },
      queryClient: new QueryClient(),
      activeAgentId: 'agent-1',
      ...callbacks,
    })

    expect(outcome).toEqual({
      type: 'subscription_ready',
      agentId: 'agent-2',
      mode: 'background',
    })
    expect(callbacks.updateProcessing).toHaveBeenCalledWith('agent-2', processingSnapshot)
    expect(callbacks.replacePendingActions).toHaveBeenCalledWith(
      'agent-2',
      [expect.objectContaining({ id: 'spawn:request-1', kind: 'spawn_request' })],
      expect.any(Number),
    )
  })

  it('keeps processing updates scoped to their envelope agent, including background agents', () => {
    const callbacks = createCallbacks()
    const snapshot = { active: true, webTasks: [] }

    routeAgentChatSocketMessage({
      payload: { type: 'processing', agent_id: 'agent-2', payload: snapshot },
      queryClient: new QueryClient(),
      activeAgentId: 'agent-1',
      ...callbacks,
    })

    expect(callbacks.updateProcessing).toHaveBeenCalledWith('agent-2', snapshot)
  })

  it('passes the envelope agent through background timeline and stream callbacks', () => {
    const callbacks = createCallbacks()
    const event = {
      kind: 'message',
      cursor: '1:message:one',
      message: {
        id: 'one',
        bodyText: 'Hello',
        isOutbound: true,
        channel: 'web',
        timestamp: '2026-07-21T12:00:00Z',
        relativeTimestamp: null,
      },
    } as TimelineEvent
    const stream = { stream_id: 'stream-1', status: 'start' } as StreamEventPayload
    const queryClient = new QueryClient()

    routeAgentChatSocketMessage({
      payload: { type: 'timeline.event', agent_id: 'agent-2', payload: event },
      queryClient,
      activeAgentId: 'agent-1',
      ...callbacks,
    })
    routeAgentChatSocketMessage({
      payload: { type: 'stream.event', agent_id: 'agent-2', payload: stream },
      queryClient,
      activeAgentId: 'agent-1',
      ...callbacks,
    })

    expect(callbacks.receiveRealtimeEvent).toHaveBeenCalledWith('agent-2', event)
    expect(callbacks.receiveStreamEvent).toHaveBeenCalledWith('agent-2', stream)
  })
})
