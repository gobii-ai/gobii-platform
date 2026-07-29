/**
 * #510: the stream card must hand off to the persisted message without a blank frame.
 * Clearing the stream the moment the outbound message event arrived unmounted the card
 * one commit before the timeline painted the message — the reply visibly disappeared
 * and re-rendered from scratch.
 */
import { describe, expect, it } from 'vitest'

import type { TimelineEvent } from '../types/agentChat'
import { createAppStore } from './appStore'
import { chatActions, receiveStreamEvent, selectActiveChatSession } from './chatSlice'

const AGENT = 'agent-510'

function outboundMessageEvent(id: string): TimelineEvent {
  return {
    kind: 'message',
    cursor: `cursor-${id}`,
    timestamp: '2026-07-29T17:00:00Z',
    message: {
      id,
      cursor: `cursor-${id}`,
      bodyText: 'Here is the reply.',
      bodyHtml: '',
      isOutbound: true,
      channel: 'web',
      attachments: [],
      timestamp: '2026-07-29T17:00:00Z',
    },
  } as unknown as TimelineEvent
}

function setupStreamingStore(content = 'Here is the reply.') {
  const store = createAppStore()
  store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
  store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'start' }))
  store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'delta', content_delta: content }))
  return store
}

describe('stream handoff (#510)', () => {
  it('records the handoff id instead of clearing the stream on outbound message arrival', () => {
    const store = setupStreamingStore()
    store.dispatch(chatActions.realtimeEventReceived({ agentId: AGENT, event: outboundMessageEvent('m1') }))

    const stream = selectActiveChatSession(store.getState()).stream.streaming
    expect(stream).not.toBeNull()
    expect(stream?.handoffMessageId).toBe('m1')
    expect(stream?.done).toBe(true)
    expect(stream?.content).toBe('Here is the reply.')
  })

  it('keeps the first handoff id when further outbound messages arrive', () => {
    const store = setupStreamingStore()
    store.dispatch(chatActions.realtimeEventReceived({ agentId: AGENT, event: outboundMessageEvent('m1') }))
    store.dispatch(chatActions.realtimeEventReceived({ agentId: AGENT, event: outboundMessageEvent('m2') }))

    expect(selectActiveChatSession(store.getState()).stream.streaming?.handoffMessageId).toBe('m1')
  })

  it('still clears immediately when the stream has no content to hand off', () => {
    const store = createAppStore()
    store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
    store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'start' }))
    store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'delta', reasoning_delta: 'thinking…' }))
    store.dispatch(chatActions.realtimeEventReceived({ agentId: AGENT, event: outboundMessageEvent('m1') }))

    expect(selectActiveChatSession(store.getState()).stream.streaming).toBeNull()
  })

  it('streamHandedOff clears exactly the named stream', () => {
    const store = setupStreamingStore()
    store.dispatch(chatActions.streamHandedOff({ agentId: AGENT, streamId: 'other' }))
    expect(selectActiveChatSession(store.getState()).stream.streaming).not.toBeNull()

    store.dispatch(chatActions.streamHandedOff({ agentId: AGENT, streamId: 's1' }))
    expect(selectActiveChatSession(store.getState()).stream.streaming).toBeNull()
  })

  it('keeps streamed content through done so the reveal can finish', () => {
    const store = setupStreamingStore()
    store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'done' }))

    const stream = selectActiveChatSession(store.getState()).stream.streaming
    expect(stream?.done).toBe(true)
    expect(stream?.content).toBe('Here is the reply.')
  })
})
