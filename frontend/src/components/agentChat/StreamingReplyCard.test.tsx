/**
 * #510: the card owns the reply until the reveal catches up AND the persisted message is
 * rendered, then swaps in one commit via streamHandedOff. (Reduced motion is forced so
 * the reveal completes instantly and the swap conditions are deterministic.)
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StreamingReplyCard } from './StreamingReplyCard'
import { chatActions, receiveStreamEvent, selectActiveChatSession } from '../../store/chatSlice'
import { createTestAppStore, StoreProvider } from '../../test/storeTestUtils'

const AGENT = 'agent-510'

beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
})

function renderCard(store: ReturnType<typeof createTestAppStore>, props: Partial<Parameters<typeof StreamingReplyCard>[0]> = {}) {
  return render(
    <StoreProvider store={store}>
      <StreamingReplyCard
        content="The reply text."
        agentFirstName="Scout"
        isStreaming={false}
        done
        streamId="s1"
        agentId={AGENT}
        handoffReady={false}
        {...props}
      />
    </StoreProvider>,
  )
}

function storeWithStream() {
  const store = createTestAppStore()
  store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
  store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'start' }))
  store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'delta', content_delta: 'The reply text.' }))
  store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'done' }))
  return store
}

describe('StreamingReplyCard handoff (#510)', () => {
  it('renders the content and holds the stream while the message is not yet rendered', () => {
    const store = storeWithStream()
    renderCard(store, { handoffReady: false })

    expect(screen.getByText('The reply text.')).toBeInTheDocument()
    expect(selectActiveChatSession(store.getState()).stream.streaming).not.toBeNull()
  })

  it('dispatches the swap once done + reveal complete + message rendered', async () => {
    const store = storeWithStream()
    renderCard(store, { handoffReady: true })

    await waitFor(() => {
      expect(selectActiveChatSession(store.getState()).stream.streaming).toBeNull()
    })
  })

  it('does not swap while the stream is still open', () => {
    const store = createTestAppStore()
    store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
    store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'start' }))
    store.dispatch(receiveStreamEvent(AGENT, { stream_id: 's1', status: 'delta', content_delta: 'The reply text.' }))
    renderCard(store, { isStreaming: true, done: false, handoffReady: true })

    expect(selectActiveChatSession(store.getState()).stream.streaming).not.toBeNull()
  })
})
