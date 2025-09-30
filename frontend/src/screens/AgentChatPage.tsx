import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentChatStore } from '../stores/agentChatStore'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

export type AgentChatPageProps = {
  agentId: string
  agentName?: string | null
}

export function AgentChatPage({ agentId, agentName }: AgentChatPageProps) {
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
  }, [])

  const initialize = useAgentChatStore((state) => state.initialize)
  const loadOlder = useAgentChatStore((state) => state.loadOlder)
  const loadNewer = useAgentChatStore((state) => state.loadNewer)
  const jumpToLatest = useAgentChatStore((state) => state.jumpToLatest)
  const sendMessage = useAgentChatStore((state) => state.sendMessage)
  const events = useAgentChatStore((state) => state.events)
  const hasMoreOlder = useAgentChatStore((state) => state.hasMoreOlder)
  const hasMoreNewer = useAgentChatStore((state) => state.hasMoreNewer)
  const hasUnseenActivity = useAgentChatStore((state) => state.hasUnseenActivity)
  const processingActive = useAgentChatStore((state) => state.processingActive)
  const loading = useAgentChatStore((state) => state.loading)
  const loadingOlder = useAgentChatStore((state) => state.loadingOlder)
  const loadingNewer = useAgentChatStore((state) => state.loadingNewer)
  const error = useAgentChatStore((state) => state.error)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)

  useAgentChatSocket(agentId)

  useEffect(() => {
    initialize(agentId)
  }, [agentId, initialize])

  const getScrollContainer = useCallback(() => document.scrollingElement ?? document.documentElement, [])

  const scrollToBottom = useCallback(() => {
    if (!autoScrollPinned) return
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
    })
  }, [autoScrollPinned, getScrollContainer])

  useLayoutEffect(() => {
    scrollToBottom()
  }, [scrollToBottom, events, processingActive])

  useEffect(() => {
    const handleScroll = () => {
      const scroller = getScrollContainer()
      const distanceFromBottom = scroller.scrollHeight - window.scrollY - window.innerHeight
      setAutoScrollPinned(distanceFromBottom < 96)
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [getScrollContainer, setAutoScrollPinned])


  const agentFirstName = useMemo(() => deriveFirstName(agentName), [agentName])

  const handleJumpToLatest = async () => {
    await jumpToLatest()
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
      setAutoScrollPinned(true)
    })
  }

  const handleSend = async (body: string) => {
    await sendMessage(body)
    if (!autoScrollPinned) return
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
    })
  }

  return (
    <div className="min-h-screen">
      {error ? (
        <div className="mx-auto w-full max-w-3xl px-4 py-2 text-sm text-rose-600">{error}</div>
      ) : null}
      {loading && events.length === 0 ? (
        <div className="mx-auto flex h-[40vh] max-w-3xl items-center justify-center text-sm text-slate-500">
          Loading conversation…
        </div>
      ) : null}
      <AgentChatLayout
        agentName={agentName || 'Agent'}
        agentFirstName={agentFirstName}
        events={events}
        hasMoreOlder={hasMoreOlder}
        hasMoreNewer={hasMoreNewer}
        oldestCursor={events.length ? events[0].cursor : null}
        newestCursor={events.length ? events[events.length - 1].cursor : null}
        processingActive={processingActive}
        onLoadOlder={hasMoreOlder ? loadOlder : undefined}
        onLoadNewer={hasMoreNewer ? loadNewer : undefined}
        onSendMessage={handleSend}
        onJumpToLatest={handleJumpToLatest}
        autoScrollPinned={autoScrollPinned}
        hasUnseenActivity={hasUnseenActivity}
        timelineRef={captureTimelineRef}
        loadingOlder={loadingOlder}
        loadingNewer={loadingNewer}
      />
    </div>
  )
}
