import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Plus } from 'lucide-react'

import { createAgent, updateAgent } from '../api/agents'
import { fetchAgentSpawnIntent, type AgentSpawnIntent } from '../api/agentSpawnIntent'
import type { ConsoleContext } from '../api/context'
import { fetchUsageBurnRate, fetchUsageSummary } from '../components/usage/api'
import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { AgentIntelligenceGateModal } from '../components/agentChat/AgentIntelligenceGateModal'
import { CollaboratorInviteDialog } from '../components/agentChat/CollaboratorInviteDialog'
import { ChatSidebar } from '../components/agentChat/ChatSidebar'
import type { ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { useAgentQuickSettings } from '../hooks/useAgentQuickSettings'
import { useAgentAddons } from '../hooks/useAgentAddons'
import { useConsoleContextSwitcher } from '../hooks/useConsoleContextSwitcher'
import { useAgentChatStore } from '../stores/agentChatStore'
import { useSubscriptionStore, type PlanTier } from '../stores/subscriptionStore'
import type { AgentRosterEntry } from '../types/agentRoster'
import type { KanbanBoardSnapshot, TimelineEvent } from '../types/agentChat'
import type { DailyCreditsUpdatePayload } from '../types/dailyCredits'
import type { AgentSetupMetadata } from '../types/insight'
import type { UsageBurnRateResponse, UsageSummaryResponse } from '../components/usage'
import type { IntelligenceTierKey } from '../types/llmIntelligence'
import { storeConsoleContext } from '../util/consoleContextStorage'
import { track, AnalyticsEvent } from '../util/analytics'
import { appendReturnTo } from '../util/returnTo'

function deriveFirstName(agentName?: string | null): string {
  if (!agentName) return 'Agent'
  const [first] = agentName.trim().split(/\s+/, 1)
  return first || 'Agent'
}

const LOW_CREDIT_DAY_THRESHOLD = 2

type IntelligenceGateReason = 'plan' | 'credits' | 'both'

type IntelligenceGateState = {
  reason: IntelligenceGateReason
  selectedTier: IntelligenceTierKey
  allowedTier: IntelligenceTierKey
  multiplier: number | null
  estimatedDaysRemaining: number | null
  burnRatePerDay: number | null
}

type SpawnIntentStatus = 'idle' | 'loading' | 'ready' | 'done'

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

function hasAgentResponse(events: TimelineEvent[]): boolean {
  return events.some((event) => {
    return event.kind === 'message' && Boolean(event.message.isOutbound)
  })
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
  agentEmail?: string | null
  agentSms?: string | null
  collaboratorInviteUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  canManageCollaborators?: boolean | null
  isCollaborator?: boolean | null
  onClose?: () => void
  onCreateAgent?: () => void
  onAgentCreated?: (agentId: string) => void
  showContextSwitcher?: boolean
  persistContextSession?: boolean
  onContextSwitch?: (context: ConsoleContext) => void
}

const STREAMING_STALE_MS = 6000
  const STREAMING_REFRESH_INTERVAL_MS = 6000
// Threshold for detecting intentional scroll-up gesture on touch devices
const TOUCH_SCROLL_UNPIN_THRESHOLD = 40

export function AgentChatPage({
  agentId,
  agentName,
  agentColor,
  agentAvatarUrl,
  agentEmail,
  agentSms,
  collaboratorInviteUrl,
  viewerUserId,
  viewerEmail,
  canManageCollaborators,
  isCollaborator,
  onClose,
  onCreateAgent,
  onAgentCreated,
  showContextSwitcher = false,
  persistContextSession = true,
  onContextSwitch,
}: AgentChatPageProps) {
  const {
    data: quickSettingsPayload,
    isLoading: quickSettingsLoading,
    error: quickSettingsError,
    refetch: refetchQuickSettings,
    updateQuickSettings,
    updating: quickSettingsUpdating,
  } = useAgentQuickSettings(agentId)
  const {
    data: addonsPayload,
    refetch: refetchAddons,
    updateAddons,
    updating: addonsUpdating,
  } = useAgentAddons(agentId)
  const queryClient = useQueryClient()
  const { currentPlan, isProprietaryMode, ensureAuthenticated, upgradeModalSource } = useSubscriptionStore()
  const isNewAgent = agentId === null
  const isSelectionView = agentId === undefined
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const pendingCreateRef = useRef<{ body: string; attachments: File[]; tier: IntelligenceTierKey } | null>(null)
  const [intelligenceGate, setIntelligenceGate] = useState<IntelligenceGateState | null>(null)
  const [resolvedContext, setResolvedContext] = useState<ConsoleContext | null>(null)

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
  const [pendingAgentEmails, setPendingAgentEmails] = useState<Record<string, string>>({})
  const contactRefreshAttemptsRef = useRef<Record<string, number>>({})
  const effectiveContext = resolvedContext ?? contextData?.context ?? null
  const contextReady = Boolean(effectiveContext)
  const agentContextReady = activeAgentId ? Boolean(resolvedContext) : contextReady
  const liveAgentId = contextSwitching || !agentContextReady ? null : activeAgentId

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
  const [collaboratorInviteOpen, setCollaboratorInviteOpen] = useState(false)

  const handleCreditEvent = useCallback(() => {
    void refetchQuickSettings()
    void queryClient.invalidateQueries({ queryKey: ['usage-summary', 'agent-chat'], exact: false })
  }, [refetchQuickSettings, queryClient])
  const socketSnapshot = useAgentChatSocket(liveAgentId, { onCreditEvent: handleCreditEvent })
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(liveAgentId)
  const rosterContextKey = effectiveContext ? `${effectiveContext.type}:${effectiveContext.id}` : 'unknown'
  const rosterQuery = useAgentRoster({
    enabled: true,
    contextKey: rosterContextKey,
    forAgentId: agentId ?? undefined,
  })

  useEffect(() => {
    if (!contextData?.context) {
      return
    }
    const next = contextData.context
    if (
      (!agentId || contextSwitching) &&
      (resolvedContext?.id !== next.id || resolvedContext?.type !== next.type)
    ) {
      setResolvedContext(next)
    }
  }, [agentId, contextData?.context, contextSwitching, resolvedContext])

  useEffect(() => {
    if (rosterQuery.isSuccess && rosterQuery.data?.context) {
      setResolvedContext(rosterQuery.data.context)
      storeConsoleContext(rosterQuery.data.context)
      return
    }
    if (rosterQuery.isError && contextData?.context) {
      const next = contextData.context
      if (resolvedContext?.id !== next.id || resolvedContext?.type !== next.type) {
        // Use current console context as fallback so roster failures don't block chat init.
        setResolvedContext(next)
      }
    }
  }, [contextData?.context, resolvedContext, rosterQuery.isError, rosterQuery.isSuccess, rosterQuery.data?.context])

  const autoScrollPinnedRef = useRef(autoScrollPinned)
  // Sync ref during render (not in useEffect) so ResizeObservers see updated value immediately
  autoScrollPinnedRef.current = autoScrollPinned
  const autoScrollPinSuppressedUntilRef = useRef(autoScrollPinSuppressedUntil)
  useEffect(() => {
    autoScrollPinSuppressedUntilRef.current = autoScrollPinSuppressedUntil
  }, [autoScrollPinSuppressedUntil])
  const forceScrollOnNextUpdateRef = useRef(false)
  const didInitialScrollRef = useRef(false)
  const isNearBottomRef = useRef(isNearBottom)
  // Sync ref during render so ResizeObservers see updated value immediately
  isNearBottomRef.current = isNearBottom
  const composerFocusNudgeTimeoutRef = useRef<number | null>(null)

  // Track if we should scroll on next content update (captured before DOM changes)
  const shouldScrollOnNextUpdateRef = useRef(autoScrollPinned)

  useEffect(() => {
    didInitialScrollRef.current = false
  }, [activeAgentId])

  useEffect(() => {
    setCollaboratorInviteOpen(false)
  }, [activeAgentId])

  useEffect(() => {
    // Skip initialization for new agent (null agentId)
    if (!agentContextReady) return
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
    agentContextReady,
    persistContextSession,
    refreshContext,
    showContextSwitcher,
  ])

  // IntersectionObserver-based bottom detection - simpler and more reliable than scroll math
  const bottomSentinelRef = useRef<HTMLElement | null>(null)
  // Track whether sentinel exists to re-run effect when it appears
  const hasSentinel = !initialLoading && !hasMoreNewer
  useEffect(() => {
    // Find the bottom sentinel element and scroll container
    const sentinel = document.getElementById('timeline-bottom-sentinel')
    const container = document.getElementById('timeline-shell')
    bottomSentinelRef.current = sentinel

    // If no sentinel yet, mark as at-bottom by default (will be corrected when it appears)
    if (!sentinel || !container) {
      isNearBottomRef.current = true
      setIsNearBottom(true)
      return
    }

    const isAutoPinSuppressed = () => {
      const suppressedUntil = autoScrollPinSuppressedUntilRef.current
      return typeof suppressedUntil === 'number' && suppressedUntil > Date.now()
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        const isVisible = entry.isIntersecting
        isNearBottomRef.current = isVisible
        setIsNearBottom(isVisible)

        // Auto-restick when user scrolls to bottom (sentinel becomes visible)
        if (isVisible && !autoScrollPinnedRef.current && !isAutoPinSuppressed()) {
          setAutoScrollPinned(true)
        }
      },
      {
        // Use container as root for container scrolling
        root: container,
        // 100px buffer so we detect "near bottom" before hitting the exact bottom
        rootMargin: '0px 0px 100px 0px',
        threshold: 0,
      },
    )

    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [setAutoScrollPinned, hasSentinel])

  // Detect user scrolling UP to immediately unpin (wheel, touch, keyboard)
  useEffect(() => {
    const container = document.getElementById('timeline-shell')
    if (!container) return

    // Detect scroll-up via wheel
    const handleWheel = (e: WheelEvent) => {
      if (e.deltaY < 0 && autoScrollPinnedRef.current) {
        setAutoScrollPinned(false)
      }
    }

    // Detect scroll-up via touch gesture
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
      // Require intentional gesture: larger movement AND not too fast (avoid accidental taps)
      if (deltaY > TOUCH_SCROLL_UNPIN_THRESHOLD && elapsed > 50) {
        setAutoScrollPinned(false)
      }
    }

    // Detect scroll-up via keyboard
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!autoScrollPinnedRef.current) return
      const scrollUpKeys = ['ArrowUp', 'PageUp', 'Home']
      if (scrollUpKeys.includes(e.key)) {
        setAutoScrollPinned(false)
      }
    }

    // Listen on the container, not window
    container.addEventListener('wheel', handleWheel, { passive: true })
    container.addEventListener('touchstart', handleTouchStart, { passive: true })
    container.addEventListener('touchmove', handleTouchMove, { passive: true })
    window.addEventListener('keydown', handleKeyDown) // Keyboard stays on window

    return () => {
      container.removeEventListener('wheel', handleWheel)
      container.removeEventListener('touchstart', handleTouchStart)
      container.removeEventListener('touchmove', handleTouchMove)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [setAutoScrollPinned])

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

  const jumpToBottom = useCallback(() => {
    // Container scrolling: scroll the timeline-shell, not the window
    const container = document.getElementById('timeline-shell')
    const sentinel = document.getElementById('timeline-bottom-sentinel')
    if (sentinel) {
      // scrollIntoView is more reliable across browsers
      sentinel.scrollIntoView({ block: 'end', behavior: 'auto' })
    } else if (container) {
      container.scrollTop = container.scrollHeight + 10000
    }
    // Immediately mark as at-bottom (IntersectionObserver will confirm, but this avoids race conditions)
    isNearBottomRef.current = true
    setIsNearBottom(true)
  }, [])

  const scrollToBottom = useCallback(() => {
    if (pendingScrollFrameRef.current !== null) {
      return
    }
    pendingScrollFrameRef.current = requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      jumpToBottom()
    })
  }, [jumpToBottom])

  // Keep track of composer height to adjust scroll when it changes
  const prevComposerHeight = useRef<number | null>(null)

  useEffect(() => {
    const composer = document.getElementById('agent-composer-shell')
    const container = document.getElementById('timeline-shell')
    if (!composer || !container) return

    const observer = new ResizeObserver((entries) => {
      const height = entries[0].borderBoxSize?.[0]?.blockSize ?? entries[0].contentRect.height

      if (prevComposerHeight.current !== null) {
        const delta = height - prevComposerHeight.current
        // If composer grew and we're at the bottom, scroll down to keep content visible
        if (delta > 0 && (autoScrollPinnedRef.current || isNearBottomRef.current)) {
          container.scrollTop += delta
        }
      }

      prevComposerHeight.current = height

      // If pinned, ensure we stay at the bottom
      if (autoScrollPinnedRef.current) {
        jumpToBottom()
      }
      // IntersectionObserver handles isNearBottom updates automatically
    })

    observer.observe(composer)
    return () => observer.disconnect()
  }, [jumpToBottom])

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
    setTimelineNode(node)
  }, [])

  // Observe timeline changes (e.g. images loading) to keep pinned to bottom
  useEffect(() => {
    if (!timelineNode) return

    const observer = new ResizeObserver(() => {
      // If pinned, ensure we stay at the bottom when content changes
      if (autoScrollPinnedRef.current) {
        jumpToBottom()
      }
      // IntersectionObserver handles isNearBottom updates automatically
    })

    observer.observe(timelineNode)
    return () => observer.disconnect()
  }, [timelineNode, jumpToBottom])

  useEffect(() => () => {
    if (pendingScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingScrollFrameRef.current)
    }
  }, [])

  useEffect(() => () => {
    if (composerFocusNudgeTimeoutRef.current !== null) {
      window.clearTimeout(composerFocusNudgeTimeoutRef.current)
    }
  }, [])

  useEffect(() => {
    if (isNewAgent) {
      // New agent: no events yet, but ensure auto-scroll is pinned for when content arrives
      didInitialScrollRef.current = true
      setAutoScrollPinned(true)
      return
    }
    if (!initialLoading && events.length && !didInitialScrollRef.current) {
      didInitialScrollRef.current = true
      setAutoScrollPinned(true)
      // Immediate scroll attempt
      jumpToBottom()
      // Plus delayed scroll to catch any async layout (images, fonts, etc)
      const timeout = setTimeout(() => jumpToBottom(), 50)
      return () => clearTimeout(timeout)
    }
  }, [events.length, initialLoading, isNewAgent, jumpToBottom, setAutoScrollPinned])

  useLayoutEffect(() => {
    if (shouldScrollOnNextUpdateRef.current || forceScrollOnNextUpdateRef.current) {
      shouldScrollOnNextUpdateRef.current = false
      forceScrollOnNextUpdateRef.current = false
      // Scroll synchronously in layout effect to avoid visual flash
      jumpToBottom()
    }
    // IntersectionObserver handles isNearBottom updates automatically
  }, [
    jumpToBottom,
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

  const rosterAgents = useMemo(
    () => (contextReady ? rosterQuery.data?.agents ?? [] : []),
    [contextReady, rosterQuery.data?.agents],
  )
  const activeRosterMeta = useMemo(
    () => rosterAgents.find((agent) => agent.id === activeAgentId) ?? null,
    [activeAgentId, rosterAgents],
  )
  const isStoreSynced = storeAgentId === activeAgentId
  const resolvedAgentName = (isStoreSynced ? storedAgentName : activeRosterMeta?.name) ?? agentName ?? null
  const resolvedAvatarUrl = (isStoreSynced ? storedAgentAvatarUrl : activeRosterMeta?.avatarUrl) ?? agentAvatarUrl ?? null
  const resolvedAgentColorHex =
    (isStoreSynced ? agentColorHex : activeRosterMeta?.displayColorHex) ?? agentColor ?? null
  const pendingAgentEmail = activeAgentId ? pendingAgentEmails[activeAgentId] ?? null : null
  const resolvedAgentEmail = activeRosterMeta?.email ?? pendingAgentEmail ?? agentEmail ?? null
  const resolvedAgentSms = activeRosterMeta?.sms ?? agentSms ?? null
  const resolvedIsOrgOwned = activeRosterMeta?.isOrgOwned ?? false
  const activeIsCollaborator = activeRosterMeta?.isCollaborator ?? (isCollaborator ?? false)
  const activeCanManageAgent = activeRosterMeta?.canManageAgent ?? !activeIsCollaborator
  const activeCanManageCollaborators = activeRosterMeta?.canManageCollaborators ?? (canManageCollaborators ?? true)
  const hasAgentReply = useMemo(() => hasAgentResponse(events), [events])
  useEffect(() => {
    if (!activeAgentId || !activeRosterMeta?.email) {
      return
    }
    setPendingAgentEmails((current) => {
      if (!current[activeAgentId]) {
        return current
      }
      const next = { ...current }
      delete next[activeAgentId]
      return next
    })
  }, [activeAgentId, activeRosterMeta?.email])

  useEffect(() => {
    if (!activeAgentId || !resolvedAgentEmail) {
      return
    }
    if (contactRefreshAttemptsRef.current[activeAgentId]) {
      delete contactRefreshAttemptsRef.current[activeAgentId]
    }
  }, [activeAgentId, resolvedAgentEmail])

  useEffect(() => {
    if (!activeAgentId || isNewAgent || resolvedAgentEmail || !hasAgentReply) {
      return
    }
    if (rosterQuery.isFetching) {
      return
    }
    const attempts = contactRefreshAttemptsRef.current[activeAgentId] ?? 0
    if (attempts >= 3) {
      return
    }
    contactRefreshAttemptsRef.current[activeAgentId] = attempts + 1
    const delayMs = attempts === 0 ? 500 : 2000
    const timeout = window.setTimeout(() => {
      void rosterQuery.refetch()
    }, delayMs)
    return () => window.clearTimeout(timeout)
  }, [
    activeAgentId,
    hasAgentReply,
    isNewAgent,
    resolvedAgentEmail,
    rosterQuery.isFetching,
    rosterQuery.refetch,
  ])
  const llmIntelligence = rosterQuery.data?.llmIntelligence ?? null
  const tierLabels = useMemo(() => {
    const map: Partial<Record<IntelligenceTierKey, string>> = {}
    for (const option of llmIntelligence?.options ?? []) {
      map[option.key] = option.label
    }
    return map
  }, [llmIntelligence?.options])
  const [draftIntelligenceTier, setDraftIntelligenceTier] = useState<string>('standard')
  const [intelligenceOverrides, setIntelligenceOverrides] = useState<Record<string, string>>({})
  const [intelligenceBusy, setIntelligenceBusy] = useState(false)
  const [intelligenceError, setIntelligenceError] = useState<string | null>(null)
  const [spawnIntent, setSpawnIntent] = useState<AgentSpawnIntent | null>(null)
  const [spawnIntentStatus, setSpawnIntentStatus] = useState<SpawnIntentStatus>('idle')
  const spawnIntentFetchedRef = useRef(false)
  const spawnIntentAutoSubmittedRef = useRef(false)
  const agentFirstName = useMemo(() => deriveFirstName(resolvedAgentName), [resolvedAgentName])
  const latestKanbanSnapshot = useMemo(() => getLatestKanbanSnapshot(events), [events])
  const hasSelectedAgent = Boolean(activeAgentId)
  const allowAgentRefresh = hasSelectedAgent && !contextSwitching && agentContextReady
  const rosterLoading = rosterQuery.isLoading || !agentContextReady
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

  useEffect(() => {
    if (isNewAgent) {
      setDraftIntelligenceTier('standard')
    }
    setIntelligenceError(null)
  }, [isNewAgent, activeAgentId])

  const spawnFlow = useMemo(() => {
    if (!isNewAgent || typeof window === 'undefined') {
      return false
    }
    const params = new URLSearchParams(window.location.search)
    const flag = (params.get('spawn') || '').toLowerCase()
    return flag === '1' || flag === 'true' || flag === 'yes' || flag === 'on'
  }, [isNewAgent])

  useEffect(() => {
    if (!isNewAgent || !spawnFlow) {
      spawnIntentFetchedRef.current = false
      spawnIntentAutoSubmittedRef.current = false
      setSpawnIntent(null)
      setSpawnIntentStatus('idle')
      return
    }
    if (spawnIntentFetchedRef.current) {
      return
    }
    spawnIntentFetchedRef.current = true
    setSpawnIntentStatus('loading')
    let isActive = true
    const loadSpawnIntent = async () => {
      try {
        const intent = await fetchAgentSpawnIntent()
        if (!isActive) {
          return
        }
        const charter = intent?.charter?.trim()
        if (!charter) {
          setSpawnIntentStatus('done')
          return
        }
        setSpawnIntent(intent)
        setSpawnIntentStatus('ready')
      } catch (err) {
        if (!isActive) {
          return
        }
        setSpawnIntentStatus('done')
      }
    }
    void loadSpawnIntent()
    return () => {
      isActive = false
    }
  }, [isNewAgent, spawnFlow])

  const resolvedIntelligenceTier = useMemo(() => {
    if (isNewAgent) {
      return draftIntelligenceTier
    }
    if (activeAgentId && intelligenceOverrides[activeAgentId]) {
      return intelligenceOverrides[activeAgentId]
    }
    return activeRosterMeta?.preferredLlmTier ?? 'standard'
  }, [activeAgentId, activeRosterMeta?.preferredLlmTier, draftIntelligenceTier, intelligenceOverrides, isNewAgent])

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
      isOrgOwned: false,
    }
  }, [activeAgentId, resolvedAgentColorHex, resolvedAgentName, resolvedAvatarUrl])
  const sidebarAgents = useMemo(() => {
    if (!contextReady) {
      return []
    }
    if (!activeAgentId) {
      return rosterAgents
    }
    const hasActive = rosterAgents.some((agent) => agent.id === activeAgentId)
    if (hasActive || !fallbackAgent) {
      return rosterAgents
    }
    return [fallbackAgent, ...rosterAgents]
  }, [activeAgentId, contextReady, fallbackAgent, rosterAgents])

  const resolvedInviteUrl = useMemo(() => {
    if (collaboratorInviteUrl) {
      return collaboratorInviteUrl
    }
    if (activeAgentId) {
      return `/console/agents/${activeAgentId}/`
    }
    return null
  }, [collaboratorInviteUrl, activeAgentId])

  const isCollaboratorOnly = Boolean(activeIsCollaborator && !activeCanManageAgent)
  const canShareCollaborators = Boolean(
    resolvedInviteUrl
      && !isSelectionView
      && !isNewAgent
      && activeCanManageCollaborators
      && !isCollaboratorOnly,
  )

  const handleOpenCollaboratorInvite = useCallback(() => {
    setCollaboratorInviteOpen(true)
  }, [])

  const handleCloseCollaboratorInvite = useCallback(() => {
    setCollaboratorInviteOpen(false)
  }, [])

  // Detect if the requested agent doesn't exist (deleted or never existed)
  const agentNotFound = useMemo(() => {
    if (!contextReady) return false
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
  }, [contextReady, isNewAgent, rosterQuery.isLoading, initialLoading, rosterAgents, activeAgentId, error, loading, events.length])

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

  const handleJumpToLatest = useCallback(async () => {
    forceScrollOnNextUpdateRef.current = true
    await jumpToLatest()
    setAutoScrollPinned(true)
    scrollToBottom()
  }, [jumpToLatest, scrollToBottom, setAutoScrollPinned])

  const handleComposerFocus = useCallback(() => {
    if (typeof window === 'undefined') return
    const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0
    if (!isTouch) return

    setAutoScrollPinned(true)
    forceScrollOnNextUpdateRef.current = true
    jumpToBottom()

    if (composerFocusNudgeTimeoutRef.current !== null) {
      window.clearTimeout(composerFocusNudgeTimeoutRef.current)
    }
    composerFocusNudgeTimeoutRef.current = window.setTimeout(() => {
      jumpToBottom()
      scrollToBottom()
      composerFocusNudgeTimeoutRef.current = null
    }, 180)
  }, [jumpToBottom, scrollToBottom, setAutoScrollPinned])

  const handleUpgrade = useCallback(async (plan: PlanTier, source?: string) => {
    const resolvedSource = source ?? upgradeModalSource ?? 'upgrade_modal'
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
      plan,
      source: resolvedSource,
    })
    const checkoutUrl = appendReturnTo(plan === 'startup' ? '/subscribe/startup/' : '/subscribe/scale/')
    window.open(checkoutUrl, '_top')
  }, [ensureAuthenticated, upgradeModalSource])

  const createNewAgent = useCallback(
    async (body: string, tier: IntelligenceTierKey) => {
      try {
        const result = await createAgent(body, tier)
        const createdAgentName = result.agent_name?.trim() || 'Agent'
        const createdAgentEmail = result.agent_email?.trim() || null
        pendingAgentMetaRef.current = {
          agentId: result.agent_id,
          agentName: createdAgentName,
        }
        if (createdAgentEmail) {
          setPendingAgentEmails((current) => ({ ...current, [result.agent_id]: createdAgentEmail }))
        }
        queryClient.setQueryData<AgentRosterEntry[]>(['agent-roster'], (current) =>
          mergeRosterEntry(current, {
            id: result.agent_id,
            name: createdAgentName,
            avatarUrl: null,
            displayColorHex: null,
            isActive: true,
            shortDescription: '',
            email: createdAgentEmail,
          }),
        )
        void queryClient.invalidateQueries({ queryKey: ['agent-roster'] })
        onAgentCreated?.(result.agent_id)
      } catch (err) {
        console.error('Failed to create agent:', err)
      }
    },
    [onAgentCreated, queryClient, setPendingAgentEmails],
  )

  const handleIntelligenceChange = useCallback(
    async (tier: string): Promise<boolean> => {
      if (isNewAgent) {
        setDraftIntelligenceTier(tier)
        return true
      }
      if (!activeAgentId) {
        return false
      }
      const previousTier = resolvedIntelligenceTier
      setIntelligenceOverrides((current) => ({ ...current, [activeAgentId]: tier }))
      setIntelligenceBusy(true)
      setIntelligenceError(null)
      try {
        await updateAgent(activeAgentId, { preferred_llm_tier: tier })
        void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
        return true
      } catch (err) {
        setIntelligenceOverrides((current) => ({ ...current, [activeAgentId]: previousTier }))
        setIntelligenceError('Unable to update intelligence level.')
        return false
      } finally {
        setIntelligenceBusy(false)
      }
    },
    [activeAgentId, isNewAgent, queryClient, resolvedIntelligenceTier],
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
  const hydratedInsights = useMemo(() => {
    if (!resolvedAgentEmail && !resolvedAgentSms) {
      return availableInsights
    }
    return availableInsights.map((insight) => {
      if (insight.insightType !== 'agent_setup') {
        return insight
      }
      const metadata = insight.metadata as AgentSetupMetadata
      const nextEmail = resolvedAgentEmail ?? metadata.agentEmail ?? null
      const nextSms = resolvedAgentSms ?? metadata.sms?.agentNumber ?? null
      if (nextEmail === metadata.agentEmail && nextSms === metadata.sms?.agentNumber) {
        return insight
      }
      return {
        ...insight,
        metadata: {
          ...metadata,
          agentEmail: nextEmail,
          sms: {
            ...metadata.sms,
            agentNumber: nextSms,
          },
        },
      }
    })
  }, [availableInsights, resolvedAgentEmail, resolvedAgentSms])

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
    if (
      contextSwitching ||
      !showContextSwitcher ||
      !activeAgentId ||
      !contextReady ||
      !resolvedContext ||
      !rosterQuery.isSuccess
    ) {
      return
    }
    void refreshContext()
  }, [
    activeAgentId,
    contextReady,
    contextSwitching,
    resolvedContext,
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

  const selectionMainClassName = `has-sidebar${selectionSidebarCollapsed ? ' has-sidebar--collapsed' : ''}`
  const selectionSidebarProps = {
    agents: rosterAgents,
    activeAgentId: null,
    loading: rosterLoading,
    errorMessage: rosterErrorMessage,
    onSelectAgent: handleSelectAgent,
    onCreateAgent: handleCreateAgent,
    defaultCollapsed: selectionSidebarCollapsed,
    onToggle: setSelectionSidebarCollapsed,
    contextSwitcher: contextSwitcher ?? undefined,
  }
  const renderSelectionLayout = (content: ReactNode) => (
    <div className="agent-chat-page">
      <ChatSidebar {...selectionSidebarProps} />
      <main className={selectionMainClassName}>{content}</main>
    </div>
  )

  if (isSelectionView) {
    if (!contextReady || rosterLoading) {
      return renderSelectionLayout(
        <div className="flex min-h-[60vh] items-center justify-center">
          <p className="text-sm font-medium text-slate-500">Loading workspace…</p>
        </div>,
      )
    }
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

  const canManageDailyCredits = Boolean(activeAgentId && !isNewAgent)
  const dailyCreditsInfo = canManageDailyCredits ? quickSettingsPayload?.settings?.dailyCredits ?? null : null
  const dailyCreditsStatus = canManageDailyCredits ? quickSettingsPayload?.status?.dailyCredits ?? null : null
  const contactCap = addonsPayload?.contactCap ?? null
  const contactCapStatus = addonsPayload?.status?.contactCap ?? null
  const contactPackOptions = addonsPayload?.contactPacks?.options ?? []
  const contactPackCanManageBilling = Boolean(addonsPayload?.contactPacks?.canManageBilling)
  const taskPackOptions = addonsPayload?.taskPacks?.options ?? []
  const taskPackCanManageBilling = Boolean(addonsPayload?.taskPacks?.canManageBilling)
  const contactPackShowUpgrade = true
  const taskPackShowUpgrade = true
  const contactPackManageUrl = addonsPayload?.manageBillingUrl ?? null
  const hardLimitUpsell = Boolean(quickSettingsPayload?.meta?.plan?.isFree)
  const hardLimitUpgradeUrl = quickSettingsPayload?.meta?.upgradeUrl ?? null
  const dailyCreditsErrorMessage = quickSettingsError instanceof Error
    ? quickSettingsError.message
    : quickSettingsError
      ? 'Unable to load daily credits.'
      : null
  const handleUpdateDailyCredits = useCallback(
    async (payload: DailyCreditsUpdatePayload) => {
      await updateQuickSettings({ dailyCredits: payload })
    },
    [updateQuickSettings],
  )
  const handleUpdateContactPacks = useCallback(
    async (quantities: Record<string, number>) => {
      await updateAddons({ contactPacks: { quantities } })
    },
    [updateAddons],
  )
  const handleUpdateTaskPacks = useCallback(
    async (quantities: Record<string, number>) => {
      await updateAddons({ taskPacks: { quantities } })
      await queryClient.invalidateQueries({ queryKey: ['usage-summary', 'agent-chat'], exact: false })
    },
    [queryClient, updateAddons],
  )

  const shouldFetchUsageSummary = Boolean(contextReady && !isSelectionView && (activeAgentId || isNewAgent))
  const shouldFetchUsageBurnRate = Boolean(contextReady && !isSelectionView && isNewAgent)
  const usageContextKey = effectiveContext
    ? `${effectiveContext.type}:${effectiveContext.id}`
    : null
  const {
    data: usageSummary,
  } = useQuery<UsageSummaryResponse, Error>({
    queryKey: ['usage-summary', 'agent-chat', usageContextKey],
    queryFn: ({ signal }) => fetchUsageSummary({}, signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled: shouldFetchUsageSummary,
  })
  const burnRateTier = (resolvedIntelligenceTier || 'standard') as IntelligenceTierKey
  const {
    data: burnRateSummary,
    refetch: refetchBurnRateSummary,
  } = useQuery<UsageBurnRateResponse, Error>({
    queryKey: ['usage-burn-rate', 'agent-chat', usageContextKey, burnRateTier],
    queryFn: ({ signal }) => fetchUsageBurnRate({ tier: burnRateTier }, signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled: shouldFetchUsageBurnRate,
  })
  const taskQuota = usageSummary?.metrics.quota ?? null
  const extraTasksEnabled = Boolean(usageSummary?.extra_tasks?.enabled)
  const hasUnlimitedQuota = taskQuota ? taskQuota.total < 0 || taskQuota.available < 0 : false
  // Use < 1 threshold to catch "dust credits" (e.g., 0.001) that aren't enough to do anything
  const isOutOfTaskCredits = Boolean(taskQuota && !hasUnlimitedQuota && taskQuota.available < 1)
  const showTaskCreditsWarning = Boolean(
    taskQuota
    && !hasUnlimitedQuota
    && !extraTasksEnabled
    && (
      isOutOfTaskCredits
      || (taskQuota.available <= 100 && taskQuota.used_pct > 90)
    ),
  )
  const taskCreditsWarningVariant = showTaskCreditsWarning
    ? (isOutOfTaskCredits ? 'out' : 'low')
    : null
  const taskCreditsDismissKey = effectiveContext
    ? `${effectiveContext.type}:${effectiveContext.id}`
    : null
  const billingUrl = useMemo(() => {
    if (!effectiveContext) {
      return '/console/billing/'
    }
    if (effectiveContext.type === 'organization') {
      return `/console/billing/?org_id=${effectiveContext.id}`
    }
    return '/console/billing/'
  }, [effectiveContext])

  const closeGate = useCallback(() => {
    pendingCreateRef.current = null
    setIntelligenceGate(null)
  }, [])

  const buildGateAnalytics = useCallback((overrides: Record<string, unknown> = {}) => {
    if (!intelligenceGate) {
      return overrides
    }
    return {
      reason: intelligenceGate.reason,
      selectedTier: intelligenceGate.selectedTier,
      allowedTier: intelligenceGate.allowedTier,
      multiplier: intelligenceGate.multiplier,
      estimatedDaysRemaining: intelligenceGate.estimatedDaysRemaining,
      burnRatePerDay: intelligenceGate.burnRatePerDay,
      currentPlan,
      ...overrides,
    }
  }, [currentPlan, intelligenceGate])

  const handleGateDismiss = useCallback(() => {
    track(AnalyticsEvent.INTELLIGENCE_GATE_DISMISSED, buildGateAnalytics())
    closeGate()
  }, [buildGateAnalytics, closeGate])

  const handleGateUpgrade = useCallback((plan: PlanTier) => {
    closeGate()
    void handleUpgrade(plan, 'intelligence_gate')
  }, [closeGate, handleUpgrade])

  const handleGateAddPack = useCallback(() => {
    track(AnalyticsEvent.INTELLIGENCE_GATE_ADD_PACK_CLICKED, buildGateAnalytics())
    closeGate()
    if (typeof window !== 'undefined') {
      window.open(billingUrl, '_top')
    }
  }, [billingUrl, buildGateAnalytics, closeGate])

  const handleGateContinue = useCallback(() => {
    const pending = pendingCreateRef.current
    if (!pending || !intelligenceGate) {
      closeGate()
      return
    }
    const needsPlanUpgrade = intelligenceGate.reason === 'plan' || intelligenceGate.reason === 'both'
    const tierToUse = needsPlanUpgrade ? intelligenceGate.allowedTier : pending.tier
    track(AnalyticsEvent.INTELLIGENCE_GATE_CONTINUED, buildGateAnalytics({ chosenTier: tierToUse }))
    if (needsPlanUpgrade) {
      setDraftIntelligenceTier(tierToUse)
    }
    closeGate()
    void createNewAgent(pending.body, tierToUse)
  }, [buildGateAnalytics, closeGate, createNewAgent, intelligenceGate])

  const handleSend = useCallback(async (body: string, attachments: File[] = []) => {
    if (!activeAgentId && !isNewAgent) {
      return
    }
    // If this is a new agent, create it first then navigate to it
    if (isNewAgent) {
      const authenticated = await ensureAuthenticated()
      if (!authenticated) {
        return
      }
      const selectedTier = (resolvedIntelligenceTier || 'standard') as IntelligenceTierKey
      const option = llmIntelligence?.options.find((item) => item.key === selectedTier) ?? null
      const allowedTier = (llmIntelligence?.maxAllowedTier || 'standard') as IntelligenceTierKey
      const allowedRank = llmIntelligence?.maxAllowedTierRank ?? null
      const selectedRank = option?.rank ?? null
      const isLocked = typeof allowedRank === 'number' && typeof selectedRank === 'number'
        ? selectedRank > allowedRank
        : Boolean(llmIntelligence && !llmIntelligence.canEdit && selectedTier !== allowedTier)
      const multiplier = option?.multiplier ?? 1
      let estimatedDaysRemaining: number | null = null
      let burnRatePerDay: number | null = null
      let lowCredits = false
      let burnRatePayload = burnRateSummary
      if (!burnRatePayload && shouldFetchUsageBurnRate) {
        try {
          const refreshed = await refetchBurnRateSummary()
          burnRatePayload = refreshed.data
        } catch (err) {
          burnRatePayload = undefined
        }
      }

      const burnRateQuota = burnRatePayload?.quota ?? null
      const burnRateUnlimited = burnRateQuota?.unlimited ?? hasUnlimitedQuota
      const burnRateExtraEnabled = burnRatePayload?.extra_tasks?.enabled ?? extraTasksEnabled
      const projectedDays = burnRatePayload?.projection?.projected_days_remaining ?? null
      burnRatePerDay = burnRatePayload?.snapshot?.burn_rate_per_day ?? null

      if (!burnRateUnlimited && !burnRateExtraEnabled && projectedDays !== null) {
        estimatedDaysRemaining = projectedDays
        lowCredits = estimatedDaysRemaining <= LOW_CREDIT_DAY_THRESHOLD
      }
      if (isLocked || lowCredits) {
        const gateReason: IntelligenceGateReason = isLocked && lowCredits ? 'both' : isLocked ? 'plan' : 'credits'
        track(AnalyticsEvent.INTELLIGENCE_GATE_SHOWN, {
          reason: gateReason,
          selectedTier,
          allowedTier,
          multiplier: Number.isFinite(multiplier) ? multiplier : null,
          estimatedDaysRemaining,
          burnRatePerDay,
          currentPlan,
        })
        pendingCreateRef.current = { body, attachments, tier: selectedTier }
        setIntelligenceGate({
          reason: gateReason,
          selectedTier,
          allowedTier,
          multiplier: Number.isFinite(multiplier) ? multiplier : null,
          estimatedDaysRemaining,
          burnRatePerDay,
        })
        return
      }
      await createNewAgent(body, selectedTier)
      return
    }
    await sendMessage(body, attachments)
    if (!autoScrollPinnedRef.current) return
    scrollToBottom()
  }, [
    activeAgentId,
    burnRateSummary,
    createNewAgent,
    currentPlan,
    ensureAuthenticated,
    extraTasksEnabled,
    hasUnlimitedQuota,
    isNewAgent,
    llmIntelligence?.canEdit,
    llmIntelligence?.maxAllowedTier,
    llmIntelligence?.maxAllowedTierRank,
    llmIntelligence?.options,
    resolvedIntelligenceTier,
    refetchBurnRateSummary,
    scrollToBottom,
    sendMessage,
    shouldFetchUsageBurnRate,
  ])

  useEffect(() => {
    if (!isNewAgent || !spawnFlow || !spawnIntent?.charter?.trim()) {
      return
    }
    if (!contextReady || rosterQuery.isLoading) {
      return
    }
    if (spawnIntentAutoSubmittedRef.current) {
      return
    }
    if (spawnIntentStatus !== 'ready') {
      return
    }

    const preferredTierRaw = spawnIntent.preferred_llm_tier?.trim() || null
    if (preferredTierRaw) {
      let resolvedTier = preferredTierRaw
      if (llmIntelligence) {
        const isKnownTier = llmIntelligence.options.some((option) => option.key === preferredTierRaw)
        resolvedTier = isKnownTier ? preferredTierRaw : 'standard'
      }
      if (resolvedTier !== draftIntelligenceTier) {
        setDraftIntelligenceTier(resolvedTier)
        return
      }
    }

    spawnIntentAutoSubmittedRef.current = true
    const sendPromise = handleSend(spawnIntent.charter)
    sendPromise.finally(() => setSpawnIntentStatus('done'))
  }, [
    contextReady,
    draftIntelligenceTier,
    handleSend,
    isNewAgent,
    llmIntelligence,
    rosterQuery.isLoading,
    spawnFlow,
    spawnIntent,
    spawnIntentStatus,
  ])

  const showSpawnIntentLoader = Boolean(
    spawnFlow && isNewAgent && (spawnIntentStatus === 'loading' || spawnIntentStatus === 'ready'),
  )

  return (
    <div className="agent-chat-page">
      {error || (sessionStatus === 'error' && sessionError) ? (
        <div className="mx-auto w-full max-w-3xl px-4 py-2 text-sm text-rose-600">{error || sessionError}</div>
      ) : null}
      {intelligenceGate ? (
        <AgentIntelligenceGateModal
          open={Boolean(intelligenceGate)}
          reason={intelligenceGate.reason}
          selectedTier={intelligenceGate.selectedTier}
          allowedTier={intelligenceGate.allowedTier}
          tierLabels={tierLabels}
          multiplier={intelligenceGate.multiplier}
          estimatedDaysRemaining={intelligenceGate.estimatedDaysRemaining}
          burnRatePerDay={intelligenceGate.burnRatePerDay}
          currentPlan={currentPlan}
          showUpgradePlans={isProprietaryMode}
          showAddPack={isProprietaryMode && Boolean(billingUrl)}
          onUpgrade={handleGateUpgrade}
          onAddPack={handleGateAddPack}
          onContinue={handleGateContinue}
          onClose={handleGateDismiss}
        />
      ) : null}
      <CollaboratorInviteDialog
        open={collaboratorInviteOpen}
        agentName={resolvedAgentName || agentName}
        inviteUrl={resolvedInviteUrl}
        canManage={activeCanManageCollaborators}
        onClose={handleCloseCollaboratorInvite}
      />
      <AgentChatLayout
        agentId={activeAgentId}
        agentFirstName={isNewAgent ? 'New Agent' : agentFirstName}
        agentColorHex={resolvedAgentColorHex || undefined}
        agentAvatarUrl={resolvedAvatarUrl}
        agentEmail={resolvedAgentEmail}
        agentSms={resolvedAgentSms}
        agentName={isNewAgent ? 'New Agent' : (resolvedAgentName || 'Agent')}
        agentIsOrgOwned={resolvedIsOrgOwned}
        isCollaborator={isCollaboratorOnly}
        canManageAgent={activeCanManageAgent}
        hideInsightsPanel={isCollaboratorOnly}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        connectionStatus={connectionIndicator.status}
        connectionLabel={connectionIndicator.label}
        connectionDetail={connectionIndicator.detail}
        kanbanSnapshot={latestKanbanSnapshot}
        agentRoster={sidebarAgents}
        activeAgentId={activeAgentId}
        insightsPanelStorageKey={activeAgentId}
        switchingAgentId={switchingAgentId}
        rosterLoading={rosterLoading}
        rosterError={rosterErrorMessage}
        onSelectAgent={handleSelectAgent}
        onCreateAgent={handleCreateAgent}
        contextSwitcher={contextSwitcher ?? undefined}
        onComposerFocus={handleComposerFocus}
        onClose={onClose}
        dailyCredits={dailyCreditsInfo}
        dailyCreditsStatus={dailyCreditsStatus}
        dailyCreditsLoading={canManageDailyCredits ? quickSettingsLoading : false}
        dailyCreditsError={canManageDailyCredits ? dailyCreditsErrorMessage : null}
        onRefreshDailyCredits={canManageDailyCredits ? refetchQuickSettings : undefined}
        onUpdateDailyCredits={canManageDailyCredits ? handleUpdateDailyCredits : undefined}
        dailyCreditsUpdating={canManageDailyCredits ? quickSettingsUpdating : false}
        hardLimitShowUpsell={canManageDailyCredits ? hardLimitUpsell : false}
        hardLimitUpgradeUrl={canManageDailyCredits ? hardLimitUpgradeUrl : null}
        contactCap={contactCap}
        contactCapStatus={contactCapStatus}
        contactPackOptions={contactPackOptions}
        contactPackCanManageBilling={contactPackCanManageBilling}
        contactPackShowUpgrade={contactPackShowUpgrade}
        contactPackUpdating={addonsUpdating}
        onUpdateContactPacks={contactPackCanManageBilling ? handleUpdateContactPacks : undefined}
        taskPackOptions={taskPackOptions}
        taskPackCanManageBilling={taskPackCanManageBilling}
        taskPackUpdating={addonsUpdating}
        onUpdateTaskPacks={taskPackCanManageBilling ? handleUpdateTaskPacks : undefined}
        taskQuota={taskQuota}
        showTaskCreditsWarning={showTaskCreditsWarning}
        taskCreditsWarningVariant={taskCreditsWarningVariant}
        showTaskCreditsUpgrade={taskPackShowUpgrade}
        taskCreditsDismissKey={taskCreditsDismissKey}
        onRefreshAddons={refetchAddons}
        contactPackManageUrl={contactPackManageUrl}
        onShare={canShareCollaborators ? handleOpenCollaboratorInvite : undefined}
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
        streamingThinkingCollapsed={streamingThinkingCollapsed}
        onToggleStreamingThinking={handleToggleStreamingThinking}
        onLoadOlder={isNewAgent ? undefined : (hasMoreOlder ? loadOlder : undefined)}
        onLoadNewer={isNewAgent ? undefined : (hasMoreNewer ? loadNewer : undefined)}
        onSendMessage={handleSend}
        onJumpToLatest={handleJumpToLatest}
        autoFocusComposer
        isNearBottom={isNearBottom}
        hasUnseenActivity={isNewAgent ? false : hasUnseenActivity}
        timelineRef={captureTimelineRef}
        loadingOlder={isNewAgent ? false : loadingOlder}
        loadingNewer={isNewAgent ? false : loadingNewer}
        initialLoading={initialLoading}
        insights={isNewAgent ? [] : hydratedInsights}
        currentInsightIndex={currentInsightIndex}
        onDismissInsight={dismissInsight}
        onInsightIndexChange={setCurrentInsightIndex}
        onPauseChange={setInsightsPaused}
        isInsightsPaused={insightsPaused}
        onUpgrade={handleUpgrade}
        llmIntelligence={llmIntelligence}
        currentLlmTier={resolvedIntelligenceTier}
        onLlmTierChange={handleIntelligenceChange}
        allowLockedIntelligenceSelection={isNewAgent}
        llmTierSaving={intelligenceBusy}
        llmTierError={intelligenceError}
        spawnIntentLoading={showSpawnIntentLoader}
      />
    </div>
  )
}
