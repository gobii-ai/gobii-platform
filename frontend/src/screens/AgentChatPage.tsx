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
import { HttpError } from '../api/http'
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
const ROSTER_REFRESH_INTERVAL_MS = 20_000
const ROSTER_PENDING_AVATAR_REFRESH_INTERVAL_MS = 4_000
const ROSTER_PENDING_AVATAR_TRACK_WINDOW_MS = 90_000

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

type PendingAvatarTracking = Record<string, number>

function normalizeAvatarUrl(value: string | null | undefined): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  return trimmed ? trimmed : null
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

const AGENT_LIMIT_ERROR_PATTERN = /agent limit reached|do not have any persistent agents available/i

type CreateAgentErrorState = {
  message: string
  showUpgradeCta: boolean
}

function extractFirstErrorMessage(payload: unknown): string | null {
  const queue: unknown[] = [payload]
  const seen = new Set<object>()

  while (queue.length > 0) {
    const current = queue.shift()
    if (typeof current === 'string') {
      const trimmed = current.trim()
      if (trimmed) {
        return trimmed
      }
      continue
    }

    if (Array.isArray(current)) {
      for (const entry of current) {
        queue.push(entry)
      }
      continue
    }

    if (!current || typeof current !== 'object') {
      continue
    }

    if (seen.has(current)) {
      continue
    }
    seen.add(current)

    const record = current as Record<string, unknown>
    const priorityKeys = ['error', 'detail', 'message', 'non_field_errors', '__all__']
    for (const key of priorityKeys) {
      if (key in record) {
        queue.unshift(record[key])
      }
    }

    for (const value of Object.values(record)) {
      queue.push(value)
    }
  }

  return null
}

function buildCreateAgentError(err: unknown, isProprietaryMode: boolean): CreateAgentErrorState {
  const fallback = 'Unable to create that agent right now.'
  let message: string | null = null

  if (err instanceof HttpError) {
    message = extractFirstErrorMessage(err.body)
  } else if (err instanceof Error) {
    const trimmed = err.message.trim()
    message = trimmed || null
  }

  const resolved = message ?? fallback
  if (AGENT_LIMIT_ERROR_PATTERN.test(resolved)) {
    if (isProprietaryMode) {
      return {
        message: "You've reached your agent limit for your current plan. Upgrade to create more agents.",
        showUpgradeCta: true,
      }
    }
    return {
      message: "You've reached your agent limit for this deployment. Adjust deployment settings to allow more agents.",
      showUpgradeCta: false,
    }
  }
  return {
    message: resolved,
    showUpgradeCta: false,
  }
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

function prunePendingAvatarTracking(
  pending: PendingAvatarTracking,
  rosterAgents: AgentRosterEntry[],
  nowMs = Date.now(),
): PendingAvatarTracking {
  const pendingEntries = Object.entries(pending)
  if (pendingEntries.length === 0) {
    return pending
  }

  const rosterById = new Map<string, AgentRosterEntry>()
  for (const agent of rosterAgents) {
    rosterById.set(agent.id, agent)
  }

  let changed = false
  const next: PendingAvatarTracking = {}

  for (const [agentId, expiresAt] of pendingEntries) {
    if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) {
      changed = true
      continue
    }

    const rosterEntry = rosterById.get(agentId)
    if (!rosterEntry || normalizeAvatarUrl(rosterEntry.avatarUrl)) {
      changed = true
      continue
    }

    next[agentId] = expiresAt
  }

  return changed ? next : pending
}

type AgentRosterQueryData = {
  context: ConsoleContext
  agents: AgentRosterEntry[]
  llmIntelligence?: unknown
}

function isAgentRosterQueryData(value: unknown): value is AgentRosterQueryData {
  if (!value || typeof value !== 'object') {
    return false
  }
  const data = value as { context?: unknown; agents?: unknown }
  if (!Array.isArray(data.agents)) {
    return false
  }
  if (!data.context || typeof data.context !== 'object') {
    return false
  }
  const context = data.context as { type?: unknown; id?: unknown }
  return typeof context.type === 'string' && typeof context.id === 'string'
}

function sameConsoleContext(left: ConsoleContext | null | undefined, right: ConsoleContext | null | undefined): boolean {
  if (!left || !right) {
    return false
  }
  return left.type === right.type && left.id === right.id
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
const AUTO_SCROLL_REPIN_SUPPRESSION_MS = 1500
const BOTTOM_REPIN_THRESHOLD_PX = 50
const NEAR_BOTTOM_THRESHOLD_PX = 100
const UNPIN_DISTANCE_FROM_BOTTOM_PX = 12
const PROGRAMMATIC_SCROLL_GUARD_MS = 150

function isEditableEventTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false
  }
  return target.closest('input, textarea, select, [contenteditable="true"]') !== null
}

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
  const [activeAgentId, setActiveAgentId] = useState<string | null>(agentId ?? null)
  const {
    data: quickSettingsPayload,
    isLoading: quickSettingsLoading,
    error: quickSettingsError,
    refetch: refetchQuickSettings,
    updateQuickSettings,
    updating: quickSettingsUpdating,
  } = useAgentQuickSettings(activeAgentId)
  const {
    data: addonsPayload,
    refetch: refetchAddons,
    updateAddons,
    updating: addonsUpdating,
  } = useAgentAddons(activeAgentId)
  const queryClient = useQueryClient()
  const {
    currentPlan,
    isProprietaryMode,
    ensureAuthenticated,
    upgradeModalSource,
    openUpgradeModal,
  } = useSubscriptionStore()
  const isNewAgent = agentId === null
  const isSelectionView = agentId === undefined
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const pendingCreateRef = useRef<{ body: string; attachments: File[]; tier: IntelligenceTierKey; charterOverride?: string | null } | null>(null)
  const [intelligenceGate, setIntelligenceGate] = useState<IntelligenceGateState | null>(null)
  const [resolvedContext, setResolvedContext] = useState<ConsoleContext | null>(null)

  const handleContextSwitched = useCallback(
    (context: ConsoleContext) => {
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'] })
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
  const nextScheduledAt = useAgentChatStore((state) => state.nextScheduledAt)
  const streaming = useAgentChatStore((state) => state.streaming)
  const streamingLastUpdatedAt = useAgentChatStore((state) => state.streamingLastUpdatedAt)
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
  const suppressAutoScrollPin = useAgentChatStore((state) => state.suppressAutoScrollPin)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const isStoreSynced = storeAgentId === activeAgentId
  const timelineEvents = !isNewAgent && isStoreSynced ? events : []
  const timelineHasMoreOlder = !isNewAgent && isStoreSynced ? hasMoreOlder : false
  const timelineHasMoreNewer = !isNewAgent && isStoreSynced ? hasMoreNewer : false
  const timelineHasUnseenActivity = !isNewAgent && isStoreSynced ? hasUnseenActivity : false
  const timelineProcessingActive = !isNewAgent && isStoreSynced ? processingActive : false
  const timelineProcessingStartedAt = !isNewAgent && isStoreSynced ? processingStartedAt : null
  const timelineAwaitingResponse = !isNewAgent && isStoreSynced ? awaitingResponse : false
  const timelineProcessingWebTasks = !isNewAgent && isStoreSynced ? processingWebTasks : []
  const timelineNextScheduledAt = !isNewAgent && isStoreSynced ? nextScheduledAt : null
  const timelineStreaming = !isNewAgent && isStoreSynced ? streaming : null
  const timelineLoadingOlder = !isNewAgent && isStoreSynced ? loadingOlder : false
  const timelineLoadingNewer = !isNewAgent && isStoreSynced ? loadingNewer : false
  const initialLoading = !isNewAgent && (!isStoreSynced || (loading && timelineEvents.length === 0))

  const [isNearBottom, setIsNearBottom] = useState(true)
  const [collaboratorInviteOpen, setCollaboratorInviteOpen] = useState(false)
  const [pendingAvatarTracking, setPendingAvatarTracking] = useState<PendingAvatarTracking>({})
  const trackPendingAvatarRefresh = useCallback((agentId: string) => {
    const expiresAt = Date.now() + ROSTER_PENDING_AVATAR_TRACK_WINDOW_MS
    setPendingAvatarTracking((current) => {
      const currentExpiry = current[agentId]
      if (currentExpiry && currentExpiry >= expiresAt) {
        return current
      }
      return { ...current, [agentId]: expiresAt }
    })
  }, [])

  const handleCreditEvent = useCallback(() => {
    void refetchQuickSettings()
    void queryClient.invalidateQueries({ queryKey: ['usage-summary', 'agent-chat'], exact: false })
  }, [refetchQuickSettings, queryClient])
  const handleAgentProfileEvent = useCallback(
    (rawPayload: Record<string, unknown>) => {
      const agentIdFromEvent = typeof rawPayload.agent_id === 'string' ? rawPayload.agent_id : null
      if (!agentIdFromEvent) {
        return
      }

      const hasName = Object.prototype.hasOwnProperty.call(rawPayload, 'agent_name')
      const hasColor = Object.prototype.hasOwnProperty.call(rawPayload, 'agent_color_hex')
      const hasAvatar = Object.prototype.hasOwnProperty.call(rawPayload, 'agent_avatar_url')
      const hasShortDescription = Object.prototype.hasOwnProperty.call(rawPayload, 'short_description')
      const hasMiniDescription = Object.prototype.hasOwnProperty.call(rawPayload, 'mini_description')
      if (!hasName && !hasColor && !hasAvatar && !hasShortDescription && !hasMiniDescription) {
        return
      }
      if (hasAvatar) {
        const avatarFromEvent = typeof rawPayload.agent_avatar_url === 'string' ? rawPayload.agent_avatar_url : null
        if (normalizeAvatarUrl(avatarFromEvent)) {
          setPendingAvatarTracking((current) => {
            if (!current[agentIdFromEvent]) {
              return current
            }
            const next = { ...current }
            delete next[agentIdFromEvent]
            return next
          })
        }
      }

      queryClient.setQueriesData(
        { queryKey: ['agent-roster'] },
        (
          current:
            | { context: ConsoleContext; agents: AgentRosterEntry[]; llmIntelligence?: unknown }
            | undefined,
        ) => {
          if (!current?.agents?.length) {
            return current
          }

          let changed = false
          const nextAgents = current.agents.map((agent) => {
            if (agent.id !== agentIdFromEvent) {
              return agent
            }

            const next = { ...agent }
            if (hasName) {
              const nextName = typeof rawPayload.agent_name === 'string' ? rawPayload.agent_name : null
              if (nextName && nextName !== next.name) {
                next.name = nextName
                changed = true
              }
            }
            if (hasColor) {
              const nextColor = typeof rawPayload.agent_color_hex === 'string' ? rawPayload.agent_color_hex : null
              if (nextColor !== next.displayColorHex) {
                next.displayColorHex = nextColor
                changed = true
              }
            }
            if (hasAvatar) {
              const nextAvatar = typeof rawPayload.agent_avatar_url === 'string' ? rawPayload.agent_avatar_url : null
              if (nextAvatar !== next.avatarUrl) {
                next.avatarUrl = nextAvatar
                changed = true
              }
            }
            if (hasShortDescription) {
              const nextDescription = typeof rawPayload.short_description === 'string' ? rawPayload.short_description : ''
              if (nextDescription !== next.shortDescription) {
                next.shortDescription = nextDescription
                changed = true
              }
            }
            if (hasMiniDescription) {
              const nextMiniDescription = typeof rawPayload.mini_description === 'string' ? rawPayload.mini_description : ''
              if (nextMiniDescription !== next.miniDescription) {
                next.miniDescription = nextMiniDescription
                changed = true
              }
            }

            return next
          })

          if (!changed) {
            return current
          }

          return {
            ...current,
            agents: hasName ? [...nextAgents].sort((left, right) => compareRosterNames(left.name, right.name)) : nextAgents,
          }
        },
      )
    },
    [queryClient],
  )
  const socketSnapshot = useAgentChatSocket(liveAgentId, {
    onCreditEvent: handleCreditEvent,
    onAgentProfileEvent: handleAgentProfileEvent,
  })
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(liveAgentId)
  const rosterContextKey = effectiveContext ? `${effectiveContext.type}:${effectiveContext.id}` : 'unknown'
  const rosterQueryAgentId = resolvedContext ? undefined : (agentId ?? undefined)
  const hasPendingAvatarTracking = Object.keys(pendingAvatarTracking).length > 0
  const rosterRefreshIntervalMs = hasPendingAvatarTracking
    ? ROSTER_PENDING_AVATAR_REFRESH_INTERVAL_MS
    : ROSTER_REFRESH_INTERVAL_MS
  const rosterQuery = useAgentRoster({
    enabled: true,
    contextKey: rosterContextKey,
    forAgentId: rosterQueryAgentId,
    refetchIntervalMs: rosterRefreshIntervalMs,
  })

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const expiryValues = Object.values(pendingAvatarTracking)
    if (expiryValues.length === 0) {
      return
    }
    const nextExpiry = Math.min(...expiryValues)
    const delayMs = Math.max(0, nextExpiry - Date.now()) + 25
    const timeout = window.setTimeout(() => {
      setPendingAvatarTracking((current) => {
        return prunePendingAvatarTracking(current, rosterQuery.data?.agents ?? [], Date.now())
      })
    }, delayMs)
    return () => window.clearTimeout(timeout)
  }, [pendingAvatarTracking, rosterQuery.data?.agents])

  useEffect(() => {
    if (!rosterQuery.data?.agents) {
      return
    }
    setPendingAvatarTracking((current) => prunePendingAvatarTracking(current, rosterQuery.data.agents))
  }, [rosterQuery.data?.agents])

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
  const lastProgrammaticScrollAtRef = useRef(0)
  const forceScrollOnNextUpdateRef = useRef(false)
  const didInitialScrollRef = useRef(false)
  const isNearBottomRef = useRef(isNearBottom)
  // Sync ref during render so ResizeObservers see updated value immediately
  isNearBottomRef.current = isNearBottom
  const autoRepinTimeoutRef = useRef<number | null>(null)
  const composerFocusNudgeTimeoutRef = useRef<number | null>(null)
  const userTouchActiveRef = useRef(false)
  const touchEndTimerRef = useRef<number | null>(null)

  // Track if we should scroll on next content update (captured before DOM changes)
  const shouldScrollOnNextUpdateRef = useRef(autoScrollPinned)

  const syncNearBottomState = useCallback((container: HTMLElement | null): number | null => {
    if (!container) {
      return null
    }
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    const nearBottom = distanceFromBottom <= NEAR_BOTTOM_THRESHOLD_PX
    isNearBottomRef.current = nearBottom
    setIsNearBottom((previous) => (previous === nearBottom ? previous : nearBottom))
    return distanceFromBottom
  }, [])

  const repinAutoScrollIfAtBottom = useCallback((container: HTMLElement | null) => {
    if (!container || autoScrollPinnedRef.current) {
      return
    }
    // Respect the suppression window after intentional user scroll-up
    if (autoScrollPinSuppressedUntilRef.current && Date.now() < autoScrollPinSuppressedUntilRef.current) {
      return
    }
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    if (distanceFromBottom > BOTTOM_REPIN_THRESHOLD_PX) {
      return
    }
    autoScrollPinnedRef.current = true
    setAutoScrollPinned(true)
  }, [setAutoScrollPinned])

  const unpinAutoScrollFromUserGesture = useCallback(() => {
    if (!autoScrollPinnedRef.current) {
      return
    }
    // Cancel any pending forced follow when the user starts reading older content.
    shouldScrollOnNextUpdateRef.current = false
    forceScrollOnNextUpdateRef.current = false
    autoScrollPinnedRef.current = false
    autoScrollPinSuppressedUntilRef.current = Date.now() + AUTO_SCROLL_REPIN_SUPPRESSION_MS
    setAutoScrollPinned(false)
    suppressAutoScrollPin(AUTO_SCROLL_REPIN_SUPPRESSION_MS)
    if (autoRepinTimeoutRef.current !== null) {
      window.clearTimeout(autoRepinTimeoutRef.current)
    }
    autoRepinTimeoutRef.current = window.setTimeout(() => {
      autoRepinTimeoutRef.current = null
      repinAutoScrollIfAtBottom(document.getElementById('timeline-shell'))
    }, AUTO_SCROLL_REPIN_SUPPRESSION_MS + 16)
  }, [repinAutoScrollIfAtBottom, setAutoScrollPinned, suppressAutoScrollPin])

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
  const hasSentinel = !initialLoading && !timelineHasMoreNewer
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

    const observer = new IntersectionObserver(
      ([entry]) => {
        const distanceFromBottom = syncNearBottomState(container)
        const atBottom = entry.isIntersecting
          && typeof distanceFromBottom === 'number'
          && distanceFromBottom <= BOTTOM_REPIN_THRESHOLD_PX

        // Auto-restick only when the user is truly at the bottom.
        if (atBottom) {
          repinAutoScrollIfAtBottom(container)
        }
      },
      {
        // Use container as root for container scrolling
        root: container,
        // Extend bottom edge so sentinel is "visible" before fully in view.
        rootMargin: '0px 0px 50px 0px',
        threshold: 0,
      },
    )

    syncNearBottomState(container)
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasSentinel, repinAutoScrollIfAtBottom, syncNearBottomState])

  // Detect user scrolling UP to immediately unpin (wheel, touch, keyboard, scrollbar drag)
  useEffect(() => {
    const container = document.getElementById('timeline-shell')
    if (!container) return

    let lastScrollTop = container.scrollTop

    // Catch scrollbar drags / momentum scrolling where wheel events may not fire consistently.
    const handleScroll = () => {
      const nextScrollTop = container.scrollTop
      const distanceFromBottom = syncNearBottomState(container)
      // Don't try to re-pin while user is actively touching — let their scroll intent take priority
      if (!userTouchActiveRef.current) {
        repinAutoScrollIfAtBottom(container)
      }
      const movedAwayFromBottom = typeof distanceFromBottom === 'number'
        && distanceFromBottom > UNPIN_DISTANCE_FROM_BOTTOM_PX
      // Guard against false unpins from programmatic scrolls (scrollIntoView can cause transient scrollTop decreases).
      // Bypass the guard when the user is actively touching — touch scroll is always user-initiated.
      if (
        movedAwayFromBottom
        && nextScrollTop < lastScrollTop - 2
        && (userTouchActiveRef.current || Date.now() - lastProgrammaticScrollAtRef.current > PROGRAMMATIC_SCROLL_GUARD_MS)
      ) {
        unpinAutoScrollFromUserGesture()
      }
      lastScrollTop = nextScrollTop
    }

    // Detect scroll-up via wheel
    const handleWheel = (e: WheelEvent) => {
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      if (e.deltaY < 0 && distanceFromBottom > UNPIN_DISTANCE_FROM_BOTTOM_PX) {
        unpinAutoScrollFromUserGesture()
      }
    }

    // Track touch lifecycle to suppress programmatic scrolls while user is touching
    let touchStartY: number | null = null
    const handleTouchStart = (e: TouchEvent) => {
      if (touchEndTimerRef.current !== null) {
        window.clearTimeout(touchEndTimerRef.current)
        touchEndTimerRef.current = null
      }
      userTouchActiveRef.current = true
      touchStartY = e.touches[0]?.clientY ?? null
    }
    // Detect upward swipe directly via touch movement — more reliable than waiting
    // for scroll events on mobile, which can be blocked by the programmatic scroll guard.
    const handleTouchMove = (e: TouchEvent) => {
      if (!autoScrollPinnedRef.current || touchStartY === null) return
      const currentY = e.touches[0]?.clientY
      if (currentY === undefined) return
      // Finger moving down on screen = scrolling up (revealing older content)
      if (currentY - touchStartY > 10) {
        const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
        if (distanceFromBottom > UNPIN_DISTANCE_FROM_BOTTOM_PX) {
          unpinAutoScrollFromUserGesture()
        }
      }
    }
    const handleTouchEnd = () => {
      touchStartY = null
      if (touchEndTimerRef.current !== null) {
        window.clearTimeout(touchEndTimerRef.current)
      }
      touchEndTimerRef.current = window.setTimeout(() => {
        userTouchActiveRef.current = false
        touchEndTimerRef.current = null
      }, 200)
    }

    // Detect scroll-up via keyboard
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!autoScrollPinnedRef.current) return
      if (isEditableEventTarget(e.target)) return
      const scrollUpKeys = ['ArrowUp', 'PageUp', 'Home']
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      if (scrollUpKeys.includes(e.key) && distanceFromBottom > UNPIN_DISTANCE_FROM_BOTTOM_PX) {
        unpinAutoScrollFromUserGesture()
      }
    }

    // Listen on the container, not window
    syncNearBottomState(container)
    container.addEventListener('scroll', handleScroll, { passive: true })
    container.addEventListener('wheel', handleWheel, { passive: true })
    container.addEventListener('touchstart', handleTouchStart, { passive: true })
    container.addEventListener('touchmove', handleTouchMove, { passive: true })
    container.addEventListener('touchend', handleTouchEnd, { passive: true })
    container.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    window.addEventListener('keydown', handleKeyDown) // Keyboard stays on window

    return () => {
      container.removeEventListener('scroll', handleScroll)
      container.removeEventListener('wheel', handleWheel)
      container.removeEventListener('touchstart', handleTouchStart)
      container.removeEventListener('touchmove', handleTouchMove)
      container.removeEventListener('touchend', handleTouchEnd)
      container.removeEventListener('touchcancel', handleTouchEnd)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [repinAutoScrollIfAtBottom, syncNearBottomState, unpinAutoScrollFromUserGesture])

  // Unpin auto-scroll when processing ends so user's reading position is preserved
  const prevProcessingRef = useRef(timelineProcessingActive)
  useEffect(() => {
    const wasProcessing = prevProcessingRef.current
    prevProcessingRef.current = timelineProcessingActive
    if (wasProcessing && !timelineProcessingActive && !isNearBottomRef.current && !autoScrollPinnedRef.current) {
      setAutoScrollPinned(false)
    }
  }, [timelineProcessingActive, setAutoScrollPinned])

  // Capture scroll decision BEFORE content changes to avoid race with scroll handler
  const prevEventsRef = useRef(timelineEvents)
  const prevStreamingRef = useRef(timelineStreaming)

  if (timelineEvents !== prevEventsRef.current || timelineStreaming !== prevStreamingRef.current) {
    shouldScrollOnNextUpdateRef.current = autoScrollPinned
    prevEventsRef.current = timelineEvents
    prevStreamingRef.current = timelineStreaming
  }

  const pendingScrollFrameRef = useRef<number | null>(null)

  const jumpToBottom = useCallback(() => {
    // Container scrolling: scroll the timeline-shell, not the window
    const container = document.getElementById('timeline-shell')
    const sentinel = document.getElementById('timeline-bottom-sentinel')
    if (!container) return
    lastProgrammaticScrollAtRef.current = Date.now()
    // Kill iOS momentum scrolling — toggling overflow forces the scroll to stop immediately
    container.style.overflowY = 'hidden'
    if (sentinel) {
      // scrollIntoView is more reliable across browsers
      sentinel.scrollIntoView({ block: 'end', behavior: 'auto' })
    } else {
      container.scrollTop = container.scrollHeight + 10000
    }
    // Restore on next frame so the container remains scrollable
    requestAnimationFrame(() => { container.style.overflowY = '' })
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
        if (delta > 0 && autoScrollPinnedRef.current && !userTouchActiveRef.current) {
          container.scrollTop += delta
        }
      }

      prevComposerHeight.current = height

      // If pinned, ensure we stay at the bottom
      if (autoScrollPinnedRef.current && !userTouchActiveRef.current) {
        jumpToBottom()
      }
      syncNearBottomState(container)
      // IntersectionObserver handles isNearBottom updates automatically
    })

    observer.observe(composer)
    return () => observer.disconnect()
  }, [jumpToBottom, syncNearBottomState])

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
    setTimelineNode(node)
  }, [])

  // Observe timeline changes (e.g. images loading, new DOM elements) to keep pinned to bottom
  useEffect(() => {
    if (!timelineNode) return
    const inner = document.getElementById('timeline-events')

    const observer = new ResizeObserver(() => {
      // If pinned, ensure we stay at the bottom when content changes
      // Skip while user is actively touching to prevent scroll fighting on mobile
      if (autoScrollPinnedRef.current && !userTouchActiveRef.current) {
        jumpToBottom()
      }
      syncNearBottomState(timelineNode)
    })

    observer.observe(timelineNode)
    if (inner) {
      observer.observe(inner)
    }
    return () => observer.disconnect()
  }, [timelineNode, jumpToBottom, syncNearBottomState])

  useEffect(() => () => {
    if (pendingScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingScrollFrameRef.current)
    }
  }, [])

  useEffect(() => () => {
    if (composerFocusNudgeTimeoutRef.current !== null) {
      window.clearTimeout(composerFocusNudgeTimeoutRef.current)
    }
    if (touchEndTimerRef.current !== null) {
      window.clearTimeout(touchEndTimerRef.current)
    }
  }, [])

  useEffect(() => {
    if (isNewAgent) {
      // New agent: no events yet, but ensure auto-scroll is pinned for when content arrives
      didInitialScrollRef.current = true
      setAutoScrollPinned(true)
      return
    }
    if (!initialLoading && timelineEvents.length && !didInitialScrollRef.current) {
      didInitialScrollRef.current = true
      setAutoScrollPinned(true)
      // Immediate scroll attempt
      jumpToBottom()
      // Plus delayed scroll to catch any async layout (images, fonts, etc)
      const timeout = setTimeout(() => jumpToBottom(), 50)
      return () => clearTimeout(timeout)
    }
  }, [timelineEvents.length, initialLoading, isNewAgent, jumpToBottom, setAutoScrollPinned])

  useLayoutEffect(() => {
    if (forceScrollOnNextUpdateRef.current) {
      // Force scroll (user-initiated actions like send, jump-to-latest) — always honor
      forceScrollOnNextUpdateRef.current = false
      shouldScrollOnNextUpdateRef.current = false
      jumpToBottom()
    } else if (shouldScrollOnNextUpdateRef.current) {
      // Auto scroll (new content while pinned) — skip while user is touching
      shouldScrollOnNextUpdateRef.current = false
      if (!userTouchActiveRef.current) {
        jumpToBottom()
      }
    }
    // IntersectionObserver handles isNearBottom updates automatically
  }, [
    jumpToBottom,
    timelineEvents,
    timelineStreaming,
    timelineLoadingOlder,
    timelineLoadingNewer,
    timelineHasMoreNewer,
    timelineHasUnseenActivity,
    initialLoading,
    timelineProcessingActive,
    timelineAwaitingResponse,
  ])

  const rosterAgents = useMemo(
    () => (contextReady ? rosterQuery.data?.agents ?? [] : []),
    [contextReady, rosterQuery.data?.agents],
  )
  const activeRosterMeta = useMemo(
    () => rosterAgents.find((agent) => agent.id === activeAgentId) ?? null,
    [activeAgentId, rosterAgents],
  )
  const storeAgentName = isStoreSynced ? storedAgentName : null
  const storeResolvedAvatarUrl = isStoreSynced ? storedAgentAvatarUrl : null
  const storeAgentColor = isStoreSynced ? agentColorHex : null
  const resolvedAgentName = storeAgentName ?? activeRosterMeta?.name ?? agentName ?? null
  const resolvedAvatarUrl = storeResolvedAvatarUrl ?? activeRosterMeta?.avatarUrl ?? agentAvatarUrl ?? null
  const resolvedAgentColorHex = storeAgentColor ?? activeRosterMeta?.displayColorHex ?? agentColor ?? null
  const pendingAgentEmail = activeAgentId ? pendingAgentEmails[activeAgentId] ?? null : null
  const resolvedAgentEmail = activeRosterMeta?.email ?? pendingAgentEmail ?? agentEmail ?? null
  const resolvedAgentSms = activeRosterMeta?.sms ?? agentSms ?? null
  const resolvedIsOrgOwned = activeRosterMeta?.isOrgOwned ?? false
  const activeIsCollaborator = activeRosterMeta?.isCollaborator ?? (isCollaborator ?? false)
  const activeCanManageAgent = activeRosterMeta?.canManageAgent ?? !activeIsCollaborator
  const activeCanManageCollaborators = activeRosterMeta?.canManageCollaborators ?? (canManageCollaborators ?? true)
  const hasAgentReply = useMemo(() => hasAgentResponse(timelineEvents), [timelineEvents])
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
  const [createAgentError, setCreateAgentError] = useState<CreateAgentErrorState | null>(null)
  const [spawnIntent, setSpawnIntent] = useState<AgentSpawnIntent | null>(null)
  const [spawnIntentStatus, setSpawnIntentStatus] = useState<SpawnIntentStatus>('idle')
  const spawnIntentAutoSubmittedRef = useRef(false)
  const spawnIntentRequestIdRef = useRef(0)
  const agentFirstName = useMemo(() => deriveFirstName(resolvedAgentName), [resolvedAgentName])
  const latestKanbanSnapshot = useMemo(() => getLatestKanbanSnapshot(timelineEvents), [timelineEvents])
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
    setCreateAgentError(null)
  }, [isNewAgent, activeAgentId])

  useEffect(() => {
    if (!isNewAgent) {
      return
    }
    if (!llmIntelligence?.systemDefaultTier) {
      return
    }
    // Only auto-apply the system default when the draft is still at the initial value,
    // so we don't overwrite a user selection.
    if (draftIntelligenceTier !== 'standard') {
      return
    }
    const systemDefault = llmIntelligence.systemDefaultTier
    const isKnownTier = llmIntelligence.options.some((option) => option.key === systemDefault)
    const resolvedTier = isKnownTier ? systemDefault : 'standard'
    if (resolvedTier !== draftIntelligenceTier) {
      setDraftIntelligenceTier(resolvedTier)
    }
  }, [draftIntelligenceTier, isNewAgent, llmIntelligence?.options, llmIntelligence?.systemDefaultTier])

  const spawnFlow = useMemo(() => {
    if (!isNewAgent || typeof window === 'undefined') {
      return false
    }
    const params = new URLSearchParams(window.location.search)
    const flag = (params.get('spawn') || '').toLowerCase()
    return flag === '1' || flag === 'true' || flag === 'yes' || flag === 'on'
  }, [isNewAgent])
  const onboardingTarget = spawnIntent?.onboarding_target ?? null
  const requiresTrialPlanSelection = Boolean(spawnIntent?.requires_plan_selection)

  useEffect(() => {
    if (!isNewAgent || !spawnFlow) {
      spawnIntentAutoSubmittedRef.current = false
      spawnIntentRequestIdRef.current = 0
      setSpawnIntent(null)
      setSpawnIntentStatus('idle')
      return
    }
    spawnIntentRequestIdRef.current += 1
    const requestId = spawnIntentRequestIdRef.current
    const controller = new AbortController()
    setSpawnIntentStatus('loading')
    const loadSpawnIntent = async () => {
      try {
        const intent = await fetchAgentSpawnIntent(controller.signal)
        if (spawnIntentRequestIdRef.current !== requestId) {
          return
        }
        setSpawnIntent(intent)
        const charter = intent?.charter?.trim()
        if (intent?.requires_plan_selection || charter) {
          setSpawnIntentStatus('ready')
          return
        }
        if (!charter) {
          setSpawnIntentStatus('done')
          return
        }
      } catch (err) {
        if (controller.signal.aborted || spawnIntentRequestIdRef.current !== requestId) {
          return
        }
        setSpawnIntentStatus('done')
      }
    }
    void loadSpawnIntent()
    return () => {
      controller.abort()
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
      miniDescription: '',
      shortDescription: '',
      isOrgOwned: false,
    }
  }, [activeAgentId, resolvedAgentColorHex, resolvedAgentName, resolvedAvatarUrl])
  const rosterAgentsWithActiveMeta = useMemo(() => {
    if (!activeAgentId) {
      return rosterAgents
    }

    let changed = false
    const nextAgents = rosterAgents.map((agent) => {
      if (agent.id !== activeAgentId) {
        return agent
      }

      const nextName = resolvedAgentName || agent.name || 'Agent'
      const nextAvatarUrl = normalizeAvatarUrl(resolvedAvatarUrl) ?? normalizeAvatarUrl(agent.avatarUrl)
      const nextColor = resolvedAgentColorHex ?? agent.displayColorHex ?? null
      const nextEmail = resolvedAgentEmail ?? agent.email ?? null
      const nextSms = resolvedAgentSms ?? agent.sms ?? null
      const nextIsOrgOwned = agent.isOrgOwned ?? resolvedIsOrgOwned

      if (
        nextName === agent.name
        && nextAvatarUrl === agent.avatarUrl
        && nextColor === agent.displayColorHex
        && nextEmail === (agent.email ?? null)
        && nextSms === (agent.sms ?? null)
        && nextIsOrgOwned === agent.isOrgOwned
      ) {
        return agent
      }

      changed = true
      return {
        ...agent,
        name: nextName,
        avatarUrl: nextAvatarUrl,
        displayColorHex: nextColor,
        email: nextEmail,
        sms: nextSms,
        isOrgOwned: nextIsOrgOwned,
      }
    })

    return changed ? nextAgents : rosterAgents
  }, [
    activeAgentId,
    resolvedAgentColorHex,
    resolvedAgentEmail,
    resolvedAgentName,
    resolvedAgentSms,
    resolvedAvatarUrl,
    resolvedIsOrgOwned,
    rosterAgents,
  ])
  const sidebarAgents = useMemo(() => {
    if (!contextReady) {
      return []
    }
    if (!activeAgentId) {
      return rosterAgentsWithActiveMeta
    }
    const hasActive = rosterAgentsWithActiveMeta.some((agent) => agent.id === activeAgentId)
    if (hasActive || !fallbackAgent) {
      return rosterAgentsWithActiveMeta
    }
    return [fallbackAgent, ...rosterAgentsWithActiveMeta]
  }, [activeAgentId, contextReady, fallbackAgent, rosterAgentsWithActiveMeta])

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
    if (!agentInRoster && !loading && timelineEvents.length === 0) return true
    return false
  }, [contextReady, isNewAgent, rosterQuery.isLoading, initialLoading, rosterAgents, activeAgentId, error, loading, timelineEvents.length])

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
      if (window.location.pathname.startsWith('/app')) {
        window.dispatchEvent(new PopStateEvent('popstate'))
      }
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
    const checkoutPath = plan === 'startup' ? '/subscribe/startup/' : '/subscribe/scale/'
    let returnToPath: string | undefined
    if (requiresTrialPlanSelection) {
      returnToPath = onboardingTarget === 'api_keys'
        ? '/console/api-keys/'
        : '/console/agents/create/quick/'
    }
    const checkoutUrl = appendReturnTo(checkoutPath, returnToPath)
    window.open(checkoutUrl, '_top')
  }, [ensureAuthenticated, onboardingTarget, requiresTrialPlanSelection, upgradeModalSource])

  const createNewAgent = useCallback(
    async (body: string, tier: IntelligenceTierKey, charterOverride?: string | null) => {
      setCreateAgentError(null)
      try {
        const result = await createAgent(body, tier, charterOverride)
        const createdAgentName = result.agent_name?.trim() || 'Agent'
        const createdAgentEmail = result.agent_email?.trim() || null
        const createdAgentEntry: AgentRosterEntry = {
          id: result.agent_id,
          name: createdAgentName,
          avatarUrl: null,
          displayColorHex: null,
          isActive: true,
          miniDescription: '',
          shortDescription: '',
          email: createdAgentEmail,
        }
        pendingAgentMetaRef.current = {
          agentId: result.agent_id,
          agentName: createdAgentName,
        }
        trackPendingAvatarRefresh(result.agent_id)
        if (createdAgentEmail) {
          setPendingAgentEmails((current) => ({ ...current, [result.agent_id]: createdAgentEmail }))
        }
        queryClient.setQueriesData<AgentRosterQueryData>(
          { queryKey: ['agent-roster'] },
          (current) => {
            if (!isAgentRosterQueryData(current)) {
              return current
            }
            if (effectiveContext && !sameConsoleContext(current.context, effectiveContext)) {
              return current
            }
            const nextAgents = mergeRosterEntry(current.agents, createdAgentEntry)
            if (nextAgents === current.agents) {
              return current
            }
            return {
              ...current,
              agents: nextAgents,
            }
          },
        )
        void queryClient.invalidateQueries({ queryKey: ['agent-roster'] })
        onAgentCreated?.(result.agent_id)
      } catch (err) {
        const errorState = buildCreateAgentError(err, isProprietaryMode)
        setCreateAgentError(errorState)
        console.error('Failed to create agent:', err)
      }
    },
    [effectiveContext, isProprietaryMode, onAgentCreated, queryClient, setPendingAgentEmails, trackPendingAvatarRefresh],
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
        void refetchQuickSettings()
        void refreshProcessing()
        return true
      } catch (err) {
        setIntelligenceOverrides((current) => ({ ...current, [activeAgentId]: previousTier }))
        setIntelligenceError('Unable to update intelligence level.')
        return false
      } finally {
        setIntelligenceBusy(false)
      }
    },
    [activeAgentId, isNewAgent, queryClient, refreshProcessing, refetchQuickSettings, resolvedIntelligenceTier],
  )

  // Start/stop insight rotation based on processing state
  const isProcessing = allowAgentRefresh && (timelineProcessingActive || timelineAwaitingResponse || (timelineStreaming && !timelineStreaming.done))
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
    if (!allowAgentRefresh || !timelineStreaming || timelineStreaming.done) {
      return () => undefined
    }
    const interval = window.setInterval(() => {
      void refreshProcessing()
    }, STREAMING_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [allowAgentRefresh, refreshProcessing, timelineStreaming])

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
    if (!allowAgentRefresh || !timelineStreaming || timelineStreaming.done) {
      return () => undefined
    }
    if (timelineProcessingActive) {
      return () => undefined
    }
    const lastUpdated = streamingLastUpdatedAt ?? Date.now()
    const elapsed = Date.now() - lastUpdated
    const timeoutMs = Math.max(0, STREAMING_STALE_MS - elapsed)
    const handleTimeout = () => {
      finalizeStreaming()
      if (timelineStreaming.reasoning && !timelineStreaming.content) {
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
    timelineProcessingActive,
    refreshLatest,
    timelineStreaming,
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

  const canManageDailyCredits = Boolean(activeAgentId && !isNewAgent)
  const dailyCreditsInfo = canManageDailyCredits ? quickSettingsPayload?.settings?.dailyCredits ?? null : null
  const dailyCreditsStatus = canManageDailyCredits ? quickSettingsPayload?.status?.dailyCredits ?? null : null
  const contactCap = addonsPayload?.contactCap ?? null
  const contactCapStatus = addonsPayload?.status?.contactCap ?? null
  const contactPackOptions = addonsPayload?.contactPacks?.options ?? []
  const contactPackCanManageBilling = Boolean(addonsPayload?.contactPacks?.canManageBilling)
  const taskPackOptions = addonsPayload?.taskPacks?.options ?? []
  const taskPackCanManageBilling = Boolean(addonsPayload?.taskPacks?.canManageBilling)
  const addonsTrial = addonsPayload?.trial ?? null
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
      void refreshProcessing()
    },
    [refreshProcessing, updateQuickSettings],
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
    void createNewAgent(pending.body, tierToUse, pending.charterOverride)
  }, [buildGateAnalytics, closeGate, createNewAgent, intelligenceGate])

  const handleSend = useCallback(async (body: string, attachments: File[] = [], charterOverride?: string | null) => {
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
      void isLocked
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
      // Option 1: never block/coerce by plan tier before the agent exists.
      // The backend clamps the tier at persistence time and at runtime routing.
      if (lowCredits) {
        track(AnalyticsEvent.INTELLIGENCE_GATE_SHOWN, {
          reason: 'credits',
          selectedTier,
          allowedTier,
          multiplier: Number.isFinite(multiplier) ? multiplier : null,
          estimatedDaysRemaining,
          burnRatePerDay,
          currentPlan,
        })
        pendingCreateRef.current = { body, attachments, tier: selectedTier, charterOverride }
        setIntelligenceGate({
          reason: 'credits',
          selectedTier,
          allowedTier,
          multiplier: Number.isFinite(multiplier) ? multiplier : null,
          estimatedDaysRemaining,
          burnRatePerDay,
        })
        return
      }
      await createNewAgent(body, selectedTier, charterOverride)
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
    if (!isNewAgent || !spawnFlow || !requiresTrialPlanSelection) {
      return
    }
    openUpgradeModal('trial_onboarding', { dismissible: false })
  }, [isNewAgent, openUpgradeModal, requiresTrialPlanSelection, spawnFlow])

  useEffect(() => {
    if (!isNewAgent || !spawnFlow || spawnIntentStatus === 'loading') {
      return
    }
    if (!spawnIntent || spawnIntent.requires_plan_selection) {
      return
    }
    if (spawnIntent.onboarding_target !== 'api_keys') {
      return
    }
    window.location.assign('/console/api-keys/')
  }, [isNewAgent, spawnFlow, spawnIntent, spawnIntentStatus])

  useEffect(() => {
    if (!isNewAgent || !spawnFlow || requiresTrialPlanSelection || !spawnIntent?.charter?.trim()) {
      return
    }
    if (spawnIntent.onboarding_target === 'api_keys') {
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
    const desiredTierRaw = preferredTierRaw || llmIntelligence?.systemDefaultTier || null
    if (desiredTierRaw) {
      let resolvedTier = desiredTierRaw
      if (llmIntelligence) {
        const isKnownTier = llmIntelligence.options.some((option) => option.key === desiredTierRaw)
        resolvedTier = isKnownTier ? desiredTierRaw : 'standard'
      }
      if (resolvedTier !== draftIntelligenceTier) {
        setDraftIntelligenceTier(resolvedTier)
        return
      }
    }

    spawnIntentAutoSubmittedRef.current = true
    const sendPromise = handleSend(spawnIntent.charter, [], spawnIntent.charter_override)
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
    requiresTrialPlanSelection,
  ])

  const showSpawnIntentLoader = Boolean(
    spawnFlow && isNewAgent && (spawnIntentStatus === 'loading' || spawnIntentStatus === 'ready'),
  )

  const topLevelError = (isStoreSynced ? error : null) || (sessionStatus === 'error' ? sessionError : null)

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

  return (
    <div className="agent-chat-page" data-processing={isProcessing ? 'true' : 'false'}>
      {topLevelError ? (
        <div className="mx-auto w-full max-w-3xl px-4 py-2 text-sm text-rose-600">{topLevelError}</div>
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
        addonsTrial={addonsTrial}
        taskQuota={taskQuota}
        showTaskCreditsWarning={showTaskCreditsWarning}
        taskCreditsWarningVariant={taskCreditsWarningVariant}
        showTaskCreditsUpgrade={taskPackShowUpgrade}
        taskCreditsDismissKey={taskCreditsDismissKey}
        onRefreshAddons={refetchAddons}
        contactPackManageUrl={contactPackManageUrl}
        onShare={canShareCollaborators ? handleOpenCollaboratorInvite : undefined}
        composerError={createAgentError?.message ?? null}
        composerErrorShowUpgrade={Boolean(createAgentError?.showUpgradeCta)}
        events={timelineEvents}
        hasMoreOlder={timelineHasMoreOlder}
        hasMoreNewer={timelineHasMoreNewer}
        oldestCursor={timelineEvents.length ? timelineEvents[0].cursor : null}
        newestCursor={timelineEvents.length ? timelineEvents[timelineEvents.length - 1].cursor : null}
        processingActive={timelineProcessingActive}
        processingStartedAt={timelineProcessingStartedAt}
        awaitingResponse={timelineAwaitingResponse}
        processingWebTasks={timelineProcessingWebTasks}
        nextScheduledAt={timelineNextScheduledAt}
        streaming={timelineStreaming}
        onLoadOlder={timelineHasMoreOlder ? loadOlder : undefined}
        onLoadNewer={timelineHasMoreNewer ? loadNewer : undefined}
        onSendMessage={handleSend}
        onJumpToLatest={handleJumpToLatest}
        autoFocusComposer
        autoScrollPinned={autoScrollPinned}
        isNearBottom={isNearBottom}
        hasUnseenActivity={timelineHasUnseenActivity}
        timelineRef={captureTimelineRef}
        loadingOlder={timelineLoadingOlder}
        loadingNewer={timelineLoadingNewer}
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
