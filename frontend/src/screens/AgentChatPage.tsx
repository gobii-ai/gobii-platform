import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { AgentChatBanner, type ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentChatStore } from '../stores/agentChatStore'
import type { KanbanBoardSnapshot, TimelineEvent } from '../types/agentChat'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

function getLatestKanbanSnapshot(events: TimelineEvent[]): KanbanBoardSnapshot | null {
  // Find the most recent kanban event (they're ordered oldest to newest)
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.kind === 'kanban') {
      return event.snapshot
    }
  }
  return null
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
  const awaitingResponse = useAgentChatStore((state) => state.awaitingResponse)
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

    // Mobile detection for adaptive thresholds
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0

    // Use larger thresholds on mobile to account for browser chrome changes (address bar, keyboard)
    const restickThreshold = isTouchDevice ? 80 : 20
    const touchScrollThreshold = 40 // More intentional gesture required to unpin

    const getDistanceToBottom = () => {
      const target = scroller || document.documentElement || document.body
      // Use visualViewport for accurate measurement on mobile (accounts for keyboard, browser chrome)
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight
      const documentHeight = target.scrollHeight
      const scrollTop = target.scrollTop
      // On mobile, clientHeight can include hidden browser chrome - use visualViewport when available
      const effectiveClientHeight = window.visualViewport ? viewportHeight : target.clientHeight
      return documentHeight - effectiveClientHeight - scrollTop
    }

    // Detect user scrolling UP via wheel - immediately unstick
    const handleWheel = (e: WheelEvent) => {
      if (e.deltaY < 0 && autoScrollPinnedRef.current) {
        setAutoScrollPinned(false)
      }
    }

    // Detect user scrolling UP via touch
    let touchStartY = 0
    let touchStartTime = 0
    const handleTouchStart = (e: TouchEvent) => {
      touchStartY = e.touches[0]?.clientY ?? 0
      touchStartTime = Date.now()
    }
    const handleTouchMove = (e: TouchEvent) => {
      if (!autoScrollPinnedRef.current) return
      const touchY = e.touches[0]?.clientY ?? 0
      const deltaY = touchY - touchStartY
      const elapsed = Date.now() - touchStartTime
      // Require more intentional gesture: larger movement AND not too fast (avoid accidental taps)
      if (deltaY > touchScrollThreshold && elapsed > 50) {
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
    let scrollTicking = false
    const checkAndRestick = () => {
      const distanceToBottom = getDistanceToBottom()
      const currentlyPinned = autoScrollPinnedRef.current
      const suppressedUntil = autoScrollPinSuppressedUntilRef.current
      const suppressionActive = typeof suppressedUntil === 'number' && suppressedUntil > Date.now()

      if (!currentlyPinned && !suppressionActive && distanceToBottom <= restickThreshold) {
        setAutoScrollPinned(true)
      }
    }

    const handleScroll = () => {
      if (scrollTicking) return
      scrollTicking = true
      requestAnimationFrame(() => {
        scrollTicking = false
        checkAndRestick()
      })
    }

    // Handle viewport resize (keyboard show/hide, browser chrome changes on mobile)
    let resizeTicking = false
    const handleViewportResize = () => {
      if (resizeTicking) return
      resizeTicking = true
      requestAnimationFrame(() => {
        resizeTicking = false
        checkAndRestick()
      })
    }

    window.addEventListener('wheel', handleWheel, { passive: true })
    window.addEventListener('touchstart', handleTouchStart, { passive: true })
    window.addEventListener('touchmove', handleTouchMove, { passive: true })
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('scroll', handleScroll, { passive: true })

    // Listen to visualViewport resize for mobile browser chrome changes
    const vv = window.visualViewport
    if (vv) {
      vv.addEventListener('resize', handleViewportResize)
      vv.addEventListener('scroll', handleViewportResize)
    }
    window.addEventListener('resize', handleViewportResize)

    return () => {
      window.removeEventListener('wheel', handleWheel)
      window.removeEventListener('touchstart', handleTouchStart)
      window.removeEventListener('touchmove', handleTouchMove)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('scroll', handleScroll)
      if (vv) {
        vv.removeEventListener('resize', handleViewportResize)
        vv.removeEventListener('scroll', handleViewportResize)
      }
      window.removeEventListener('resize', handleViewportResize)
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

  const pendingScrollFrameRef = useRef<number | null>(null)

  const scrollToBottom = useCallback(() => {
    if (pendingScrollFrameRef.current !== null) {
      return
    }
    const scroller = getScrollContainer()
    pendingScrollFrameRef.current = requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      // Calculate max scroll accounting for visualViewport on mobile
      const documentHeight = scroller.scrollHeight
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight
      // On mobile with visualViewport, we need to account for the offset from page top
      const vvOffsetTop = window.visualViewport?.offsetTop ?? 0
      const scrollTarget = documentHeight - viewportHeight + vvOffsetTop
      window.scrollTo({ top: Math.max(0, scrollTarget) })
    })
  }, [getScrollContainer])

  useEffect(() => () => {
    if (pendingScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingScrollFrameRef.current)
    }
  }, [])

  useLayoutEffect(() => {
    // Use the captured decision from before the DOM update
    if (shouldScrollOnNextUpdateRef.current) {
      scrollToBottom()
    }
  }, [scrollToBottom, events, processingActive, streaming])

  const agentFirstName = useMemo(() => deriveFirstName(agentName), [agentName])
  const latestKanbanSnapshot = useMemo(() => getLatestKanbanSnapshot(events), [events])
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
    scrollToBottom()
    setAutoScrollPinned(true)
  }

  const handleSend = async (body: string, attachments: File[] = []) => {
    await sendMessage(body, attachments)
    if (!autoScrollPinned) return
    scrollToBottom()
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
            kanbanSnapshot={latestKanbanSnapshot}
            processingActive={processingActive}
          />
        }
        events={events}
        hasMoreOlder={hasMoreOlder}
        hasMoreNewer={hasMoreNewer}
        oldestCursor={events.length ? events[0].cursor : null}
        newestCursor={events.length ? events[events.length - 1].cursor : null}
        processingActive={processingActive}
        awaitingResponse={awaitingResponse}
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
