/**
 * Regression coverage for #287: a message sent while the session was not pinned never reached the
 * render cache, and the jump control stayed lit while the viewport was at the live edge.
 */
import { QueryClient, type InfiniteData } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sendAgentMessage } from '../api/agentChat'
import { timelineQueryKey, type TimelinePage } from '../hooks/useAgentTimeline'
import { createAppStore } from './appStore'
import { chatActions, sendMessage } from './chatSlice'

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
}))

const AGENT = 'agent-1'
const BODY = 'does that all check out?'

/** The exact visibility expression used by AgentChatLayout. */
function showJumpButton(options: {
  hasMoreNewer: boolean
  autoScrollPinned: boolean
  hasUnseenActivity: boolean
  isNearBottom: boolean
}): boolean {
  return options.hasMoreNewer
    || (!options.autoScrollPinned && (options.hasUnseenActivity || !options.isNearBottom))
}

function seedTimelineCache(queryClient: QueryClient) {
  const page: TimelinePage = {
    events: [],
    hasMoreOlder: false,
    hasMoreNewer: false,
    oldestCursor: null,
    newestCursor: null,
    currentPlan: null,
    pendingActionsStateOrder: 0,
    raw: {} as TimelinePage['raw'],
  }
  queryClient.setQueryData<InfiniteData<TimelinePage>>(timelineQueryKey(AGENT), {
    pages: [page],
    pageParams: [undefined],
  })
}

function cachedMessageBodies(queryClient: QueryClient): string[] {
  const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(timelineQueryKey(AGENT))
  return (data?.pages ?? []).flatMap((page) =>
    page.events.filter((event) => event.kind === 'message').map((event) => event.message.bodyText ?? ''),
  )
}

describe('sending while unpinned', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(sendAgentMessage).mockResolvedValue({ ok: true } as never)
  })

  it('renders the sender their own message and returns to the live edge', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const store = createAppStore({ queryClient })
    seedTimelineCache(queryClient)
    store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
    store.dispatch(chatActions.autoScrollPinnedSet({ agentId: AGENT, pinned: false }))

    await store.dispatch(sendMessage({ body: BODY }) as never)

    const session = store.getState().chat.sessionsByAgentId[AGENT]
    expect(cachedMessageBodies(queryClient)).toContain(BODY)
    expect(session.timelineUi.autoScrollPinned).toBe(true)
    expect(session.timelineUi.pendingEvents).toEqual([])
  })

  it('leaves the jump control hidden at the live edge after sending', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const store = createAppStore({ queryClient })
    seedTimelineCache(queryClient)
    store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
    store.dispatch(chatActions.autoScrollPinnedSet({ agentId: AGENT, pinned: false }))

    await store.dispatch(sendMessage({ body: BODY }) as never)

    const session = store.getState().chat.sessionsByAgentId[AGENT]
    expect(showJumpButton({
      hasMoreNewer: false,
      autoScrollPinned: session.timelineUi.autoScrollPinned,
      hasUnseenActivity: session.timelineUi.hasUnseenActivity,
      isNearBottom: true,
    })).toBe(false)
  })
})
