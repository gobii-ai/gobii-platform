import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { AgentChatBanner } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentChatStore } from '../stores/agentChatStore'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

export type AgentChatPageProps = {
  agentId: string
  agentName?: string | null
  agentColor?: string | null
  agentAvatarUrl?: string | null
}

export function AgentChatPage({ agentId, agentName, agentColor, agentAvatarUrl }: AgentChatPageProps) {
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
  }, [])
  const bottomSentinelRef = useRef<HTMLDivElement | null>(null)
  const captureBottomSentinelRef = useCallback((node: HTMLDivElement | null) => {
    bottomSentinelRef.current = node
  }, [])

  const initialize = useAgentChatStore((state) => state.initialize)
  const agentColorHex = useAgentChatStore((state) => state.agentColorHex)
  const loadOlder = useAgentChatStore((state) => state.loadOlder)
  const loadNewer = useAgentChatStore((state) => state.loadNewer)
  const jumpToLatest = useAgentChatStore((state) => state.jumpToLatest)
  const sendMessage = useAgentChatStore((state) => state.sendMessage)
  const events = useAgentChatStore((state) => state.events)
  const hasMoreOlder = useAgentChatStore((state) => state.hasMoreOlder)
  const hasMoreNewer = useAgentChatStore((state) => state.hasMoreNewer)
  const hasUnseenActivity = useAgentChatStore((state) => state.hasUnseenActivity)
  const processingActive = useAgentChatStore((state) => state.processingActive)
  const processingWebTasks = useAgentChatStore((state) => state.processingWebTasks)
  const streaming = useAgentChatStore((state) => state.streaming)
  const loading = useAgentChatStore((state) => state.loading)
  const loadingOlder = useAgentChatStore((state) => state.loadingOlder)
  const loadingNewer = useAgentChatStore((state) => state.loadingNewer)
  const error = useAgentChatStore((state) => state.error)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const initialLoading = loading && events.length === 0

  const { error: sessionError } = useAgentWebSession(agentId)

  const autoScrollPinnedRef = useRef(autoScrollPinned)
  useEffect(() => {
    autoScrollPinnedRef.current = autoScrollPinned
  }, [autoScrollPinned])

  const autoScrollPinSuppressedUntilRef = useRef(autoScrollPinSuppressedUntil)
  useEffect(() => {
    autoScrollPinSuppressedUntilRef.current = autoScrollPinSuppressedUntil
  }, [autoScrollPinSuppressedUntil])

  useAgentChatSocket(agentId)

  useEffect(() => {
    initialize(agentId, { agentColorHex: agentColor })
  }, [agentId, initialize, agentColor])

  const getScrollContainer = useCallback(() => document.scrollingElement ?? document.documentElement ?? document.body, [])

  useEffect(() => {
    const scroller = getScrollContainer()

    const threshold = 160
    let ticking = false

    const readScrollPosition = () => {
      const target = scroller || document.documentElement || document.body
      const distanceToBottom = target.scrollHeight - target.clientHeight - target.scrollTop
      return distanceToBottom
    }

    const handleScroll = () => {
      if (ticking) {
        return
      }
      ticking = true
      requestAnimationFrame(() => {
        ticking = false
        const distanceToBottom = readScrollPosition()
        const currentlyPinned = autoScrollPinnedRef.current
        const suppressedUntil = autoScrollPinSuppressedUntilRef.current
        const suppressionActive = typeof suppressedUntil === 'number' && suppressedUntil > Date.now()

        if (!currentlyPinned && !suppressionActive && distanceToBottom <= 12) {
          setAutoScrollPinned(true)
          return
        }

        if (!currentlyPinned) {
          return
        }

        if (distanceToBottom > threshold) {
          setAutoScrollPinned(false)
        }
      })
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    document.addEventListener('scroll', handleScroll, { passive: true })
    scroller?.addEventListener('scroll', handleScroll, { passive: true })

    return () => {
      window.removeEventListener('scroll', handleScroll)
      document.removeEventListener('scroll', handleScroll)
      scroller?.removeEventListener('scroll', handleScroll)
    }
  }, [getScrollContainer, setAutoScrollPinned])

  const scrollToBottom = useCallback(() => {
    if (!autoScrollPinned) return
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
    })
  }, [autoScrollPinned, getScrollContainer])

  useLayoutEffect(() => {
    scrollToBottom()
  }, [scrollToBottom, events, processingActive, streaming])

  const agentFirstName = useMemo(() => deriveFirstName(agentName), [agentName])

  useEffect(() => {
    const sentinel = bottomSentinelRef.current
    if (!sentinel) {
      setAutoScrollPinned(false)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries.find((item) => item.target === sentinel)
        const nextPinned = Boolean(entry?.isIntersecting)
        setAutoScrollPinned(nextPinned)
      },
      { root: null, threshold: 0.75 },
    )

    observer.observe(sentinel)

    return () => {
      observer.disconnect()
    }
  }, [setAutoScrollPinned, hasMoreNewer])

  const handleJumpToLatest = async () => {
    await jumpToLatest()
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
      setAutoScrollPinned(true)
    })
  }

  const handleSend = async (body: string, attachments: File[] = []) => {
    await sendMessage(body, attachments)
    if (!autoScrollPinned) return
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
    })
  }

  return (
    <div className="min-h-screen">
      {error || sessionError ? (
        <div className="mx-auto w-full max-w-3xl px-4 py-2 text-sm text-rose-600">{error || sessionError}</div>
      ) : null}
      <AgentChatLayout
        agentFirstName={agentFirstName}
        agentColorHex={agentColorHex || agentColor || undefined}
        header={
          <AgentChatBanner
            agentName={agentName || 'Agent'}
            agentAvatarUrl={agentAvatarUrl}
            agentColorHex={agentColorHex || agentColor || undefined}
          />
        }
        events={events}
        hasMoreOlder={hasMoreOlder}
        hasMoreNewer={hasMoreNewer}
        oldestCursor={events.length ? events[0].cursor : null}
        newestCursor={events.length ? events[events.length - 1].cursor : null}
        processingActive={processingActive}
        processingWebTasks={processingWebTasks}
        streaming={streaming}
        onLoadOlder={hasMoreOlder ? loadOlder : undefined}
        onLoadNewer={hasMoreNewer ? loadNewer : undefined}
        onSendMessage={handleSend}
        onJumpToLatest={handleJumpToLatest}
        autoScrollPinned={autoScrollPinned}
        hasUnseenActivity={hasUnseenActivity}
        timelineRef={captureTimelineRef}
        bottomSentinelRef={captureBottomSentinelRef}
        loadingOlder={loadingOlder}
        loadingNewer={loadingNewer}
        initialLoading={initialLoading}
      />
    </div>
  )
}
