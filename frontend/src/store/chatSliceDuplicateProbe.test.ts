/**
 * Probe for #48: a sent message rendering twice, one copy stuck on "Sending".
 *
 * The optimistic bubble and the server echo carry different cursors, so mergeTimelineEvents can
 * never collapse them. Reconciliation used to rest entirely on a signature guess -- normalized
 * text, attachment count, and timestamps within OPTIMISTIC_MATCH_WINDOW_MS -- and every case
 * below marked "the guess cannot see this" rendered two copies before the sender started
 * settling its own send by clientId.
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

const AGENT = 'agent-dup'
const BODY = 'did the invoice go out?'

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

/** Every message copy the user would see, with the status badge that renders on it. */
function renderedMessages(queryClient: QueryClient): Array<{ body: string; status?: string }> {
  const data = queryClient.getQueryData<InfiniteData<TimelinePage>>(timelineQueryKey(AGENT))
  return (data?.pages ?? []).flatMap((page) =>
    page.events
      .filter((event) => event.kind === 'message')
      .map((event) => ({ body: event.message.bodyText ?? '', status: event.message.status })),
  )
}

/** A faithful server echo, with individual fields overridable to model a degraded one. */
function serverEcho(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'message',
    cursor: '1784997786629681:message:01SERVERCURSOR',
    message: {
      id: 'server-msg-1',
      cursor: '1784997786629681:message:01SERVERCURSOR',
      bodyText: BODY,
      bodyHtml: '',
      isOutbound: false,
      channel: 'web',
      attachments: [],
      timestamp: new Date().toISOString(),
      relativeTimestamp: 'now',
      ...overrides,
    },
  }
}

async function sendAndCount(echo: unknown) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createAppStore({ queryClient })
  seedTimelineCache(queryClient)
  store.dispatch(chatActions.agentSelected({ agentId: AGENT }))
  vi.mocked(sendAgentMessage).mockResolvedValue(echo as never)

  await store.dispatch(sendMessage({ body: BODY }) as never)
  return renderedMessages(queryClient)
}

describe('#48 optimistic reconciliation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('collapses to one copy when the echo is faithful', async () => {
    const rendered = await sendAndCount(serverEcho())
    expect(rendered).toHaveLength(1)
    expect(rendered[0].status).not.toBe('sending')
  })

  it('collapses when the echo timestamp is inside the match window', async () => {
    const rendered = await sendAndCount(
      serverEcho({ timestamp: new Date(Date.now() + 119_000).toISOString() }),
    )
    expect(rendered).toHaveLength(1)
  })

  // Was red: a device clock more than two minutes off duplicated on every single send.
  it('collapses when the echo timestamp is outside the match window', async () => {
    const rendered = await sendAndCount(
      serverEcho({ timestamp: new Date(Date.now() + 121_000).toISOString() }),
    )
    expect(rendered).toHaveLength(1)
    expect(rendered.some((m) => m.status === 'sending')).toBe(false)
  })

  // Was red: the window is symmetric, so a slow clock broke it too.
  it('collapses when the clock disagreement runs the other way', async () => {
    const rendered = await sendAndCount(
      serverEcho({ timestamp: new Date(Date.now() - 121_000).toISOString() }),
    )
    expect(rendered).toHaveLength(1)
  })

  // Was red, and needs no broken clock: a rejected or late-linked upload is enough.
  it('collapses when the echo reports an attachment the optimistic copy did not count', async () => {
    const rendered = await sendAndCount(
      serverEcho({ attachments: [{ id: 'a1', filename: 'invoice.pdf', url: '' }] }),
    )
    expect(rendered).toHaveLength(1)
  })

  // Was red: any character the two sides normalize differently was enough.
  it('collapses when the server rewrites the body text', async () => {
    const rendered = await sendAndCount(serverEcho({ bodyText: `${BODY} ` + '​' }))
    expect(rendered).toHaveLength(1)
  })

  // Stripping tags off the html lands back on the same normalized text, so this one is safe.
  it('collapses when the echo carries only rendered html', async () => {
    const rendered = await sendAndCount(
      serverEcho({ bodyText: '', bodyHtml: `<p>${BODY}</p>` }),
    )
    expect(rendered).toHaveLength(1)
  })

  // A missing timestamp skips the window comparison rather than failing it.
  it('collapses when the echo has no timestamp at all', async () => {
    const rendered = await sendAndCount(serverEcho({ timestamp: null }))
    expect(rendered).toHaveLength(1)
  })
})
