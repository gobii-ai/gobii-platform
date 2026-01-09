import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { createAgent } from '../api/agents'
import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import type { ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { useAgentChatStore } from '../stores/agentChatStore'
import type { AgentRosterEntry } from '../types/agentRoster'
import type { KanbanBoardSnapshot, TimelineEvent } from '../types/agentChat'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

function buildAgentChatPath(pathname: string, agentId: string): string {
  if (pathname.startsWith('/app')) {
    return `/app/agents/${agentId}`
  }
  if (pathname.includes('/console/agents/')) {
    return `/console/agents/${agentId}/chat/`
  }
  return `/app/agents/${agentId}`
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

type AgentSwitchMeta = {
  agentId: string
  agentName?: string | null
  agentColorHex?: string | null
  agentAvatarUrl?: string | null
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
  agentId: string | null
  agentName?: string | null
  agentColor?: string | null
  agentAvatarUrl?: string | null
  onClose?: () => void
  onCreateAgent?: () => void
  onAgentCreated?: (agentId: string) => void
}

const STREAMING_STALE_MS = 6000
const STREAMING_REFRESH_INTERVAL_MS = 6000

export function AgentChatPage({ agentId, agentName, agentColor, agentAvatarUrl, onClose, onCreateAgent, onAgentCreated }: AgentChatPageProps) {
  const isNewAgent = agentId === null
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
  }, [])
  const bottomSentinelRef = useRef<HTMLDivElement | null>(null)
  const captureBottomSentinelRef = useCallback((node: HTMLDivElement | null) => {
    bottomSentinelRef.current = node
  }, [])

  const [activeAgentId, setActiveAgentId] = useState(agentId)
  const [switchingAgentId, setSwitchingAgentId] = useState<string | null>(null)
  const pendingAgentMetaRef = useRef<AgentSwitchMeta | null>(null)

  useEffect(() => {
    setActiveAgentId(agentId)
  }, [agentId])

  const initialize = useAgentChatStore((state) => state.initialize)
  const storeAgentId = useAgentChatStore((state) => state.agentId)
  const agentColorHex = useAgentChatStore((state) => state.agentColorHex)
  const storedAgentName = useAgentChatStore((state) => state.agentName)
  const storedAgentAvatarUrl = useAgentChatStore((state) => state.agentAvatarUrl)
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
  const fetchInsights = useAgentChatStore((state) => state.fetchInsights)
  const startInsightRotation = useAgentChatStore((state) => state.startInsightRotation)
  const stopInsightRotation = useAgentChatStore((state) => state.stopInsightRotation)
  const dismissInsight = useAgentChatStore((state) => state.dismissInsight)
  const setInsightsPaused = useAgentChatStore((state) => state.setInsightsPaused)
  const setCurrentInsightIndex = useAgentChatStore((state) => state.setCurrentInsightIndex)
  const insights = useAgentChatStore((state) => state.insights)
  const currentInsightIndex = useAgentChatStore((state) => state.currentInsightIndex)
  const insightProcessingStartedAt = useAgentChatStore((state) => state.insightProcessingStartedAt)
  const dismissedInsightIds = useAgentChatStore((state) => state.dismissedInsightIds)
  const insightsPaused = useAgentChatStore((state) => state.insightsPaused)
  const loading = useAgentChatStore((state) => state.loading)
  const loadingOlder = useAgentChatStore((state) => state.loadingOlder)
  const loadingNewer = useAgentChatStore((state) => state.loadingNewer)
  const error = useAgentChatStore((state) => state.error)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const initialLoading = !isNewAgent && loading && events.length === 0

  const socketSnapshot = useAgentChatSocket(activeAgentId)
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(activeAgentId)
  const rosterQuery = useAgentRoster()

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
    // Skip initialization for new agent (null agentId)
    if (!activeAgentId) return
    const pendingMeta = pendingAgentMetaRef.current
    const resolvedPendingMeta = pendingMeta && pendingMeta.agentId === activeAgentId ? pendingMeta : null
    pendingAgentMetaRef.current = null
    initialize(activeAgentId, {
      agentColorHex: resolvedPendingMeta?.agentColorHex ?? agentColor,
      agentName: resolvedPendingMeta?.agentName ?? agentName,
      agentAvatarUrl: resolvedPendingMeta?.agentAvatarUrl ?? agentAvatarUrl,
    })
    // Fetch insights when agent initializes
    void fetchInsights()
  }, [activeAgentId, agentAvatarUrl, agentColor, agentName, initialize, fetchInsights])

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
    pendingScrollFrameRef.current = requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight
      const vvOffsetTop = window.visualViewport?.offsetTop ?? 0

      // Find the bottom sentinel to determine where actual content ends
      const sentinel = bottomSentinelRef.current
      const composer = document.getElementById('agent-composer-shell')

      if (sentinel && composer) {
        const sentinelRect = sentinel.getBoundingClientRect()
        const composerRect = composer.getBoundingClientRect()
        // Small gap between content bottom and composer top
        const gap = 16
        // Calculate where we want the sentinel's bottom to be
        const targetBottom = composerRect.top - gap
        // How much we need to scroll to get the sentinel to that position
        const delta = sentinelRect.bottom - targetBottom

        // Only scroll down (positive delta) - never scroll up during auto-scroll
        if (delta > 0) {
          window.scrollBy({ top: delta })
        }
        return
      }

      // Fallback when elements not found: scroll to document bottom
      const documentHeight = document.documentElement.scrollHeight
      const scrollTarget = documentHeight - viewportHeight + vvOffsetTop
      window.scrollTo({ top: Math.max(0, scrollTarget) })
    })
  }, [])

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

  const rosterAgents = useMemo(() => rosterQuery.data ?? [], [rosterQuery.data])
  const activeRosterMeta = useMemo(
    () => rosterAgents.find((agent) => agent.id === activeAgentId) ?? null,
    [activeAgentId, rosterAgents],
  )
  const isStoreSynced = storeAgentId === activeAgentId
  const resolvedAgentName = (isStoreSynced ? storedAgentName : activeRosterMeta?.name) ?? agentName ?? null
  const resolvedAvatarUrl = (isStoreSynced ? storedAgentAvatarUrl : activeRosterMeta?.avatarUrl) ?? agentAvatarUrl ?? null
  const resolvedAgentColorHex =
    (isStoreSynced ? agentColorHex : activeRosterMeta?.displayColorHex) ?? agentColor ?? null
  const agentFirstName = useMemo(() => deriveFirstName(resolvedAgentName), [resolvedAgentName])
  const latestKanbanSnapshot = useMemo(() => getLatestKanbanSnapshot(events), [events])
  const connectionIndicator = useMemo(
    () => {
      // For new agent, show ready state since there's no socket/session yet
      if (isNewAgent) {
        return { status: 'connected' as const, label: 'Ready', detail: 'Describe your new agent to get started.' }
      }
      return deriveConnectionIndicator({
        socketStatus: socketSnapshot.status,
        socketError: socketSnapshot.lastError,
        sessionStatus,
        sessionError,
      })
    },
    [isNewAgent, sessionError, sessionStatus, socketSnapshot.lastError, socketSnapshot.status],
  )

  // Update document title when agent changes
  useEffect(() => {
    const name = isNewAgent ? 'New Agent' : (resolvedAgentName || 'Agent')
    document.title = `${name} · Gobii`
  }, [isNewAgent, resolvedAgentName])

  const rosterErrorMessage = rosterQuery.isError
    ? rosterQuery.error instanceof Error
      ? rosterQuery.error.message
      : 'Unable to load agents right now.'
    : null
  const fallbackAgent = useMemo<AgentRosterEntry | null>(() => {
    if (!activeAgentId) {
      return null
    }
    return {
      id: activeAgentId,
      name: resolvedAgentName || 'Agent',
      avatarUrl: resolvedAvatarUrl,
      displayColorHex: resolvedAgentColorHex ?? null,
      isActive: true,
      shortDescription: '',
    }
  }, [activeAgentId, resolvedAgentColorHex, resolvedAgentName, resolvedAvatarUrl])
  const sidebarAgents = useMemo(() => {
    if (!activeAgentId) {
      return rosterAgents
    }
    const hasActive = rosterAgents.some((agent) => agent.id === activeAgentId)
    if (hasActive || !fallbackAgent) {
      return rosterAgents
    }
    return [fallbackAgent, ...rosterAgents]
  }, [activeAgentId, fallbackAgent, rosterAgents])

  useEffect(() => {
    if (!switchingAgentId) {
      return
    }
    if (!loading) {
      setSwitchingAgentId(null)
    }
  }, [loading, switchingAgentId])

  const handleSelectAgent = useCallback(
    (agent: AgentRosterEntry) => {
      if (agent.id === activeAgentId) {
        return
      }
      pendingAgentMetaRef.current = {
        agentId: agent.id,
        agentName: agent.name,
        agentColorHex: agent.displayColorHex,
        agentAvatarUrl: agent.avatarUrl,
      }
      setSwitchingAgentId(agent.id)
      setActiveAgentId(agent.id)
      const nextPath = buildAgentChatPath(window.location.pathname, agent.id)
      const nextUrl = `${nextPath}${window.location.search}${window.location.hash}`
      window.history.pushState({ agentId: agent.id }, '', nextUrl)
      window.dispatchEvent(new PopStateEvent('popstate'))
    },
    [activeAgentId],
  )

  const handleCreateAgent = useCallback(() => {
    // Use the prop callback if provided (for client-side navigation in ImmersiveApp)
    if (onCreateAgent) {
      onCreateAgent()
      return
    }
    // Fall back to full page navigation for console mode
    window.location.assign('/console/agents/create/quick/')
  }, [onCreateAgent])


  const handleJumpToLatest = async () => {
    await jumpToLatest()
    scrollToBottom()
    setAutoScrollPinned(true)
  }

  const handleSend = async (body: string, attachments: File[] = []) => {
    // If this is a new agent, create it first then navigate to it
    if (isNewAgent) {
      try {
        const result = await createAgent(body)
        onAgentCreated?.(result.agent_id)
      } catch (err) {
        console.error('Failed to create agent:', err)
      }
      return
    }
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

  // Start/stop insight rotation based on processing state
  const isProcessing = processingActive || awaitingResponse || (streaming && !streaming.done)
  useEffect(() => {
    if (isProcessing) {
      startInsightRotation()
    } else {
      stopInsightRotation()
    }
  }, [isProcessing, startInsightRotation, stopInsightRotation])

  // Get available insights (filtered for dismissed)
  const availableInsights = useMemo(() => {
    return insights.filter(
      (insight) => !dismissedInsightIds.has(insight.insightId)
    )
  }, [insights, dismissedInsightIds])

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
    <div className="agent-chat-page min-h-screen">
      {error || (sessionStatus === 'error' && sessionError) ? (
        <div className="mx-auto w-full max-w-3xl px-4 py-2 text-sm text-rose-600">{error || sessionError}</div>
      ) : null}
      <AgentChatLayout
        agentFirstName={isNewAgent ? 'New Agent' : agentFirstName}
        agentColorHex={resolvedAgentColorHex || undefined}
        agentAvatarUrl={resolvedAvatarUrl}
        agentName={isNewAgent ? 'New Agent' : (resolvedAgentName || 'Agent')}
        connectionStatus={connectionIndicator.status}
        connectionLabel={connectionIndicator.label}
        connectionDetail={connectionIndicator.detail}
        kanbanSnapshot={latestKanbanSnapshot}
        agentRoster={sidebarAgents}
        activeAgentId={activeAgentId}
        switchingAgentId={switchingAgentId}
        rosterLoading={rosterQuery.isLoading}
        rosterError={rosterErrorMessage}
        onSelectAgent={handleSelectAgent}
        onCreateAgent={handleCreateAgent}
        onClose={onClose}
        events={isNewAgent ? [] : events}
        hasMoreOlder={isNewAgent ? false : hasMoreOlder}
        hasMoreNewer={isNewAgent ? false : hasMoreNewer}
        oldestCursor={isNewAgent ? null : (events.length ? events[0].cursor : null)}
        newestCursor={isNewAgent ? null : (events.length ? events[events.length - 1].cursor : null)}
        processingActive={isNewAgent ? false : processingActive}
        awaitingResponse={isNewAgent ? false : awaitingResponse}
        processingWebTasks={isNewAgent ? [] : processingWebTasks}
        streaming={isNewAgent ? null : streaming}
        thinkingCollapsedByCursor={isNewAgent ? {} : thinkingCollapsedByCursor}
        onToggleThinking={handleToggleThinking}
        streamingThinkingCollapsed={streamingThinkingCollapsed}
        onToggleStreamingThinking={handleToggleStreamingThinking}
        onLoadOlder={isNewAgent ? undefined : (hasMoreOlder ? loadOlder : undefined)}
        onLoadNewer={isNewAgent ? undefined : (hasMoreNewer ? loadNewer : undefined)}
        onSendMessage={handleSend}
        onJumpToLatest={handleJumpToLatest}
        autoFocusComposer={isNewAgent}
        autoScrollPinned={autoScrollPinned}
        hasUnseenActivity={isNewAgent ? false : hasUnseenActivity}
        timelineRef={captureTimelineRef}
        bottomSentinelRef={captureBottomSentinelRef}
        loadingOlder={isNewAgent ? false : loadingOlder}
        loadingNewer={isNewAgent ? false : loadingNewer}
        initialLoading={initialLoading}
        insights={isNewAgent ? [] : availableInsights}
        currentInsightIndex={currentInsightIndex}
        onDismissInsight={dismissInsight}
        onInsightIndexChange={setCurrentInsightIndex}
        onPauseChange={setInsightsPaused}
        isInsightsPaused={insightsPaused}
      />
    </div>
  )
}
