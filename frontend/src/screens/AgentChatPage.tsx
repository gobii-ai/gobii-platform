import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Plus } from 'lucide-react'

import { createAgent } from '../api/agents'
import type { ConsoleContext } from '../api/context'
import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { ChatSidebar } from '../components/agentChat/ChatSidebar'
import type { ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { useConsoleContextSwitcher } from '../hooks/useConsoleContextSwitcher'
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

function compareRosterNames(left: string, right: string): number {
  return left.localeCompare(right, undefined, { sensitivity: 'base' })
}

function insertRosterEntry(agents: AgentRosterEntry[], entry: AgentRosterEntry): AgentRosterEntry[] {
  const insertionIndex = agents.findIndex((agent) => compareRosterNames(entry.name, agent.name) < 0)
  if (insertionIndex === -1) {
    return [...agents, entry]
  }
  return [...agents.slice(0, insertionIndex), entry, ...agents.slice(insertionIndex)]
}

function mergeRosterEntry(agents: AgentRosterEntry[] | undefined, entry: AgentRosterEntry): AgentRosterEntry[] {
  const roster = agents ?? []
  if (roster.some((agent) => agent.id === entry.id)) {
    return roster
  }
  return insertRosterEntry(roster, entry)
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

type AgentNotFoundStateProps = {
  hasOtherAgents: boolean
  onCreateAgent: () => void
}

function AgentNotFoundState({ hasOtherAgents, onCreateAgent }: AgentNotFoundStateProps) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-4">
      <div className="mb-6 flex size-16 items-center justify-center rounded-full bg-amber-100 text-amber-600">
        <AlertTriangle className="size-8" aria-hidden="true" />
      </div>
      <h2 className="mb-2 text-xl font-semibold text-gray-800">Agent not found</h2>
      <p className="mb-6 max-w-md text-center text-sm text-gray-600">
        {hasOtherAgents
          ? 'This agent may have been deleted or you may not have access to it. Select another agent from the sidebar or create a new one.'
          : 'This agent may have been deleted or you may not have access to it. Create a new agent to get started.'}
      </p>
      <button
        type="button"
        onClick={onCreateAgent}
        className="group inline-flex items-center justify-center gap-x-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
      >
        <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
        Create New Agent
      </button>
    </div>
  )
}

type AgentSelectStateProps = {
  hasAgents: boolean
  onCreateAgent?: () => void
}

function AgentSelectState({ hasAgents, onCreateAgent }: AgentSelectStateProps) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Agent workspace</p>
      <h2 className="text-2xl font-semibold text-slate-900">
        {hasAgents ? 'Select a conversation' : 'No agents yet'}
      </h2>
      <p className="max-w-lg text-sm text-slate-500">
        {hasAgents
          ? 'Pick an agent from the list to continue the conversation.'
          : 'Create your first agent to start a new conversation.'}
      </p>
      {!hasAgents && onCreateAgent ? (
        <button
          type="button"
          onClick={onCreateAgent}
          className="group mt-2 inline-flex items-center justify-center gap-x-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
          Create Your First Agent
        </button>
      ) : null}
    </div>
  )
}

export type AgentChatPageProps = {
  agentId?: string | null
  agentName?: string | null
  agentColor?: string | null
  agentAvatarUrl?: string | null
  onClose?: () => void
  onCreateAgent?: () => void
  onAgentCreated?: (agentId: string) => void
  showContextSwitcher?: boolean
  persistContextSession?: boolean
  onContextSwitch?: (context: ConsoleContext) => void
}

const STREAMING_STALE_MS = 6000
const STREAMING_REFRESH_INTERVAL_MS = 6000
const SCROLL_END_TOLERANCE_PX = 4
const BOTTOM_PANEL_GAP_PX = 20

export function AgentChatPage({
  agentId,
  agentName,
  agentColor,
  agentAvatarUrl,
  onClose,
  onCreateAgent,
  onAgentCreated,
  showContextSwitcher = false,
  persistContextSession = true,
  onContextSwitch,
}: AgentChatPageProps) {
  const queryClient = useQueryClient()
  const isNewAgent = agentId === null
  const isSelectionView = agentId === undefined
  const timelineRef = useRef<HTMLDivElement | null>(null)

  const handleContextSwitched = useCallback(
    (context: ConsoleContext) => {
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: true })
      if (onContextSwitch) {
        onContextSwitch(context)
        return
      }
      if (typeof window !== 'undefined') {
        window.location.reload()
      }
    },
    [onContextSwitch, queryClient],
  )

  const {
    data: contextData,
    isSwitching: contextSwitching,
    error: contextError,
    switchContext,
    refresh: refreshContext,
  } = useConsoleContextSwitcher({
    enabled: showContextSwitcher,
    onSwitched: handleContextSwitched,
    persistSession: persistContextSession,
  })

  const [activeAgentId, setActiveAgentId] = useState<string | null>(agentId ?? null)
  const [switchingAgentId, setSwitchingAgentId] = useState<string | null>(null)
  const [selectionSidebarCollapsed, setSelectionSidebarCollapsed] = useState(false)
  const pendingAgentMetaRef = useRef<AgentSwitchMeta | null>(null)
  const liveAgentId = contextSwitching ? null : activeAgentId

  useEffect(() => {
    setActiveAgentId(agentId ?? null)
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
  const processingStartedAt = useAgentChatStore((state) => state.processingStartedAt)
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
  const dismissedInsightIds = useAgentChatStore((state) => state.dismissedInsightIds)
  const insightsPaused = useAgentChatStore((state) => state.insightsPaused)
  const loading = useAgentChatStore((state) => state.loading)
  const loadingOlder = useAgentChatStore((state) => state.loadingOlder)
  const loadingNewer = useAgentChatStore((state) => state.loadingNewer)
  const error = useAgentChatStore((state) => state.error)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const initialLoading = !isNewAgent && loading && events.length === 0

  const [isNearBottom, setIsNearBottom] = useState(true)

  const socketSnapshot = useAgentChatSocket(liveAgentId)
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(liveAgentId)
  const rosterQuery = useAgentRoster()

  const autoScrollPinnedRef = useRef(autoScrollPinned)
  useEffect(() => {
    autoScrollPinnedRef.current = autoScrollPinned
  }, [autoScrollPinned])
  const autoScrollPinSuppressedUntilRef = useRef(autoScrollPinSuppressedUntil)
  useEffect(() => {
    autoScrollPinSuppressedUntilRef.current = autoScrollPinSuppressedUntil
  }, [autoScrollPinSuppressedUntil])
  const forceScrollOnNextUpdateRef = useRef(false)
  const didInitialScrollRef = useRef(false)
  const isNearBottomRef = useRef(isNearBottom)

  // Track if we should scroll on next content update (captured before DOM changes)
  const shouldScrollOnNextUpdateRef = useRef(autoScrollPinned)

  useEffect(() => {
    didInitialScrollRef.current = false
  }, [activeAgentId])

  useEffect(() => {
    isNearBottomRef.current = isNearBottom
  }, [isNearBottom])

  useEffect(() => {
    // Skip initialization for new agent (null agentId)
    if (!activeAgentId) return
    const pendingMeta = pendingAgentMetaRef.current
    const resolvedPendingMeta = pendingMeta && pendingMeta.agentId === activeAgentId ? pendingMeta : null
    pendingAgentMetaRef.current = null
    const run = async () => {
      await initialize(activeAgentId, {
        agentColorHex: resolvedPendingMeta?.agentColorHex ?? agentColor,
        agentName: resolvedPendingMeta?.agentName ?? agentName,
        agentAvatarUrl: resolvedPendingMeta?.agentAvatarUrl ?? agentAvatarUrl,
      })
      if (showContextSwitcher) {
        void refreshContext()
      }
    }
    void run()
    // Fetch insights when agent initializes
    void fetchInsights()
  }, [
    activeAgentId,
    agentAvatarUrl,
    agentColor,
    agentName,
    fetchInsights,
    initialize,
    persistContextSession,
    refreshContext,
    showContextSwitcher,
  ])

  const getScrollContainer = useCallback(() => document.scrollingElement ?? document.documentElement ?? document.body, [])

  const getScrollDistanceToBottom = useCallback(() => {
    const target = getScrollContainer()
    const documentHeight = target.scrollHeight
    const scrollTop = target.scrollTop
    const clientHeight = target.clientHeight
    return documentHeight - clientHeight - scrollTop
  }, [getScrollContainer])

  const getBottomGapOffset = useCallback(() => {
    const timeline = timelineRef.current
    if (!timeline) return 0
    const timelineStyle = window.getComputedStyle(timeline)
    const paddingBottom = parseFloat(timelineStyle.paddingBottom) || 0

    const rootStyle = window.getComputedStyle(document.documentElement)
    const composerHeightRaw = rootStyle.getPropertyValue('--composer-height')
    let composerHeight = composerHeightRaw ? parseFloat(composerHeightRaw) : 0

    if (!composerHeight) {
      const composer = document.getElementById('agent-composer-shell')
      composerHeight = composer?.getBoundingClientRect().height ?? 0
    }

    return Math.max(0, paddingBottom - composerHeight)
  }, [])
  const getAdjustedDistanceToBottom = useCallback(() => {
    const scrollDistance = getScrollDistanceToBottom()
    const bottomGapOffset = getBottomGapOffset()
    const visualGap = bottomGapOffset > 0 ? BOTTOM_PANEL_GAP_PX : 0
    return scrollDistance - bottomGapOffset + visualGap
  }, [getBottomGapOffset, getScrollDistanceToBottom])
  const updateIsNearBottom = useCallback(() => {
    const adjustedDistance = getAdjustedDistanceToBottom()
    const nextIsNearBottom = adjustedDistance <= SCROLL_END_TOLERANCE_PX
    isNearBottomRef.current = nextIsNearBottom
    setIsNearBottom((current) => (current === nextIsNearBottom ? current : nextIsNearBottom))
    return adjustedDistance
  }, [getAdjustedDistanceToBottom])

  useEffect(() => {
    const scroller = getScrollContainer()

    // Mobile detection for adaptive thresholds
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0

    // Use larger thresholds on mobile to account for browser chrome changes (address bar, keyboard)
    const restickThreshold = isTouchDevice ? 80 : 20
    const touchScrollThreshold = 40 // More intentional gesture required to unpin
    const isAutoPinSuppressed = () => {
      const suppressedUntil = autoScrollPinSuppressedUntilRef.current
      return typeof suppressedUntil === 'number' && suppressedUntil > Date.now()
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

    // Track scroll direction - only restick when user scrolls DOWN to bottom
    let lastScrollTop = scroller?.scrollTop ?? window.scrollY
    let scrollTicking = false

    const handleScroll = () => {
      if (scrollTicking) return
      scrollTicking = true
      requestAnimationFrame(() => {
        scrollTicking = false
        const target = scroller || document.documentElement || document.body
        const currentScrollTop = target.scrollTop
        const scrolledDown = currentScrollTop > lastScrollTop
        lastScrollTop = currentScrollTop
        const adjustedDistance = getAdjustedDistanceToBottom()
        const nextIsNearBottom = adjustedDistance <= SCROLL_END_TOLERANCE_PX
        const nearBottomForRestick = adjustedDistance <= restickThreshold
        isNearBottomRef.current = nextIsNearBottom
        setIsNearBottom((current) => (current === nextIsNearBottom ? current : nextIsNearBottom))

        // Only restick if user actively scrolled down to the bottom
        if (!autoScrollPinnedRef.current && scrolledDown && nearBottomForRestick && !isAutoPinSuppressed()) {
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
  }, [getAdjustedDistanceToBottom, getScrollContainer, setAutoScrollPinned])

  useEffect(() => {
    updateIsNearBottom()
    const handleResize = () => {
      updateIsNearBottom()
    }

    window.addEventListener('resize', handleResize)
    window.visualViewport?.addEventListener('resize', handleResize)
    window.visualViewport?.addEventListener('scroll', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      window.visualViewport?.removeEventListener('resize', handleResize)
      window.visualViewport?.removeEventListener('scroll', handleResize)
    }
  }, [updateIsNearBottom])

  // Unpin auto-scroll when processing ends so user's reading position is preserved
  const prevProcessingRef = useRef(processingActive)
  useEffect(() => {
    const wasProcessing = prevProcessingRef.current
    prevProcessingRef.current = processingActive
    if (wasProcessing && !processingActive && !isNearBottomRef.current) {
      setAutoScrollPinned(false)
    }
  }, [processingActive, setAutoScrollPinned])

  // Capture scroll decision BEFORE content changes to avoid race with scroll handler
  const prevEventsRef = useRef(events)
  const prevStreamingRef = useRef(streaming)

  if (events !== prevEventsRef.current || streaming !== prevStreamingRef.current) {
    shouldScrollOnNextUpdateRef.current = autoScrollPinned
    prevEventsRef.current = events
    prevStreamingRef.current = streaming
  }

  const pendingScrollFrameRef = useRef<number | null>(null)

  const scrollToBottom = useCallback(() => {
    if (pendingScrollFrameRef.current !== null) {
      return
    }
    pendingScrollFrameRef.current = requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      const adjustedDistance = getAdjustedDistanceToBottom()
      if (adjustedDistance > 0) {
        window.scrollBy({ top: adjustedDistance })
      }
      updateIsNearBottom()
    })
  }, [getAdjustedDistanceToBottom, updateIsNearBottom])

  // Keep track of composer height to adjust scroll when it changes
  const prevComposerHeight = useRef<number | null>(null)

  const jumpToBottom = useCallback(() => {
    const target = document.scrollingElement ?? document.documentElement ?? document.body
    // Scroll to a very large number to ensure we hit the bottom regardless of recent layout changes
    window.scrollTo({ top: target.scrollHeight + 10000, behavior: 'auto' })
    updateIsNearBottom()
  }, [updateIsNearBottom])

  useEffect(() => {
    const composer = document.getElementById('agent-composer-shell')
    if (!composer) return

    const observer = new ResizeObserver((entries) => {
      const height = entries[0].borderBoxSize?.[0]?.blockSize ?? entries[0].contentRect.height

      if (prevComposerHeight.current !== null) {
        const delta = height - prevComposerHeight.current
        // If composer grew and we're at the bottom, scroll down to keep content visible
        if (delta > 0 && (autoScrollPinnedRef.current || isNearBottomRef.current)) {
          window.scrollBy({ top: delta })
        }
      }

      prevComposerHeight.current = height

      if (autoScrollPinnedRef.current) {
        // Ensure we stay pinned if we were pinned
        jumpToBottom()
      } else {
        updateIsNearBottom()
      }
    })

    observer.observe(composer)
    return () => observer.disconnect()
  }, [jumpToBottom, updateIsNearBottom])

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
    setTimelineNode(node)
  }, [])

  // Observe timeline changes (e.g. images loading) to keep pinned to bottom
  useEffect(() => {
    if (!timelineNode) return

    const observer = new ResizeObserver(() => {
      if (autoScrollPinnedRef.current) {
        jumpToBottom()
      } else {
        updateIsNearBottom()
      }
    })

    observer.observe(timelineNode)
    return () => observer.disconnect()
  }, [timelineNode, jumpToBottom, updateIsNearBottom])

  useEffect(() => () => {
    if (pendingScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingScrollFrameRef.current)
    }
  }, [])

  useEffect(() => {
    if (isNewAgent) {
      didInitialScrollRef.current = true
      return
    }
    if (!initialLoading && events.length && !didInitialScrollRef.current) {
      didInitialScrollRef.current = true
      setAutoScrollPinned(true)
      // Use a small timeout to allow layout to settle before jumping
      requestAnimationFrame(() => jumpToBottom())
    }
  }, [events.length, initialLoading, isNewAgent, jumpToBottom, setAutoScrollPinned])

  useLayoutEffect(() => {
    if (shouldScrollOnNextUpdateRef.current || forceScrollOnNextUpdateRef.current) {
      forceScrollOnNextUpdateRef.current = false
      scrollToBottom()
      return
    }
    updateIsNearBottom()
  }, [
    scrollToBottom,
    updateIsNearBottom,
    events,
    streaming,
    loadingOlder,
    loadingNewer,
    hasMoreNewer,
    hasUnseenActivity,
    initialLoading,
    processingActive,
    awaitingResponse,
  ])

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
  const hasSelectedAgent = Boolean(activeAgentId)
  const allowAgentRefresh = hasSelectedAgent && !contextSwitching
  const contextSwitcher = useMemo(() => {
    if (!contextData || !contextData.organizationsEnabled || contextData.organizations.length === 0) {
      return null
    }
    return {
      current: contextData.context,
      personal: contextData.personal,
      organizations: contextData.organizations,
      onSwitch: switchContext,
      isBusy: contextSwitching,
      errorMessage: contextError,
    }
  }, [contextData, contextError, contextSwitching, switchContext])
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
    const name = isSelectionView
      ? 'Select a conversation'
      : isNewAgent
        ? 'New Agent'
        : (resolvedAgentName || 'Agent')
    document.title = `${name} · Gobii`
  }, [isNewAgent, isSelectionView, resolvedAgentName])

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

  // Detect if the requested agent doesn't exist (deleted or never existed)
  const agentNotFound = useMemo(() => {
    // Not applicable for new agent creation
    if (isNewAgent) return false
    // Wait for both roster and initial load to complete
    if (rosterQuery.isLoading || initialLoading) return false
    // Check if agent exists in roster
    const agentInRoster = rosterAgents.some((agent) => agent.id === activeAgentId)
    // If there's an error loading the agent AND it's not in the roster, it's not found
    // Also consider not found if roster loaded but agent isn't there and we have an error
    if (!agentInRoster && error) return true
    // If roster loaded, agent isn't in roster, and we have no events (failed to load), mark as not found
    if (!agentInRoster && !loading && events.length === 0) return true
    return false
  }, [isNewAgent, rosterQuery.isLoading, initialLoading, rosterAgents, activeAgentId, error, loading, events.length])

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
    forceScrollOnNextUpdateRef.current = true
    await jumpToLatest()
    setAutoScrollPinned(true)
    scrollToBottom()
  }

  const handleSend = async (body: string, attachments: File[] = []) => {
    if (!activeAgentId && !isNewAgent) {
      return
    }
    // If this is a new agent, create it first then navigate to it
    if (isNewAgent) {
      try {
        const result = await createAgent(body)
        const createdAgentName = result.agent_name?.trim() || 'Agent'
        pendingAgentMetaRef.current = {
          agentId: result.agent_id,
          agentName: createdAgentName,
        }
        queryClient.setQueryData<AgentRosterEntry[]>(['agent-roster'], (current) =>
          mergeRosterEntry(current, {
            id: result.agent_id,
            name: createdAgentName,
            avatarUrl: null,
            displayColorHex: null,
            isActive: true,
            shortDescription: '',
          }),
        )
        void queryClient.invalidateQueries({ queryKey: ['agent-roster'] })
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
  const isProcessing = allowAgentRefresh && (processingActive || awaitingResponse || (streaming && !streaming.done))
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
    if (!allowAgentRefresh || !streaming || streaming.done) {
      return () => undefined
    }
    const interval = window.setInterval(() => {
      void refreshProcessing()
    }, STREAMING_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [allowAgentRefresh, refreshProcessing, streaming])

  useEffect(() => {
    if (contextSwitching || !showContextSwitcher || !activeAgentId || !rosterQuery.isSuccess) {
      return
    }
    void refreshContext()
  }, [
    activeAgentId,
    contextSwitching,
    refreshContext,
    rosterQuery.dataUpdatedAt,
    rosterQuery.isSuccess,
    showContextSwitcher,
  ])

  useEffect(() => {
    if (!allowAgentRefresh || !streaming || streaming.done) {
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
    allowAgentRefresh,
    finalizeStreaming,
    processingActive,
    refreshLatest,
    streaming,
    streamingLastUpdatedAt,
  ])

  const selectionMainClassName = `min-h-screen has-sidebar${selectionSidebarCollapsed ? ' has-sidebar--collapsed' : ''}`
  const selectionSidebarProps = {
    agents: rosterAgents,
    activeAgentId: null,
    loading: rosterQuery.isLoading,
    errorMessage: rosterErrorMessage,
    onSelectAgent: handleSelectAgent,
    onCreateAgent: handleCreateAgent,
    defaultCollapsed: selectionSidebarCollapsed,
    onToggle: setSelectionSidebarCollapsed,
    contextSwitcher: contextSwitcher ?? undefined,
  }
  const renderSelectionLayout = (content: ReactNode) => (
    <div className="agent-chat-page min-h-screen">
      <ChatSidebar {...selectionSidebarProps} />
      <main className={selectionMainClassName}>{content}</main>
    </div>
  )

  if (isSelectionView) {
    return renderSelectionLayout(
      <AgentSelectState
        hasAgents={rosterAgents.length > 0}
        onCreateAgent={handleCreateAgent}
      />,
    )
  }

  // Show a dedicated not-found state with sidebar still accessible
  if (agentNotFound) {
    return renderSelectionLayout(
      <AgentNotFoundState
        hasOtherAgents={rosterAgents.length > 0}
        onCreateAgent={handleCreateAgent}
      />,
    )
  }

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
        insightsPanelStorageKey={activeAgentId}
        switchingAgentId={switchingAgentId}
        rosterLoading={rosterQuery.isLoading}
        rosterError={rosterErrorMessage}
        onSelectAgent={handleSelectAgent}
        onCreateAgent={handleCreateAgent}
        contextSwitcher={contextSwitcher ?? undefined}
        onClose={onClose}
        events={isNewAgent ? [] : events}
        hasMoreOlder={isNewAgent ? false : hasMoreOlder}
        hasMoreNewer={isNewAgent ? false : hasMoreNewer}
        oldestCursor={isNewAgent ? null : (events.length ? events[0].cursor : null)}
        newestCursor={isNewAgent ? null : (events.length ? events[events.length - 1].cursor : null)}
        processingActive={isNewAgent ? false : processingActive}
        processingStartedAt={isNewAgent ? null : processingStartedAt}
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
        onComposerFocus={handleJumpToLatest}
        autoFocusComposer
        isNearBottom={isNearBottom}
        hasUnseenActivity={isNewAgent ? false : hasUnseenActivity}
        timelineRef={captureTimelineRef}
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
