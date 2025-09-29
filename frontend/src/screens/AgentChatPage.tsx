import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'

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

  useLayoutEffect(() => {
    if (!autoScrollPinned) return
    window.scrollTo(0, document.documentElement.scrollHeight)
  }, [events, autoScrollPinned])

  useEffect(() => {
    const handleScroll = () => {
      const scrollHeight = document.documentElement.scrollHeight
      const scrollTop = window.scrollY
      const clientHeight = window.innerHeight
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight
      const shouldPin = distanceFromBottom < 64
      setAutoScrollPinned(shouldPin)
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [setAutoScrollPinned])

  const agentFirstName = useMemo(() => deriveFirstName(agentName), [agentName])

  const handleJumpToLatest = async () => {
    await jumpToLatest()
    const node = timelineRef.current
    if (!node) return
    requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight
      setAutoScrollPinned(true)
    })
  }

  const handleSend = async (body: string) => {
    await sendMessage(body)
    const node = timelineRef.current
    if (node) {
      requestAnimationFrame(() => {
        node.scrollTop = node.scrollHeight
      })
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
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
        timelineRef={timelineRef}
        loadingOlder={loadingOlder}
        loadingNewer={loadingNewer}
      />
    </div>
  )
}
