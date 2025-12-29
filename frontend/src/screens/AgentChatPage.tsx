import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { AgentChatBanner } from '../components/agentChat/AgentChatBanner'
import type { ConnectionStatusTone } from '../components/agentChat/ConnectionStatusIndicator'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentChatStore } from '../stores/agentChatStore'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

type ConnectionIndicator = {
  status: ConnectionStatusTone
  label: string
  detail?: string | null
}

function deriveConnectionIndicator({
  socketStatus,
  socketError,
  sessionStatus,
  sessionError,
}: {
  socketStatus: ReturnType<typeof useAgentChatSocket>['status']
  socketError: string | null
  sessionStatus: ReturnType<typeof useAgentWebSession>['status']
  sessionError: string | null
}): ConnectionIndicator {
  if (socketStatus === 'offline') {
    return { status: 'offline', label: 'Offline', detail: 'Waiting for network connection.' }
  }

  if (sessionStatus === 'error') {
    return {
      status: 'error',
      label: 'Session error',
      detail: sessionError || 'Web session needs attention.',
    }
  }

  if (socketStatus === 'error') {
    return {
      status: 'error',
      label: 'Connection error',
      detail: socketError || 'WebSocket needs attention.',
    }
  }

  if (socketStatus === 'connected' && sessionStatus === 'active') {
    return { status: 'connected', label: 'Connected', detail: 'Live updates active.' }
  }

  if (socketStatus === 'reconnecting') {
    return {
      status: 'reconnecting',
      label: 'Reconnecting',
      detail: socketError || 'Restoring live updates.',
    }
  }

  if (sessionStatus === 'starting') {
    // socketStatus can only be 'connected' here since 'reconnecting' was already handled above
    const shouldReconnect = socketStatus === 'connected'
    return {
      status: shouldReconnect ? 'reconnecting' : 'connecting',
      label: shouldReconnect ? 'Reconnecting' : 'Connecting',
      detail: sessionError || 'Re-establishing session.',
    }
  }

  if (socketStatus === 'connected') {
    return { status: 'connecting', label: 'Syncing', detail: 'Syncing session state.' }
  }

  return { status: 'connecting', label: 'Connecting', detail: 'Opening live connection.' }
}

export type AgentChatPageProps = {
  agentId: string
  agentName?: string | null
  agentColor?: string | null
  agentAvatarUrl?: string | null
}

const STREAMING_STALE_MS = 6000
const STREAMING_REFRESH_INTERVAL_MS = 6000

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
  const streamingLastUpdatedAt = useAgentChatStore((state) => state.streamingLastUpdatedAt)
  const thinkingCollapsedByCursor = useAgentChatStore((state) => state.thinkingCollapsedByCursor)
  const toggleThinkingCollapsed = useAgentChatStore((state) => state.toggleThinkingCollapsed)
  const streamingThinkingCollapsed = useAgentChatStore((state) => state.streamingThinkingCollapsed)
  const setStreamingThinkingCollapsed = useAgentChatStore((state) => state.setStreamingThinkingCollapsed)
  const finalizeStreaming = useAgentChatStore((state) => state.finalizeStreaming)
  const refreshLatest = useAgentChatStore((state) => state.refreshLatest)
  const refreshProcessing = useAgentChatStore((state) => state.refreshProcessing)
  const loading = useAgentChatStore((state) => state.loading)
  const loadingOlder = useAgentChatStore((state) => state.loadingOlder)
  const loadingNewer = useAgentChatStore((state) => state.loadingNewer)
  const error = useAgentChatStore((state) => state.error)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const initialLoading = loading && events.length === 0

  const socketSnapshot = useAgentChatSocket(agentId)
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(agentId)

  const autoScrollPinnedRef = useRef(autoScrollPinned)
  useEffect(() => {
    autoScrollPinnedRef.current = autoScrollPinned
  }, [autoScrollPinned])

  // Track if we should scroll on next content update (captured before DOM changes)
  const shouldScrollOnNextUpdateRef = useRef(autoScrollPinned)

  const autoScrollPinSuppressedUntilRef = useRef(autoScrollPinSuppressedUntil)
  useEffect(() => {
    autoScrollPinSuppressedUntilRef.current = autoScrollPinSuppressedUntil
  }, [autoScrollPinSuppressedUntil])

  useEffect(() => {
    initialize(agentId, { agentColorHex: agentColor })
  }, [agentId, initialize, agentColor])

  const getScrollContainer = useCallback(() => document.scrollingElement ?? document.documentElement ?? document.body, [])

  useEffect(() => {
    const scroller = getScrollContainer()

    // Threshold for re-sticking when user scrolls back to bottom
    const restickThreshold = 20

    const getDistanceToBottom = () => {
      const target = scroller || document.documentElement || document.body
      return target.scrollHeight - target.clientHeight - target.scrollTop
    }

    // Detect user scrolling UP via wheel - immediately unstick
    const handleWheel = (e: WheelEvent) => {
      if (e.deltaY < 0 && autoScrollPinnedRef.current) {
        // User is scrolling up - unstick immediately
        setAutoScrollPinned(false)
      }
    }

    // Detect user scrolling UP via touch
    let touchStartY = 0
    const handleTouchStart = (e: TouchEvent) => {
      touchStartY = e.touches[0]?.clientY ?? 0
    }
    const handleTouchMove = (e: TouchEvent) => {
      const touchY = e.touches[0]?.clientY ?? 0
      // Touch moved down = scrolling up (pulling content down)
      if (touchY > touchStartY + 10 && autoScrollPinnedRef.current) {
        setAutoScrollPinned(false)
      }
    }

    // Detect user scrolling UP via keyboard
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!autoScrollPinnedRef.current) return
      const scrollUpKeys = ['ArrowUp', 'PageUp', 'Home']
      if (scrollUpKeys.includes(e.key)) {
        setAutoScrollPinned(false)
      }
    }

    // Check if user has scrolled back to bottom (for re-sticking)
    let ticking = false
    const handleScroll = () => {
      if (ticking) return
      ticking = true
      requestAnimationFrame(() => {
        ticking = false
        const distanceToBottom = getDistanceToBottom()
        const currentlyPinned = autoScrollPinnedRef.current
        const suppressedUntil = autoScrollPinSuppressedUntilRef.current
        const suppressionActive = typeof suppressedUntil === 'number' && suppressedUntil > Date.now()

        // Re-stick when user scrolls to bottom
        if (!currentlyPinned && !suppressionActive && distanceToBottom <= restickThreshold) {
          setAutoScrollPinned(true)
        }
      })
    }

    window.addEventListener('wheel', handleWheel, { passive: true })
    window.addEventListener('touchstart', handleTouchStart, { passive: true })
    window.addEventListener('touchmove', handleTouchMove, { passive: true })
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('scroll', handleScroll, { passive: true })

    return () => {
      window.removeEventListener('wheel', handleWheel)
      window.removeEventListener('touchstart', handleTouchStart)
      window.removeEventListener('touchmove', handleTouchMove)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('scroll', handleScroll)
    }
  }, [getScrollContainer, setAutoScrollPinned])

  // Capture scroll decision BEFORE content changes to avoid race with scroll handler
  const prevEventsRef = useRef(events)
  const prevStreamingRef = useRef(streaming)
  const prevProcessingActiveRef = useRef(processingActive)

  // Before render, capture whether we should scroll (based on current scroll position)
  if (
    events !== prevEventsRef.current ||
    streaming !== prevStreamingRef.current ||
    processingActive !== prevProcessingActiveRef.current
  ) {
    // Content is about to change - capture scroll decision NOW before DOM updates
    shouldScrollOnNextUpdateRef.current = autoScrollPinnedRef.current
    prevEventsRef.current = events
    prevStreamingRef.current = streaming
    prevProcessingActiveRef.current = processingActive
  }

  const scrollToBottom = useCallback(() => {
    const scroller = getScrollContainer()
    requestAnimationFrame(() => {
      window.scrollTo({ top: scroller.scrollHeight })
    })
  }, [getScrollContainer])

  useLayoutEffect(() => {
    // Use the captured decision from before the DOM update
    if (shouldScrollOnNextUpdateRef.current) {
      scrollToBottom()
    }
  }, [scrollToBottom, events, processingActive, streaming])

  const agentFirstName = useMemo(() => deriveFirstName(agentName), [agentName])
  const connectionIndicator = useMemo(
    () =>
      deriveConnectionIndicator({
        socketStatus: socketSnapshot.status,
        socketError: socketSnapshot.lastError,
        sessionStatus,
        sessionError,
      }),
    [sessionError, sessionStatus, socketSnapshot.lastError, socketSnapshot.status],
  )


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

  const handleToggleThinking = useCallback(
    (cursor: string) => {
      toggleThinkingCollapsed(cursor)
    },
    [toggleThinkingCollapsed],
  )

  const handleToggleStreamingThinking = useCallback(() => {
    setStreamingThinkingCollapsed(!streamingThinkingCollapsed)
  }, [setStreamingThinkingCollapsed, streamingThinkingCollapsed])

  useEffect(() => {
    if (!streaming || streaming.done) {
      return () => undefined
    }
    const interval = window.setInterval(() => {
      void refreshProcessing()
    }, STREAMING_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [refreshProcessing, streaming])

  useEffect(() => {
    if (!streaming || streaming.done) {
      return () => undefined
    }
    if (processingActive) {
      return () => undefined
    }
    const lastUpdated = streamingLastUpdatedAt ?? Date.now()
    const elapsed = Date.now() - lastUpdated
    const timeoutMs = Math.max(0, STREAMING_STALE_MS - elapsed)
    const handleTimeout = () => {
      finalizeStreaming()
      if (streaming.reasoning && !streaming.content) {
        void refreshLatest()
      }
    }
    if (timeoutMs === 0) {
      handleTimeout()
      return () => undefined
    }
    const timeout = window.setTimeout(handleTimeout, timeoutMs)
    return () => window.clearTimeout(timeout)
  }, [
    finalizeStreaming,
    processingActive,
    refreshLatest,
    streaming,
    streamingLastUpdatedAt,
  ])

  return (
    <div className="min-h-screen">
      {error || (sessionStatus === 'error' && sessionError) ? (
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
            connectionStatus={connectionIndicator.status}
            connectionLabel={connectionIndicator.label}
            connectionDetail={connectionIndicator.detail}
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
        thinkingCollapsedByCursor={thinkingCollapsedByCursor}
        onToggleThinking={handleToggleThinking}
        streamingThinkingCollapsed={streamingThinkingCollapsed}
        onToggleStreamingThinking={handleToggleStreamingThinking}
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
