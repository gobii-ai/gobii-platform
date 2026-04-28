import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { AlertTriangle, Plus } from 'lucide-react'
import noiseLightTextureUrl from '../assets/textures/noise-light.png'

import { createAgent, updateAgent } from '../api/agents'
import {
  stopAgentProcessing,
  fulfillRequestedSecrets,
  removeRequestedSecrets,
  resolveContactRequests,
  resolveSpawnRequest,
  respondToHumanInputRequest,
  respondToHumanInputRequestsBatch,
  skipAgentPlanning,
} from '../api/agentChat'
import { fetchAgentSpawnIntent, type AgentSpawnIntent } from '../api/agentSpawnIntent'
import {
  parseNullableBooleanPreference,
  updateUserPreferences,
  parseFavoriteAgentIdsPreference,
  USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
} from '../api/userPreferences'
import type { ConsoleContext } from '../api/context'
import { fetchUsageBurnRate, fetchUsageSummary } from '../components/usage/api'
import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { AgentIntelligenceGateModal } from '../components/agentChat/AgentIntelligenceGateModal'
import { CollaboratorInviteDialog } from '../components/agentChat/CollaboratorInviteDialog'
import { ChatSidebar } from '../components/agentChat/ChatSidebar'
import { HighPriorityBanner } from '../components/agentChat/HighPriorityBanner'
import { findLatestStatusExpansionTargets } from '../components/agentChat/statusExpansion'
import type { ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { useAgentQuickSettings } from '../hooks/useAgentQuickSettings'
import { useAgentAddons } from '../hooks/useAgentAddons'
import { useAgentInsights } from '../hooks/useAgentInsights'
import { useRecentAgentSubscriptions } from '../hooks/useRecentAgentSubscriptions'
import { useAgentPanelRequestsEnabled } from '../hooks/useAgentPanelRequestsEnabled'
import { useConsoleContextSwitcher } from '../hooks/useConsoleContextSwitcher'
import { useAgentChatStore, setTimelineQueryClient } from '../stores/agentChatStore'
import { useSubscriptionStore, type PlanTier } from '../stores/subscriptionStore'
import { useAgentTimeline, flattenTimelinePages, getInitialPageResponse, timelineQueryKey, type TimelinePage } from '../hooks/useAgentTimeline'
import {
  refreshTimelineLatestInCache,
  replacePendingActionRequestsInCache,
  DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES,
} from '../hooks/useTimelineCacheInjector'
import { collapseDetailedStatusRuns } from '../hooks/useSimplifiedTimeline'
import { usePageLifecycle } from '../hooks/usePageLifecycle'
import { normalizeHexColor } from '../util/color'
import { HttpError } from '../api/http'
import { safeErrorMessage } from '../api/safeErrorMessage'
import type { AgentRosterEntry, AgentRosterSortMode, PlanningState, SignupPreviewState } from '../types/agentRoster'
import type { KanbanBoardSnapshot, PendingActionRequest, PendingHumanInputRequest, TimelineEvent } from '../types/agentChat'
import type { DailyCreditsUpdatePayload } from '../types/dailyCredits'
import type { AgentSetupMetadata } from '../types/insight'
import type { UsageBurnRateResponse, UsageSummaryResponse } from '../components/usage'
import type { IntelligenceTierKey } from '../types/llmIntelligence'
import { track, AnalyticsEvent } from '../util/analytics'
import { parseAgentRosterSortMode, sortRosterEntries } from '../util/agentRosterSort'
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
const AUDIT_URL_TEMPLATE_PLACEHOLDER = '00000000-0000-0000-0000-000000000000'
const TIMELINE_SCROLLABILITY_EPSILON_PX = 1
const SIGNUP_PREVIEW_PANEL_SOURCE = 'signup_preview_panel'
const INSIGHTS_IDLE_FETCH_DELAY_MS = 1200
const RESOLVED_NOISE_LIGHT_TEXTURE_URL = new URL(noiseLightTextureUrl, import.meta.url).toString()

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
type TrialOnboardingTarget = Exclude<AgentSpawnIntent['onboarding_target'], null>

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

function adjustHexColor(hexColor: string, ratio: number): string {
  const normalized = normalizeHexColor(hexColor)
  const parse = (sliceStart: number) => parseInt(normalized.slice(sliceStart, sliceStart + 2), 16)
  const clamp = (value: number) => Math.max(0, Math.min(255, Math.round(value)))
  const r = parse(1)
  const g = parse(3)
  const b = parse(5)
  if (ratio >= 0) {
    return `#${clamp(r + (255 - r) * ratio).toString(16).padStart(2, '0')}${clamp(g + (255 - g) * ratio).toString(16).padStart(2, '0')}${clamp(b + (255 - b) * ratio).toString(16).padStart(2, '0')}`.toUpperCase()
  }
  const factor = 1 - Math.abs(ratio)
  return `#${clamp(r * factor).toString(16).padStart(2, '0')}${clamp(g * factor).toString(16).padStart(2, '0')}${clamp(b * factor).toString(16).padStart(2, '0')}`.toUpperCase()
}

function buildFishSvgFaviconDataUrl(sourceSvg: string, colorHex: string): string {
  const accent = normalizeHexColor(colorHex)
  const light = adjustHexColor(accent, 0.1)
  const dark = adjustHexColor(accent, -0.25)
  const visor = adjustHexColor(accent, -0.55)

  const parser = new DOMParser()
  const doc = parser.parseFromString(sourceSvg, 'image/svg+xml')
  const svg = doc.querySelector('svg')
  if (!svg) {
    throw new Error('Invalid Gobii fish SVG favicon source')
  }

  // Tint the existing fish anatomy while preserving eye highlights and overall shape.
  const setFill = (id: string, fill: string) => {
    const node = svg.querySelector(`#${id}`) as SVGElement | null
    if (node) {
      node.setAttribute('fill', fill)
    }
  }

  setFill('body', light)
  setFill('top-fin', light)
  setFill('tail-fin', light)
  setFill('bottom-right-fin', light)
  setFill('bottom-left-fin', dark)
  setFill('eye-rectangle', visor)

  svg.setAttribute('width', '64')
  svg.setAttribute('height', '64')

  const serialized = new XMLSerializer().serializeToString(svg)
  return `data:image/svg+xml,${encodeURIComponent(serialized)}`
}

function buildFallbackFaviconDataUrl(colorHex: string): string {
  const accent = normalizeHexColor(colorHex)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="${accent}"/></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

function resolveBillingAlertMessage(reason?: string | null): string {
  switch ((reason || '').toLowerCase()) {
    case 'requires_action':
      return 'Your bank requires additional verification before we can process payment.'
    case 'requires_payment_method':
      return 'Your default payment method needs to be updated before we can process billing.'
    case 'past_due':
    case 'unpaid':
    case 'incomplete':
      return 'We were unable to collect your subscription payment.'
    case 'invoice_retrying':
      return 'Your latest invoice payment failed and Stripe is retrying automatically.'
    default:
      return 'We are unable to process billing for your subscription right now.'
  }
}

function resolveCreateAgentDisabledMessage(reason?: string | null, actionable = false): string {
  const prefix = actionable
    ? `${resolveBillingAlertMessage(reason)} Resolve billing before creating new agents.`
    : 'New agent creation is unavailable until the workspace billing issue is resolved.'
  return prefix
}

function resolveSendMessageDisabledMessage(): string {
  return 'Resolve billing before sending more messages.'
}

const AGENT_LIMIT_ERROR_PATTERN = /agent limit reached|do not have any persistent agents available/i

type CreateAgentErrorState = {
  message: string
  showUpgradeCta: boolean
  requiresTrialPlanSelection: boolean
  trialOnboardingTarget: TrialOnboardingTarget | null
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

function extractTrialPlanSelectionError(payload: unknown): {
  requiresTrialPlanSelection: boolean
  trialOnboardingTarget: TrialOnboardingTarget | null
} {
  if (!payload || typeof payload !== 'object') {
    return {
      requiresTrialPlanSelection: false,
      trialOnboardingTarget: null,
    }
  }

  const record = payload as Record<string, unknown>
  const requiresTrialPlanSelection = record.requires_plan_selection === true
  const rawTarget = record.onboarding_target
  const trialOnboardingTarget = rawTarget === 'agent_ui' || rawTarget === 'api_keys'
    ? rawTarget
    : null

  return {
    requiresTrialPlanSelection,
    trialOnboardingTarget,
  }
}

function buildCreateAgentError(err: unknown, isProprietaryMode: boolean): CreateAgentErrorState {
  const fallback = 'Unable to create that agent right now.'
  let message: string | null = null
  let requiresTrialPlanSelection = false
  let trialOnboardingTarget: TrialOnboardingTarget | null = null

  if (err instanceof HttpError) {
    message = extractFirstErrorMessage(err.body)
    const trialPlanSelection = extractTrialPlanSelectionError(err.body)
    requiresTrialPlanSelection = trialPlanSelection.requiresTrialPlanSelection
    trialOnboardingTarget = trialPlanSelection.trialOnboardingTarget
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
        requiresTrialPlanSelection: false,
        trialOnboardingTarget: null,
      }
    }
    return {
      message: "You've reached your agent limit for this deployment. Adjust deployment settings to allow more agents.",
      showUpgradeCta: false,
      requiresTrialPlanSelection: false,
      trialOnboardingTarget: null,
    }
  }
  return {
    message: resolved,
    showUpgradeCta: false,
    requiresTrialPlanSelection,
    trialOnboardingTarget,
  }
}

function mergeRosterEntry(agents: AgentRosterEntry[] | undefined, entry: AgentRosterEntry): AgentRosterEntry[] {
  const roster = agents ?? []
  if (roster.some((agent) => agent.id === entry.id)) {
    return roster
  }
  return [...roster, entry]
}

function areStringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false
    }
  }
  return true
}

function touchRosterEntryLastInteraction(
  current: AgentRosterQueryData | undefined,
  agentId: string,
  isoTimestamp: string,
): AgentRosterQueryData | undefined {
  if (!isAgentRosterQueryData(current) || !current.agents?.length) {
    return current
  }

  let changed = false
  const nextAgents = current.agents.map((agent) => {
    if (agent.id !== agentId) {
      return agent
    }
    if (agent.lastInteractionAt === isoTimestamp) {
      return agent
    }
    changed = true
    return {
      ...agent,
      lastInteractionAt: isoTimestamp,
    }
  })

  if (!changed) {
    return current
  }

  return {
    ...current,
    agents: nextAgents,
  }
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
  agentRosterSortMode?: AgentRosterSortMode
  favoriteAgentIds?: string[]
  insightsPanelExpanded?: boolean | null
  agents: AgentRosterEntry[]
  llmIntelligence?: unknown
}

type AgentChatPageStyle = CSSProperties & Record<'--agent-chat-grain-texture', string>

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
  processingActive?: boolean
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
}

function normalizeSignupPreviewState(value: unknown): SignupPreviewState {
  return value === 'awaiting_first_reply_pause' || value === 'awaiting_signup_completion'
    ? value
    : 'none'
}

function normalizePlanningState(value: unknown): PlanningState {
  return value === 'planning' || value === 'completed' || value === 'skipped'
    ? value
    : 'skipped'
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
  deleted?: boolean
  onCreateAgent: () => void
  createAgentDisabledReason?: string | null
  onBlockedCreateAgent?: () => void
}

function AgentNotFoundState({
  hasOtherAgents,
  deleted = false,
  onCreateAgent,
  createAgentDisabledReason = null,
  onBlockedCreateAgent,
}: AgentNotFoundStateProps) {
  const createAgentDisabled = Boolean(createAgentDisabledReason)
  const trackableCreateAgentDisabled = createAgentDisabled && Boolean(onBlockedCreateAgent)
  const handleCreateAgentClick = useCallback(() => {
    if (createAgentDisabled && onBlockedCreateAgent) {
      onBlockedCreateAgent()
      return
    }
    onCreateAgent()
  }, [createAgentDisabled, onBlockedCreateAgent, onCreateAgent])

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-4">
      <div className="mb-6 flex size-16 items-center justify-center rounded-full bg-amber-100 text-amber-600">
        <AlertTriangle className="size-8" aria-hidden="true" />
      </div>
      <h2 className="mb-2 text-xl font-semibold text-gray-800">{deleted ? 'Agent deleted' : 'Agent not found'}</h2>
      <p className="mb-6 max-w-md text-center text-sm text-gray-600">
        {deleted
          ? (hasOtherAgents
              ? 'This agent has been deleted. Select another agent from the sidebar or create a new one.'
              : 'This agent has been deleted. Create a new agent to get started.')
          : (hasOtherAgents
              ? 'This agent may have been deleted or you may not have access to it. Select another agent from the sidebar or create a new one.'
              : 'This agent may have been deleted or you may not have access to it. Create a new agent to get started.')}
      </p>
      <button
        type="button"
        onClick={handleCreateAgentClick}
        disabled={createAgentDisabled && !trackableCreateAgentDisabled}
        aria-disabled={createAgentDisabled ? 'true' : undefined}
        title={createAgentDisabledReason ?? undefined}
        className={`group inline-flex items-center justify-center gap-x-2 rounded-lg px-6 py-3 font-semibold text-white transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
          createAgentDisabled
            ? 'cursor-not-allowed bg-slate-400/80'
            : 'bg-gradient-to-r from-blue-600 to-indigo-600 shadow-lg hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl'
        }`}
      >
        <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
        Create New Agent
      </button>
      {createAgentDisabledReason ? (
        <p className="mt-3 max-w-md text-center text-sm text-slate-500">{createAgentDisabledReason}</p>
      ) : null}
    </div>
  )
}

type AgentSelectStateProps = {
  hasAgents: boolean
  onCreateAgent?: () => void
  createAgentDisabledReason?: string | null
  onBlockedCreateAgent?: () => void
}

function AgentSelectState({
  hasAgents,
  onCreateAgent,
  createAgentDisabledReason = null,
  onBlockedCreateAgent,
}: AgentSelectStateProps) {
  const createAgentDisabled = Boolean(createAgentDisabledReason)
  const trackableCreateAgentDisabled = createAgentDisabled && Boolean(onBlockedCreateAgent)
  const handleCreateAgentClick = useCallback(() => {
    if (createAgentDisabled && onBlockedCreateAgent) {
      onBlockedCreateAgent()
      return
    }
    onCreateAgent?.()
  }, [createAgentDisabled, onBlockedCreateAgent, onCreateAgent])

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
          onClick={handleCreateAgentClick}
          disabled={createAgentDisabled && !trackableCreateAgentDisabled}
          aria-disabled={createAgentDisabled ? 'true' : undefined}
          title={createAgentDisabledReason ?? undefined}
          className={`group mt-2 inline-flex items-center justify-center gap-x-2 rounded-lg px-6 py-3 font-semibold text-white transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
            createAgentDisabled
              ? 'cursor-not-allowed bg-slate-400/80'
              : 'bg-gradient-to-r from-blue-600 to-indigo-600 shadow-lg hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl'
          }`}
        >
          <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
          Create Your First Agent
        </button>
      ) : null}
      {!hasAgents && createAgentDisabledReason ? (
        <p className="max-w-lg text-sm text-slate-500">{createAgentDisabledReason}</p>
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
  isStaff?: boolean
  auditUrl?: string | null
  auditUrlTemplate?: string | null
  maxChatUploadSizeBytes?: number | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  canManageCollaborators?: boolean | null
  isCollaborator?: boolean | null
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
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
const TOP_LOAD_THRESHOLD_PX = 200
const BOTTOM_EXIT_THRESHOLD_PX = 1
const PROGRAMMATIC_SCROLL_GUARD_MS = 150
const RESUME_TIMELINE_BACKFILL_MAX_NEWER_PAGES = DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES

// Gated diagnostic logging for the "random snap to bottom" investigation.
// Enable in any environment by running `window.__DEBUG_SCROLL_SNAP__ = true` in DevTools.
// Each call captures the trigger site plus current scroll geometry so we can tell whether
// a snap originated from a re-pin (IntersectionObserver / handleScroll), a content-change
// auto-follow (ResizeObserver), or a stale isNearBottom render.
function isScrollSnapDebugEnabled(): boolean {
  if (typeof window === 'undefined') return false
  return Boolean((window as unknown as { __DEBUG_SCROLL_SNAP__?: boolean }).__DEBUG_SCROLL_SNAP__)
}

function logScrollSnap(event: string, details: Record<string, unknown> = {}): void {
  if (!isScrollSnapDebugEnabled()) return
  const container = typeof document !== 'undefined'
    ? document.getElementById('timeline-shell')
    : null
  const geometry = container
    ? {
        scrollTop: container.scrollTop,
        scrollHeight: container.scrollHeight,
        clientHeight: container.clientHeight,
        distanceFromBottom: container.scrollHeight - container.scrollTop - container.clientHeight,
      }
    : null
  // eslint-disable-next-line no-console
  console.log(`[scroll-snap] ${event}`, { ...details, geometry, t: Date.now() })
  // eslint-disable-next-line no-console
  console.trace(`[scroll-snap] ${event}`)
}

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
  auditUrl,
  auditUrlTemplate,
  maxChatUploadSizeBytes = null,
  viewerUserId,
  viewerEmail,
  canManageCollaborators,
  isCollaborator,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
  onClose,
  onCreateAgent,
  onAgentCreated,
  showContextSwitcher = false,
  persistContextSession = true,
  onContextSwitch,
}: AgentChatPageProps) {
  const initialThemeColorRef = useRef<string | null>(null)
  const fishFaviconSvgRef = useRef<string | null>(null)
  const fishFaviconSvgPromiseRef = useRef<Promise<string> | null>(null)

  const [activeAgentId, setActiveAgentId] = useState<string | null>(agentId ?? null)
  const activeAgentIdRef = useRef<string | null>(activeAgentId)
  const routeAgentId = typeof agentId === 'string' ? agentId : null
  const queryClient = useQueryClient()
  const {
    currentPlan,
    isProprietaryMode,
    ensureAuthenticated,
    upgradeModalSource,
    openUpgradeModal,
    personalSignupPreviewAvailable,
  } = useSubscriptionStore()
  const isNewAgent = agentId === null
  const isSelectionView = agentId === undefined
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const pendingCreateRef = useRef<{
    body: string
    attachments: File[]
    tier: IntelligenceTierKey
    charterOverride?: string | null
    selectedPipedreamAppSlugs?: string[]
  } | null>(null)
  const previewEnteredAgentIdsRef = useRef<Set<string>>(new Set())
  const [intelligenceGate, setIntelligenceGate] = useState<IntelligenceGateState | null>(null)
  const pendingAgentMetaRef = useRef<AgentSwitchMeta | null>(null)
  const locallySelectedAgentIdsRef = useRef<Set<string>>(new Set())
  const selectedFromCurrentRoster = Boolean(activeAgentId && locallySelectedAgentIdsRef.current.has(activeAgentId))
  const contextLookupAgentId = isNewAgent || isSelectionView
    ? undefined
    : selectedFromCurrentRoster
      ? undefined
      : routeAgentId ?? activeAgentId ?? undefined

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
    resolvedForAgentId: contextResolvedForAgentId,
    isLoading: contextLoading,
    isSwitching: contextSwitching,
    error: contextError,
    switchContext,
  } = useConsoleContextSwitcher({
    enabled: true,
    forAgentId: contextLookupAgentId,
    onSwitched: handleContextSwitched,
    persistSession: persistContextSession,
  })

  const [switchingAgentId, setSwitchingAgentId] = useState<string | null>(null)
  const [selectionSidebarCollapsed, setSelectionSidebarCollapsed] = useState(false)
  const [pendingAgentEmails, setPendingAgentEmails] = useState<Record<string, string>>({})
  const contactRefreshAttemptsRef = useRef<Record<string, number>>({})
  const effectiveContext = contextData?.context ?? null
  const contextMatchesAgent = !contextLookupAgentId || contextResolvedForAgentId === contextLookupAgentId
  const contextReady = (
    Boolean(effectiveContext)
    && contextMatchesAgent
    && (!contextLoading || !contextLookupAgentId)
    && !contextSwitching
    && !contextError
  )
  const agentContextReady = contextReady
  const liveAgentId = !agentContextReady ? null : activeAgentId

  useEffect(() => {
    setActiveAgentId(agentId ?? null)
  }, [agentId])

  useEffect(() => {
    activeAgentIdRef.current = activeAgentId
  }, [activeAgentId])

  // Set up queryClient bridge for the Zustand store
  useEffect(() => { setTimelineQueryClient(queryClient) }, [queryClient])

  // React-query timeline data
  const timelineQuery = useAgentTimeline(activeAgentId, { enabled: agentContextReady && !isNewAgent })
  const flatEvents = useMemo(() => flattenTimelinePages(timelineQuery.data), [timelineQuery.data])
  const initialPageResponse = useMemo(() => getInitialPageResponse(timelineQuery.data), [timelineQuery.data])
  const pendingActionRequests = useMemo<PendingActionRequest[]>(
    () => initialPageResponse?.pending_action_requests ?? [],
    [initialPageResponse],
  )

  // Extract agent metadata from timeline response
  useEffect(() => {
    if (!initialPageResponse || !activeAgentId) return
    const store = useAgentChatStore.getState()
    if (store.agentId !== activeAgentId) return
    // Update processing state from timeline response
    const snapshot = initialPageResponse.processing_snapshot
    const processingActive = snapshot?.active ?? initialPageResponse.processing_active
    if (processingActive !== undefined) {
      store.updateProcessing(snapshot ?? { active: processingActive, webTasks: [] })
    }
    // Update agent identity from timeline response
    const color = initialPageResponse.agent_color_hex
      ? normalizeHexColor(initialPageResponse.agent_color_hex)
      : null
    const name = initialPageResponse.agent_name ?? null
    const avatar = initialPageResponse.agent_avatar_url ?? null
    const signupPreviewState = normalizeSignupPreviewState(initialPageResponse.signup_preview_state)
    const planningState = normalizePlanningState(initialPageResponse.planning_state)
    if (color || name || avatar || signupPreviewState !== 'none' || planningState !== 'skipped') {
      store.updateAgentIdentity({
        agentId: activeAgentId,
        ...(color ? { agentColorHex: color } : {}),
        ...(name ? { agentName: name } : {}),
        ...(avatar ? { agentAvatarUrl: avatar } : {}),
        signupPreviewState,
        planningState,
      })
    }
  }, [initialPageResponse, activeAgentId])

  // Zustand store subscriptions (slimmed down — no more events/cursors/loading)
  const setAgentId = useAgentChatStore((state) => state.setAgentId)
  const storeAgentId = useAgentChatStore((state) => state.agentId)
  const agentColorHex = useAgentChatStore((state) => state.agentColorHex)
  const storedAgentName = useAgentChatStore((state) => state.agentName)
  const storedAgentAvatarUrl = useAgentChatStore((state) => state.agentAvatarUrl)
  const signupPreviewState = useAgentChatStore((state) => state.signupPreviewState)
  const planningState = useAgentChatStore((state) => state.planningState)
  const sendMessage = useAgentChatStore((state) => state.sendMessage)
  const receiveRealtimeEvent = useAgentChatStore((state) => state.receiveRealtimeEvent)
  const hasUnseenActivity = useAgentChatStore((state) => state.hasUnseenActivity)
  const processingActive = useAgentChatStore((state) => state.processingActive)
  const processingStartedAt = useAgentChatStore((state) => state.processingStartedAt)
  const awaitingResponse = useAgentChatStore((state) => state.awaitingResponse)
  const processingWebTasks = useAgentChatStore((state) => state.processingWebTasks)
  const nextScheduledAt = useAgentChatStore((state) => state.nextScheduledAt)
  const streaming = useAgentChatStore((state) => state.streaming)
  const streamingLastUpdatedAt = useAgentChatStore((state) => state.streamingLastUpdatedAt)
  const finalizeStreaming = useAgentChatStore((state) => state.finalizeStreaming)
  const refreshProcessing = useAgentChatStore((state) => state.refreshProcessing)
  const realtimeEventCursors = useAgentChatStore((state) => state.realtimeEventCursors)
  const consumeRealtimeEventCursor = useAgentChatStore((state) => state.consumeRealtimeEventCursor)
  const persistPendingEventsToCache = useAgentChatStore((state) => state.persistPendingEventsToCache)
  const setInsightsForAgent = useAgentChatStore((state) => state.setInsightsForAgent)
  const startInsightRotation = useAgentChatStore((state) => state.startInsightRotation)
  const stopInsightRotation = useAgentChatStore((state) => state.stopInsightRotation)
  const dismissInsight = useAgentChatStore((state) => state.dismissInsight)
  const setInsightsPaused = useAgentChatStore((state) => state.setInsightsPaused)
  const setCurrentInsightIndex = useAgentChatStore((state) => state.setCurrentInsightIndex)
  const insights = useAgentChatStore((state) => state.insights)
  const currentInsightIndex = useAgentChatStore((state) => state.currentInsightIndex)
  const dismissedInsightIds = useAgentChatStore((state) => state.dismissedInsightIds)
  const insightsPaused = useAgentChatStore((state) => state.insightsPaused)
  const autoScrollPinned = useAgentChatStore((state) => state.autoScrollPinned)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const suppressAutoScrollPin = useAgentChatStore((state) => state.suppressAutoScrollPin)
  const autoScrollPinSuppressedUntil = useAgentChatStore((state) => state.autoScrollPinSuppressedUntil)
  const previousViewedAgentIdRef = useRef<string | null>(activeAgentId)

  useEffect(() => {
    const previousAgentId = previousViewedAgentIdRef.current
    if (previousAgentId && previousAgentId !== activeAgentId) {
      persistPendingEventsToCache()
    }
    previousViewedAgentIdRef.current = activeAgentId
  }, [activeAgentId, persistPendingEventsToCache])

  // Derive timeline state from react-query
  const isStoreSynced = storeAgentId === activeAgentId
  const hasMoreOlder = timelineQuery.hasPreviousPage ?? false
  const hasMoreNewer = timelineQuery.hasNextPage ?? false
  const timelineEvents = !isNewAgent ? flatEvents : []
  const timelineHasMoreOlder = !isNewAgent ? hasMoreOlder : false
  const timelineHasMoreNewer = !isNewAgent ? hasMoreNewer : false
  const timelineHasUnseenActivity = !isNewAgent && isStoreSynced ? hasUnseenActivity : false
  const timelineProcessingActive = !isNewAgent && isStoreSynced ? processingActive : false
  const timelineProcessingStartedAt = !isNewAgent && isStoreSynced ? processingStartedAt : null
  const timelineAwaitingResponse = !isNewAgent && isStoreSynced ? awaitingResponse : false
  const timelineProcessingWebTasks = !isNewAgent && isStoreSynced ? processingWebTasks : []
  const timelineNextScheduledAt = !isNewAgent && isStoreSynced ? nextScheduledAt : null
  const timelineStreaming = !isNewAgent && isStoreSynced ? streaming : null
  const timelineLoadingOlder = !isNewAgent ? timelineQuery.isFetchingPreviousPage : false
  const timelineLoadingNewer = !isNewAgent ? timelineQuery.isFetchingNextPage : false
  const initialLoading = !isNewAgent && timelineQuery.isLoading

  const statusExpansionTargets = useMemo(
    () => findLatestStatusExpansionTargets(timelineEvents),
    [timelineEvents],
  )
  const displayEvents = useMemo(
    () => collapseDetailedStatusRuns(timelineEvents, statusExpansionTargets),
    [timelineEvents, statusExpansionTargets],
  )
  const [timelineCanScrollForOlder, setTimelineCanScrollForOlder] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)

  const canLoadOlderViaScroll = useCallback((container: HTMLElement | null) => {
    if (!container) {
      return false
    }
    return container.scrollHeight > container.clientHeight + TIMELINE_SCROLLABILITY_EPSILON_PX
  }, [])

  const syncTimelineScrollability = useCallback((container: HTMLElement | null) => {
    const next = canLoadOlderViaScroll(container)
    setTimelineCanScrollForOlder((current) => (current === next ? current : next))
    return next
  }, [canLoadOlderViaScroll])

  const showOlderLoadButton = (
    !initialLoading
    && !isNewAgent
    && !switchingAgentId
    && timelineEvents.length > 0
    && timelineHasMoreOlder
    && !timelineLoadingOlder
    && !timelineCanScrollForOlder
  )

  const prevPageCountRef = useRef(timelineQuery.data?.pages?.length ?? 0)
  const prevScrollHeightRef = useRef(0)
  const prependTrackingAgentIdRef = useRef<string | null>(activeAgentId)
  const preservePrependViewportRef = useRef(false)
  const olderPageRequestInFlightRef = useRef(false)
  const autoScrollPinnedRef = useRef(autoScrollPinned)
  const autoScrollPinSuppressedUntilRef = useRef(autoScrollPinSuppressedUntil)
  const lastProgrammaticScrollAtRef = useRef(0)
  const forceScrollOnNextUpdateRef = useRef(false)
  const didInitialScrollRef = useRef(false)
  const isNearBottomRef = useRef(isNearBottom)
  const autoRepinTimeoutRef = useRef<number | null>(null)
  const composerFocusNudgeTimeoutRef = useRef<number | null>(null)
  const userTouchActiveRef = useRef(false)
  const touchEndTimerRef = useRef<number | null>(null)
  const pinnedAtSuspendRef = useRef(autoScrollPinned)
  const resumeBackfillInFlightRef = useRef<Promise<void> | null>(null)
  const resumeBackfillRunIdRef = useRef(0)
  const allowAgentRefreshRef = useRef(false)

  // Track if we should scroll on next content update (captured before DOM changes)
  const shouldScrollOnNextUpdateRef = useRef(autoScrollPinned)

  useLayoutEffect(() => {
    autoScrollPinnedRef.current = autoScrollPinned
    autoScrollPinSuppressedUntilRef.current = autoScrollPinSuppressedUntil
    isNearBottomRef.current = isNearBottom
  }, [autoScrollPinned, autoScrollPinSuppressedUntil, isNearBottom])

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

  const freezeTimelineViewport = useCallback(() => {
    const wasPinned = autoScrollPinnedRef.current
    shouldScrollOnNextUpdateRef.current = false
    forceScrollOnNextUpdateRef.current = false
    autoScrollPinnedRef.current = false
    autoScrollPinSuppressedUntilRef.current = Date.now() + AUTO_SCROLL_REPIN_SUPPRESSION_MS
    if (wasPinned) {
      setAutoScrollPinned(false)
    }
    suppressAutoScrollPin(AUTO_SCROLL_REPIN_SUPPRESSION_MS)
    if (autoRepinTimeoutRef.current !== null) {
      window.clearTimeout(autoRepinTimeoutRef.current)
      autoRepinTimeoutRef.current = null
    }
  }, [setAutoScrollPinned, suppressAutoScrollPin])

  const requestPreviousPage = useCallback(() => {
    if (
      olderPageRequestInFlightRef.current
      || !timelineQuery.hasPreviousPage
      || timelineQuery.isFetchingPreviousPage
      || timelineQuery.isFetchPreviousPageError
    ) {
      return
    }

    freezeTimelineViewport()
    prevPageCountRef.current = timelineQuery.data?.pages?.length ?? 0
    prevScrollHeightRef.current = timelineRef.current?.scrollHeight ?? 0
    preservePrependViewportRef.current = true
    olderPageRequestInFlightRef.current = true
    void timelineQuery.fetchPreviousPage().finally(() => {
      olderPageRequestInFlightRef.current = false
    })
  }, [
    timelineQuery.data?.pages?.length,
    timelineQuery.fetchPreviousPage,
    timelineQuery.hasPreviousPage,
    timelineQuery.isFetchingPreviousPage,
    timelineQuery.isFetchPreviousPageError,
    freezeTimelineViewport,
  ])

  useEffect(() => {
    olderPageRequestInFlightRef.current = false
  }, [activeAgentId])

  // Auto-trigger older loading when scrolled near top
  useEffect(() => {
    const container = timelineRef.current
    const canReachOlderViaScroll = canLoadOlderViaScroll(container)
    const nearTopByScroll = container ? container.scrollTop <= TOP_LOAD_THRESHOLD_PX : false
    if (
      !didInitialScrollRef.current
      || initialLoading
      || isNewAgent
      || switchingAgentId
      || !timelineEvents.length
      || !canReachOlderViaScroll
    ) {
      return
    }
    if (nearTopByScroll) {
      requestPreviousPage()
    }
  }, [
    initialLoading,
    isNewAgent,
    canLoadOlderViaScroll,
    requestPreviousPage,
    switchingAgentId,
    timelineEvents.length,
  ])

  // Preserve the viewport when older pages prepend above the current scroll position.
  useLayoutEffect(() => {
    const pageCount = timelineQuery.data?.pages?.length ?? 0
    if (prependTrackingAgentIdRef.current !== activeAgentId) {
      prependTrackingAgentIdRef.current = activeAgentId
      preservePrependViewportRef.current = false
      prevPageCountRef.current = pageCount
      prevScrollHeightRef.current = timelineRef.current?.scrollHeight ?? 0
      return
    }

    if (preservePrependViewportRef.current && pageCount > prevPageCountRef.current && prevPageCountRef.current > 0) {
      const container = timelineRef.current
      if (container) {
        const newScrollHeight = container.scrollHeight
        const delta = newScrollHeight - prevScrollHeightRef.current
        if (delta > 0) {
          container.scrollTop += delta
        }
        syncTimelineScrollability(container)
        syncNearBottomState(container)
      }
    }

    if (preservePrependViewportRef.current && timelineQuery.isFetchingPreviousPage) {
      return
    }

    preservePrependViewportRef.current = false
    prevPageCountRef.current = pageCount
    prevScrollHeightRef.current = timelineRef.current?.scrollHeight ?? 0
  }, [
    activeAgentId,
    syncNearBottomState,
    syncTimelineScrollability,
    timelineQuery.data?.pages?.length,
    timelineQuery.isFetchingPreviousPage,
  ])

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
    if (activeAgentId) {
      void queryClient.invalidateQueries({ queryKey: ['agent-quick-settings', activeAgentId], exact: true })
    }
    void queryClient.invalidateQueries({ queryKey: ['usage-summary', 'agent-chat'], exact: false })
  }, [activeAgentId, queryClient])
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
      const hasProcessingActive = Object.prototype.hasOwnProperty.call(rawPayload, 'processing_active')
      const hasSignupPreviewState = Object.prototype.hasOwnProperty.call(rawPayload, 'signup_preview_state')
      const hasPlanningState = Object.prototype.hasOwnProperty.call(rawPayload, 'planning_state')
      if (
        !hasName
        && !hasColor
        && !hasAvatar
        && !hasShortDescription
        && !hasMiniDescription
        && !hasProcessingActive
        && !hasSignupPreviewState
        && !hasPlanningState
      ) {
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
          current: AgentRosterQueryData | undefined,
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
            if (hasProcessingActive) {
              const nextProcessingActive = Boolean(rawPayload.processing_active)
              if (nextProcessingActive !== next.processingActive) {
                next.processingActive = nextProcessingActive
                changed = true
              }
            }
            if (hasSignupPreviewState) {
              const nextSignupPreviewState = normalizeSignupPreviewState(rawPayload.signup_preview_state)
              if (nextSignupPreviewState !== (next.signupPreviewState ?? 'none')) {
                next.signupPreviewState = nextSignupPreviewState
                changed = true
              }
            }
            if (hasPlanningState) {
              const nextPlanningState = normalizePlanningState(rawPayload.planning_state)
              if (nextPlanningState !== (next.planningState ?? 'skipped')) {
                next.planningState = nextPlanningState
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
            agents: nextAgents,
          }
        },
      )
    },
    [queryClient],
  )
  const { status: sessionStatus, error: sessionError } = useAgentWebSession(liveAgentId, {
    keepAliveWhenHidden: Boolean(timelineProcessingActive || timelineAwaitingResponse),
  })
  const rosterContextKey = effectiveContext ? `${effectiveContext.type}:${effectiveContext.id}` : 'unknown'
  const rosterQueryAgentId = contextLookupAgentId
  const hasPendingAvatarTracking = Object.keys(pendingAvatarTracking).length > 0
  const rosterRefreshIntervalMs = hasPendingAvatarTracking
    ? ROSTER_PENDING_AVATAR_REFRESH_INTERVAL_MS
    : ROSTER_REFRESH_INTERVAL_MS
  const rosterQuery = useAgentRoster({
    enabled: contextReady,
    contextKey: rosterContextKey,
    forAgentId: rosterQueryAgentId,
    refetchIntervalMs: rosterRefreshIntervalMs,
  })
  const [agentRosterSortMode, setAgentRosterSortMode] = useState<AgentRosterSortMode>('recent')
  const [favoriteAgentIds, setFavoriteAgentIds] = useState<string[]>([])
  const [insightsPanelExpandedPreference, setInsightsPanelExpandedPreference] = useState<boolean | null>(null)
  const hasHydratedAgentRosterSortModeRef = useRef(false)
  const hasHydratedInsightsPanelExpandedPreferenceRef = useRef(false)

  useEffect(() => {
    const serverSortMode = rosterQuery.data?.agentRosterSortMode
    if (!serverSortMode || hasHydratedAgentRosterSortModeRef.current) {
      return
    }
    hasHydratedAgentRosterSortModeRef.current = true
    setAgentRosterSortMode(serverSortMode)
  }, [rosterQuery.data?.agentRosterSortMode])

  useEffect(() => {
    const serverFavoriteAgentIds = Array.isArray(rosterQuery.data?.favoriteAgentIds)
      ? rosterQuery.data.favoriteAgentIds
      : []
    setFavoriteAgentIds((current) => (
      areStringArraysEqual(current, serverFavoriteAgentIds)
        ? current
        : serverFavoriteAgentIds
    ))
  }, [rosterQuery.data?.favoriteAgentIds])

  useEffect(() => {
    const serverInsightsPanelExpanded = rosterQuery.data?.insightsPanelExpanded
    if (serverInsightsPanelExpanded === undefined || hasHydratedInsightsPanelExpandedPreferenceRef.current) {
      return
    }
    hasHydratedInsightsPanelExpandedPreferenceRef.current = true
    setInsightsPanelExpandedPreference(parseNullableBooleanPreference(serverInsightsPanelExpanded))
  }, [rosterQuery.data?.insightsPanelExpanded])

  const handleAgentRosterSortModeChange = useCallback(
    (nextSortMode: AgentRosterSortMode) => {
      setAgentRosterSortMode(nextSortMode)
      queryClient.setQueriesData<AgentRosterQueryData>(
        { queryKey: ['agent-roster'] },
        (current) => {
          if (!isAgentRosterQueryData(current)) {
            return current
          }
          if (current.agentRosterSortMode === nextSortMode) {
            return current
          }
          return {
            ...current,
            agentRosterSortMode: nextSortMode,
          }
        },
      )

      void updateUserPreferences({
        preferences: {
          [USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE]: nextSortMode,
        },
      }).then((response) => {
        const persistedSortMode = parseAgentRosterSortMode(
          response.preferences[USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE],
        )
        setAgentRosterSortMode(persistedSortMode)
        queryClient.setQueriesData<AgentRosterQueryData>(
          { queryKey: ['agent-roster'] },
          (current) => {
            if (!isAgentRosterQueryData(current)) {
              return current
            }
            if (current.agentRosterSortMode === persistedSortMode) {
              return current
            }
            return {
              ...current,
              agentRosterSortMode: persistedSortMode,
            }
          },
        )
      }).catch(() => undefined)
    },
    [queryClient],
  )

  const updateFavoriteAgentIdsInRosterCache = useCallback(
    (nextFavoriteAgentIds: string[]) => {
      queryClient.setQueriesData<AgentRosterQueryData>(
        { queryKey: ['agent-roster'] },
        (current) => {
          if (!isAgentRosterQueryData(current)) {
            return current
          }
          const currentFavoriteAgentIds = Array.isArray(current.favoriteAgentIds)
            ? current.favoriteAgentIds.filter((value): value is string => typeof value === 'string')
            : []
          if (areStringArraysEqual(currentFavoriteAgentIds, nextFavoriteAgentIds)) {
            return current
          }
          return {
            ...current,
            favoriteAgentIds: nextFavoriteAgentIds,
          }
        },
      )
    },
    [queryClient],
  )

  const updateInsightsPanelExpandedInRosterCache = useCallback(
    (nextInsightsPanelExpanded: boolean | null) => {
      queryClient.setQueriesData<AgentRosterQueryData>(
        { queryKey: ['agent-roster'] },
        (current) => {
          if (!isAgentRosterQueryData(current)) {
            return current
          }
          if (current.insightsPanelExpanded === nextInsightsPanelExpanded) {
            return current
          }
          return {
            ...current,
            insightsPanelExpanded: nextInsightsPanelExpanded,
          }
        },
      )
    },
    [queryClient],
  )

  const handleToggleAgentFavorite = useCallback(
    (agentId: string) => {
      const nextFavoriteAgentIds = favoriteAgentIds.includes(agentId)
        ? favoriteAgentIds.filter((candidateId) => candidateId !== agentId)
        : [...favoriteAgentIds, agentId]

      setFavoriteAgentIds(nextFavoriteAgentIds)
      updateFavoriteAgentIdsInRosterCache(nextFavoriteAgentIds)

      void updateUserPreferences({
        preferences: {
          [USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS]: nextFavoriteAgentIds,
        },
      }).then((response) => {
        const persistedFavoriteAgentIds = parseFavoriteAgentIdsPreference(
          response.preferences[USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS],
        )
        setFavoriteAgentIds(persistedFavoriteAgentIds)
        updateFavoriteAgentIdsInRosterCache(persistedFavoriteAgentIds)
      }).catch(() => undefined)
    },
    [favoriteAgentIds, updateFavoriteAgentIdsInRosterCache],
  )

  const handleInsightsPanelExpandedPreferenceChange = useCallback(
    (nextInsightsPanelExpanded: boolean) => {
      setInsightsPanelExpandedPreference(nextInsightsPanelExpanded)
      updateInsightsPanelExpandedInRosterCache(nextInsightsPanelExpanded)

      void updateUserPreferences({
        preferences: {
          [USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED]: nextInsightsPanelExpanded,
        },
      }).then((response) => {
        const persistedInsightsPanelExpanded = parseNullableBooleanPreference(
          response.preferences[USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED],
        )
        setInsightsPanelExpandedPreference(persistedInsightsPanelExpanded)
        updateInsightsPanelExpandedInRosterCache(persistedInsightsPanelExpanded)
      }).catch(() => undefined)
    },
    [updateInsightsPanelExpandedInRosterCache],
  )

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

  const shouldAutoFollowTimeline = useCallback(() => {
    return autoScrollPinnedRef.current && isNearBottomRef.current && !userTouchActiveRef.current
  }, [])

  const repinAutoScrollIfAtBottom = useCallback((container: HTMLElement | null, source: string = 'unknown') => {
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
    // If unseen events are waiting in pending cache, force a post-flush jump so
    // we don't strand the viewport just above newly injected latest content.
    if (timelineHasUnseenActivity) {
      forceScrollOnNextUpdateRef.current = true
    }
    logScrollSnap('repinAutoScrollIfAtBottom', {
      source,
      distanceFromBottom,
      timelineHasUnseenActivity,
      userTouchActive: userTouchActiveRef.current,
    })
    autoScrollPinnedRef.current = true
    setAutoScrollPinned(true)
  }, [setAutoScrollPinned, timelineHasUnseenActivity])

  const unpinAutoScrollFromUserGesture = useCallback(() => {
    if (!autoScrollPinnedRef.current) {
      return
    }
    freezeTimelineViewport()
    autoRepinTimeoutRef.current = window.setTimeout(() => {
      autoRepinTimeoutRef.current = null
      repinAutoScrollIfAtBottom(document.getElementById('timeline-shell'))
    }, AUTO_SCROLL_REPIN_SUPPRESSION_MS + 16)
  }, [freezeTimelineViewport, repinAutoScrollIfAtBottom])

  useEffect(() => {
    didInitialScrollRef.current = false
  }, [activeAgentId])

  useEffect(() => {
    resumeBackfillRunIdRef.current += 1
    resumeBackfillInFlightRef.current = null
    pinnedAtSuspendRef.current = autoScrollPinnedRef.current
  }, [activeAgentId])

  useEffect(() => {
    setCollaboratorInviteOpen(false)
  }, [activeAgentId])

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

        logScrollSnap('IntersectionObserver:fire', {
          isIntersecting: entry.isIntersecting,
          distanceFromBottom,
          atBottom,
          autoScrollPinned: autoScrollPinnedRef.current,
        })

        // Auto-restick only when the user is truly at the bottom.
        if (atBottom) {
          repinAutoScrollIfAtBottom(container, 'IntersectionObserver')
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
      const canReachOlderViaScroll = syncTimelineScrollability(container)
      if (
        didInitialScrollRef.current
        && !initialLoading
        && !isNewAgent
        && !switchingAgentId
        && timelineEvents.length
        && canReachOlderViaScroll
        && nextScrollTop <= TOP_LOAD_THRESHOLD_PX
      ) {
        requestPreviousPage()
      }
      // Don't try to re-pin while user is actively touching — let their scroll intent take priority
      if (!userTouchActiveRef.current) {
        repinAutoScrollIfAtBottom(container, 'handleScroll')
      }
      const movedAwayFromBottom = typeof distanceFromBottom === 'number'
        && distanceFromBottom > BOTTOM_EXIT_THRESHOLD_PX
      // Guard against false unpins from programmatic scrolls (scrollIntoView can cause transient scrollTop decreases).
      // Bypass the guard when the user is actively touching — touch scroll is always user-initiated.
      if (
        movedAwayFromBottom
        && nextScrollTop < lastScrollTop - 2
        && (userTouchActiveRef.current || Date.now() - lastProgrammaticScrollAtRef.current > PROGRAMMATIC_SCROLL_GUARD_MS)
      ) {
        logScrollSnap('handleScroll:unpin', {
          nextScrollTop,
          lastScrollTop,
          distanceFromBottom,
          userTouchActive: userTouchActiveRef.current,
          msSinceProgrammatic: Date.now() - lastProgrammaticScrollAtRef.current,
        })
        unpinAutoScrollFromUserGesture()
      }
      lastScrollTop = nextScrollTop
    }

    // Detect scroll-up via wheel
    const handleWheel = (e: WheelEvent) => {
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      if (e.deltaY < 0 && distanceFromBottom > BOTTOM_EXIT_THRESHOLD_PX) {
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
        if (distanceFromBottom > BOTTOM_EXIT_THRESHOLD_PX) {
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
      if (scrollUpKeys.includes(e.key) && distanceFromBottom > BOTTOM_EXIT_THRESHOLD_PX) {
        unpinAutoScrollFromUserGesture()
      }
    }

    // Listen on the container, not window
    syncNearBottomState(container)
    syncTimelineScrollability(container)
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
  }, [
    initialLoading,
    isNewAgent,
    repinAutoScrollIfAtBottom,
    requestPreviousPage,
    syncTimelineScrollability,
    switchingAgentId,
    syncNearBottomState,
    timelineEvents.length,
    unpinAutoScrollFromUserGesture,
  ])

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
    const willScroll = autoScrollPinned && isNearBottom
    if (willScroll) {
      logScrollSnap('renderDecision:shouldScrollOnNextUpdate', {
        autoScrollPinned,
        // Note: this is the React state, which can lag isNearBottomRef.current
        isNearBottomReactState: isNearBottom,
        isNearBottomRefCurrent: isNearBottomRef.current,
        eventsChanged: timelineEvents !== prevEventsRef.current,
        streamingChanged: timelineStreaming !== prevStreamingRef.current,
        prevEventsLen: prevEventsRef.current?.length ?? 0,
        nextEventsLen: timelineEvents?.length ?? 0,
      })
    }
    shouldScrollOnNextUpdateRef.current = willScroll
    prevEventsRef.current = timelineEvents
    prevStreamingRef.current = timelineStreaming
  }

  const pendingScrollFrameRef = useRef<number | null>(null)

  // Lightweight auto-follow: just set scrollTop, no overflow toggle.
  // Used by ResizeObserver callbacks to avoid layout thrashing during rapid layout updates.
  // Does NOT update lastProgrammaticScrollAtRef — that guard is for user-initiated
  // programmatic scrolls (jump-to-latest, send). Auto-follow must not block detection
  // of user scroll-up gestures (especially momentum scrolling after touch end on mobile).
  const snapToBottom = useCallback(() => {
    const container = document.getElementById('timeline-shell')
    if (!container) return
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    // Already at bottom — skip to avoid triggering scroll events
    if (distanceFromBottom < 2) return
    logScrollSnap('snapToBottom:execute', {
      distanceFromBottom,
      autoScrollPinned: autoScrollPinnedRef.current,
      userTouchActive: userTouchActiveRef.current,
    })
    container.scrollTop = container.scrollHeight
    isNearBottomRef.current = true
    setIsNearBottom(true)
  }, [])

  // Full jump with iOS momentum kill — used for user-initiated actions
  // (send message, click jump button, composer focus, initial scroll).
  const jumpToBottom = useCallback(() => {
    const container = document.getElementById('timeline-shell')
    const sentinel = document.getElementById('timeline-bottom-sentinel')
    if (!container) return
    logScrollSnap('jumpToBottom:execute', {
      autoScrollPinned: autoScrollPinnedRef.current,
      hasSentinel: Boolean(sentinel),
    })
    lastProgrammaticScrollAtRef.current = Date.now()
    // Kill iOS momentum scrolling — toggling overflow forces the scroll to stop immediately
    container.style.overflowY = 'hidden'
    if (sentinel) {
      sentinel.scrollIntoView({ block: 'end', behavior: 'auto' })
    } else {
      container.scrollTop = container.scrollHeight + 10000
    }
    requestAnimationFrame(() => { container.style.overflowY = '' })
    isNearBottomRef.current = true
    setIsNearBottom(true)
  }, [])

  // rAF-coalesced auto-follow (for ResizeObserver / content change paths)
  const scrollToBottom = useCallback(() => {
    if (pendingScrollFrameRef.current !== null) {
      return
    }
    pendingScrollFrameRef.current = requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      snapToBottom()
    })
  }, [snapToBottom])

  const executeAutoFollow = useCallback(() => {
    scrollToBottom()
    isNearBottomRef.current = true
    setIsNearBottom(true)
  }, [scrollToBottom])

  const runContiguousTimelineBackfill = useCallback(async (agentIdToRefresh: string) => {
    return refreshTimelineLatestInCache(queryClient, agentIdToRefresh, {
      mode: 'contiguous',
      maxNewerPages: RESUME_TIMELINE_BACKFILL_MAX_NEWER_PAGES,
    })
  }, [queryClient])

  const repinAndJumpToBottom = useCallback(() => {
    logScrollSnap('repinAndJumpToBottom')
    autoScrollPinnedRef.current = true
    forceScrollOnNextUpdateRef.current = true
    setAutoScrollPinned(true)
    jumpToBottom()
    scrollToBottom()
  }, [jumpToBottom, scrollToBottom, setAutoScrollPinned])

  const syncLatestTimeline = useCallback(async (
    agentIdToRefresh: string,
    { repinToLatest }: { repinToLatest: boolean },
  ) => {
    await runContiguousTimelineBackfill(agentIdToRefresh)
    if (activeAgentIdRef.current !== agentIdToRefresh) {
      return
    }
    if (!repinToLatest) {
      return
    }
    repinAndJumpToBottom()
  }, [repinAndJumpToBottom, runContiguousTimelineBackfill])

  const triggerResumeTimelineBackfill = useCallback(() => {
    const currentAgentId = activeAgentIdRef.current
    if (!currentAgentId || !allowAgentRefreshRef.current) {
      return
    }
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return
    }
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      return
    }
    if (resumeBackfillInFlightRef.current) {
      return
    }

    const runId = resumeBackfillRunIdRef.current + 1
    resumeBackfillRunIdRef.current = runId
    const shouldRepin = pinnedAtSuspendRef.current

    let inFlight: Promise<void> | null = null
    inFlight = syncLatestTimeline(currentAgentId, { repinToLatest: shouldRepin }).catch((error) => {
      console.error('Failed to backfill timeline after resume:', error)
    }).finally(() => {
      if (resumeBackfillRunIdRef.current !== runId) {
        return
      }
      if (resumeBackfillInFlightRef.current === inFlight) {
        resumeBackfillInFlightRef.current = null
      }
    })

    resumeBackfillInFlightRef.current = inFlight
  }, [syncLatestTimeline])

  usePageLifecycle(
    {
      onSuspend: () => {
        pinnedAtSuspendRef.current = autoScrollPinnedRef.current
      },
      onResume: () => {
        triggerResumeTimelineBackfill()
      },
    },
    { resumeThrottleMs: 4000 },
  )

  // Keep track of composer height to adjust scroll when it changes
  const prevComposerHeight = useRef<number | null>(null)

  useEffect(() => {
    const composer = document.getElementById('agent-composer-shell')
    const container = document.getElementById('timeline-shell')
    if (!composer || !container) return

    const observer = new ResizeObserver((entries) => {
      const height = entries[0].borderBoxSize?.[0]?.blockSize ?? entries[0].contentRect.height
      const shouldFollow = shouldAutoFollowTimeline()

      if (prevComposerHeight.current !== null) {
        const delta = height - prevComposerHeight.current
        // If composer grew and we're at the bottom, scroll down to keep content visible
        if (delta > 0 && shouldFollow) {
          logScrollSnap('composerResize:adjustForGrowth', { delta, height })
          container.scrollTop += delta
        }
      }

      prevComposerHeight.current = height

      // If pinned, ensure we stay at the bottom
      if (shouldFollow) {
        logScrollSnap('composerResize:autoFollow', { height })
        executeAutoFollow()
        return
      }
      syncNearBottomState(container)
    })

    observer.observe(composer)
    return () => observer.disconnect()
  }, [executeAutoFollow, shouldAutoFollowTimeline, syncNearBottomState])

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const captureTimelineRef = useCallback((node: HTMLDivElement | null) => {
    timelineRef.current = node
    syncTimelineScrollability(node)
    setTimelineNode(node)
  }, [syncTimelineScrollability])

  // Observe timeline changes (e.g. images loading, new DOM elements) to keep pinned to bottom
  useEffect(() => {
    if (!timelineNode) {
      setTimelineCanScrollForOlder(false)
      return
    }
    const inner = document.getElementById('timeline-events')

    const observer = new ResizeObserver(() => {
      syncTimelineScrollability(timelineNode)
      const shouldFollow = shouldAutoFollowTimeline()
      // If pinned, ensure we stay at the bottom when content changes
      // Skip while user is actively touching to prevent scroll fighting on mobile
      // Uses rAF-coalesced scrollToBottom to avoid ResizeObserver feedback loops
      if (shouldFollow) {
        logScrollSnap('timelineResize:autoFollow', {
          autoScrollPinned: autoScrollPinnedRef.current,
          isNearBottomRef: isNearBottomRef.current,
        })
        executeAutoFollow()
        return
      }
      syncNearBottomState(timelineNode)
    })

    observer.observe(timelineNode)
    if (inner) {
      observer.observe(inner)
    }
    return () => observer.disconnect()
  }, [executeAutoFollow, syncTimelineScrollability, timelineNode, shouldAutoFollowTimeline, syncNearBottomState])

  useEffect(() => () => {
    if (pendingScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingScrollFrameRef.current)
    }
  }, [])

  useEffect(() => () => {
    if (autoRepinTimeoutRef.current !== null) {
      window.clearTimeout(autoRepinTimeoutRef.current)
    }
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
      logScrollSnap('initialScroll:newAgentPin')
      setAutoScrollPinned(true)
      return
    }
    if (!initialLoading && timelineEvents.length && !didInitialScrollRef.current) {
      didInitialScrollRef.current = true
      logScrollSnap('initialScroll:firstLoadPin', { eventCount: timelineEvents.length })
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
      logScrollSnap('layoutEffect:forceJump')
      forceScrollOnNextUpdateRef.current = false
      shouldScrollOnNextUpdateRef.current = false
      jumpToBottom()
    } else if (shouldScrollOnNextUpdateRef.current) {
      // Auto scroll (new content while pinned) — use lightweight snap to avoid layout thrashing
      shouldScrollOnNextUpdateRef.current = false
      if (!userTouchActiveRef.current) {
        logScrollSnap('layoutEffect:autoSnap', {
          autoScrollPinned: autoScrollPinnedRef.current,
        })
        snapToBottom()
      } else {
        logScrollSnap('layoutEffect:autoSnapSuppressedByTouch')
      }
    }
  }, [
    jumpToBottom,
    snapToBottom,
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

  const rosterContextMismatch = Boolean(
    effectiveContext
      && rosterQuery.data?.context
      && !sameConsoleContext(rosterQuery.data.context, effectiveContext),
  )
  const rosterAgents = useMemo(
    () => (contextReady && !rosterContextMismatch ? rosterQuery.data?.agents ?? [] : []),
    [contextReady, rosterContextMismatch, rosterQuery.data?.agents],
  )
  const activeRosterMeta = useMemo(
    () => rosterAgents.find((agent) => agent.id === activeAgentId) ?? null,
    [activeAgentId, rosterAgents],
  )
  const desiredSocketSubscriptions = useRecentAgentSubscriptions({
    activeAgentId,
    liveAgentId,
    agentContextReady,
    contextReady,
    context: effectiveContext,
    rosterAgents,
  })
  const socketSnapshot = useAgentChatSocket(desiredSocketSubscriptions, {
    contextOverride: contextReady ? effectiveContext : null,
    onCreditEvent: handleCreditEvent,
    onAgentProfileEvent: handleAgentProfileEvent,
  })
  useEffect(() => {
    if (!agentContextReady) return
    if (!activeAgentId) return
    const pendingMeta = pendingAgentMetaRef.current
    const resolvedPendingMeta = pendingMeta && pendingMeta.agentId === activeAgentId ? pendingMeta : null
    const activeRosterSignupPreviewState = activeRosterMeta?.signupPreviewState ?? 'none'
    const activeRosterPlanningState = activeRosterMeta?.planningState ?? 'skipped'
    pendingAgentMetaRef.current = null
    setAgentId(activeAgentId, {
      agentColorHex: resolvedPendingMeta?.agentColorHex ?? agentColor,
      agentName: resolvedPendingMeta?.agentName ?? agentName,
      agentAvatarUrl: resolvedPendingMeta?.agentAvatarUrl ?? agentAvatarUrl,
      processingActive: resolvedPendingMeta?.processingActive,
      signupPreviewState: resolvedPendingMeta?.signupPreviewState ?? activeRosterSignupPreviewState,
      planningState: resolvedPendingMeta?.planningState ?? activeRosterPlanningState,
    })
  }, [
    activeAgentId,
    activeRosterMeta?.planningState,
    activeRosterMeta?.signupPreviewState,
    agentAvatarUrl,
    agentColor,
    agentName,
    setAgentId,
    agentContextReady,
  ])
  const storeAgentName = isStoreSynced ? storedAgentName : null
  const storeResolvedAvatarUrl = isStoreSynced ? storedAgentAvatarUrl : null
  const storeAgentColor = isStoreSynced ? agentColorHex : null
  const resolvedAgentName = storeAgentName ?? activeRosterMeta?.name ?? agentName ?? null
  const resolvedAvatarUrl = storeResolvedAvatarUrl ?? activeRosterMeta?.avatarUrl ?? agentAvatarUrl ?? null
  const resolvedAgentColorHex = storeAgentColor ?? activeRosterMeta?.displayColorHex ?? agentColor ?? null
  const pendingAgentEmail = activeAgentId ? pendingAgentEmails[activeAgentId] ?? null : null
  const resolvedAgentEmail = activeRosterMeta?.email ?? pendingAgentEmail ?? agentEmail ?? null
  const resolvedAgentSms = activeRosterMeta?.sms ?? agentSms ?? null
  const effectivePlanningState = isStoreSynced ? planningState : (activeRosterMeta?.planningState ?? 'skipped')
  const resolvedIsOrgOwned = activeRosterMeta?.isOrgOwned ?? false
  const activeIsCollaborator = activeRosterMeta?.isCollaborator ?? (isCollaborator ?? false)
  const activeCanManageAgent = activeRosterMeta?.canManageAgent ?? !activeIsCollaborator
  const activeCanManageCollaborators = activeRosterMeta?.canManageCollaborators ?? (canManageCollaborators ?? true)
  const hasAgentReply = useMemo(() => hasAgentResponse(timelineEvents), [timelineEvents])
  const effectiveSignupPreviewState = useMemo<SignupPreviewState>(() => {
    if (
      signupPreviewState === 'awaiting_first_reply_pause'
      && !initialLoading
      && !timelineProcessingActive
      && (!timelineAwaitingResponse || hasAgentReply)
    ) {
      return 'awaiting_signup_completion'
    }
    return signupPreviewState
  }, [hasAgentReply, initialLoading, signupPreviewState, timelineAwaitingResponse, timelineProcessingActive])
  const showSignupPreviewPanel = (
    !isNewAgent
    && !resolvedIsOrgOwned
    && personalSignupPreviewAvailable
    && effectiveSignupPreviewState !== 'none'
  )
  const previewActionState = useMemo<SignupPreviewState>(() => {
    if (effectiveSignupPreviewState !== 'none') {
      return effectiveSignupPreviewState
    }
    return (
      rosterAgents.find((agent) => (agent.signupPreviewState ?? 'none') !== 'none')?.signupPreviewState
      ?? 'none'
    )
  }, [effectiveSignupPreviewState, rosterAgents])
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
  const [createAgentTrialOnboarding, setCreateAgentTrialOnboarding] = useState<TrialOnboardingTarget | null>(null)
  const [sendMessageError, setSendMessageError] = useState<string | null>(null)
  const [stopProcessingBusy, setStopProcessingBusy] = useState(false)
  const [stopProcessingRequested, setStopProcessingRequested] = useState(false)
  const [skipPlanningBusy, setSkipPlanningBusy] = useState(false)
  const [spawnIntent, setSpawnIntent] = useState<AgentSpawnIntent | null>(null)
  const [spawnIntentStatus, setSpawnIntentStatus] = useState<SpawnIntentStatus>('idle')
  const [idleInsightsAgentId, setIdleInsightsAgentId] = useState<string | null>(null)
  const [idleInsightsPending, setIdleInsightsPending] = useState(false)
  const spawnIntentAutoSubmittedRef = useRef(false)
  const spawnIntentRequestIdRef = useRef(0)
  const agentFirstName = useMemo(() => deriveFirstName(resolvedAgentName), [resolvedAgentName])
  const latestKanbanSnapshot = useMemo(() => getLatestKanbanSnapshot(timelineEvents), [timelineEvents])
  const hasSelectedAgent = Boolean(activeAgentId)
  const allowAgentRefresh = hasSelectedAgent && !contextSwitching && agentContextReady && !rosterContextMismatch
  useEffect(() => {
    allowAgentRefreshRef.current = allowAgentRefresh
  }, [allowAgentRefresh])
  const rosterLoading = rosterQuery.isLoading || !agentContextReady || rosterContextMismatch
  const { allowAgentPanelRequests } = useAgentPanelRequestsEnabled({
    activeAgentId,
    isNewAgent,
    rosterLoading: rosterQuery.isLoading,
    allowAgentRefresh,
    rosterAgents,
  })
  const {
    data: quickSettingsPayload,
    isLoading: quickSettingsLoading,
    error: quickSettingsError,
    refetch: refetchQuickSettings,
    updateQuickSettings,
    updating: quickSettingsUpdating,
  } = useAgentQuickSettings(activeAgentId, { enabled: allowAgentPanelRequests })
  const {
    data: addonsPayload,
    refetch: refetchAddons,
    updateAddons,
    updating: addonsUpdating,
  } = useAgentAddons(activeAgentId, { enabled: allowAgentPanelRequests })
  const contextSwitcher = useMemo(() => {
    if (!showContextSwitcher) {
      return null
    }
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
  }, [contextData, contextError, contextSwitching, showContextSwitcher, switchContext])
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
    setCreateAgentTrialOnboarding(null)
    setSendMessageError(null)
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
  const onboardingTarget = createAgentTrialOnboarding ?? spawnIntent?.onboarding_target ?? null
  const requiresTrialPlanSelection = Boolean(
    createAgentTrialOnboarding || spawnIntent?.requires_plan_selection,
  )

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

  // Keep favicon synced to the active agent's color in live chat contexts.
  useEffect(() => {
    const head = document.head
    if (!head) {
      return
    }

    if (isSelectionView || !activeAgentId) {
      head.querySelector('link[data-agent-favicon="true"]')?.remove()
      head.querySelector('link[data-agent-favicon-shortcut="true"]')?.remove()
      const themeColorMeta = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null
      if (themeColorMeta && initialThemeColorRef.current !== null) {
        themeColorMeta.content = initialThemeColorRef.current
      }
      return
    }

    const colorHex = normalizeHexColor(resolvedAgentColorHex)
    let cancelled = false

    const resolveFishSvg = async (): Promise<string> => {
      if (fishFaviconSvgRef.current) {
        return fishFaviconSvgRef.current
      }
      if (!fishFaviconSvgPromiseRef.current) {
        fishFaviconSvgPromiseRef.current = fetch('/static/images/gobii-fish.svg', { credentials: 'same-origin' })
          .then(async (response) => {
            if (!response.ok) {
              throw new Error(`Failed to load Gobii fish SVG favicon source (${response.status})`)
            }
            return response.text()
          })
          .then((svgText) => {
            fishFaviconSvgRef.current = svgText
            return svgText
          })
      }
      return fishFaviconSvgPromiseRef.current
    }

    const applyFaviconHref = (href: string) => {
      if (cancelled) {
        return
      }

      let icon = head.querySelector('link[data-agent-favicon="true"]') as HTMLLinkElement | null
      if (!icon) {
        icon = document.createElement('link')
        icon.setAttribute('data-agent-favicon', 'true')
        icon.rel = 'icon'
        icon.type = 'image/svg+xml'
        head.appendChild(icon)
      }
      icon.href = href

      let shortcut = head.querySelector('link[data-agent-favicon-shortcut="true"]') as HTMLLinkElement | null
      if (!shortcut) {
        shortcut = document.createElement('link')
        shortcut.setAttribute('data-agent-favicon-shortcut', 'true')
        shortcut.rel = 'shortcut icon'
        shortcut.type = 'image/svg+xml'
        head.appendChild(shortcut)
      }
      shortcut.href = href
    }

    void resolveFishSvg()
      .then((svgText) => buildFishSvgFaviconDataUrl(svgText, colorHex))
      .then((faviconHref) => {
        applyFaviconHref(faviconHref)
      })
      .catch(() => {
        applyFaviconHref(buildFallbackFaviconDataUrl(colorHex))
      })

    const themeColorMeta = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null
    if (themeColorMeta && initialThemeColorRef.current === null) {
      initialThemeColorRef.current = themeColorMeta.content
    }
    if (themeColorMeta) {
      themeColorMeta.content = colorHex
    }
    return () => {
      cancelled = true
    }
  }, [activeAgentId, isSelectionView, resolvedAgentColorHex])

  useEffect(() => {
    return () => {
      const head = document.head
      if (!head) {
        return
      }
      head.querySelector('link[data-agent-favicon="true"]')?.remove()
      head.querySelector('link[data-agent-favicon-shortcut="true"]')?.remove()

      const themeColorMeta = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null
      if (themeColorMeta && initialThemeColorRef.current !== null) {
        themeColorMeta.content = initialThemeColorRef.current
      }
    }
  }, [])

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
      processingActive: false,
      lastInteractionAt: null,
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
  const sortedSidebarAgents = useMemo(
    () => sortRosterEntries(rosterAgentsWithActiveMeta, agentRosterSortMode),
    [agentRosterSortMode, rosterAgentsWithActiveMeta],
  )
  const sidebarAgents = useMemo(() => {
    if (!contextReady) {
      return []
    }
    if (!activeAgentId) {
      return sortedSidebarAgents
    }
    const hasActive = sortedSidebarAgents.some((agent) => agent.id === activeAgentId)
    if (hasActive || !fallbackAgent) {
      return sortedSidebarAgents
    }
    return [fallbackAgent, ...sortedSidebarAgents]
  }, [activeAgentId, contextReady, fallbackAgent, sortedSidebarAgents])

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
  const requestedAgentDeleted = Boolean(
    routeAgentId
      && activeAgentId === routeAgentId
      && rosterQuery.data?.requestedAgentStatus === 'deleted',
  )
  const agentNotFound = useMemo(() => {
    if (!contextReady) return false
    // Not applicable for new agent creation
    if (isNewAgent) return false
    if (requestedAgentDeleted) return true
    // Wait for both roster and initial load to complete
    if (rosterQuery.isLoading || initialLoading || rosterContextMismatch) return false
    // Check if agent exists in roster
    const agentInRoster = rosterAgents.some((agent) => agent.id === activeAgentId)
    // If there's an error loading the agent AND it's not in the roster, it's not found
    // Also consider not found if roster loaded but agent isn't there and we have an error
    if (!agentInRoster && timelineQuery.error) return true
    // If roster loaded, agent isn't in roster, and we have no events (failed to load), mark as not found
    if (!agentInRoster && !initialLoading && timelineEvents.length === 0) return true
    return false
  }, [
    activeAgentId,
    contextReady,
    timelineQuery.error,
    initialLoading,
    isNewAgent,
    requestedAgentDeleted,
    rosterAgents,
    rosterContextMismatch,
    rosterQuery.isLoading,
    timelineEvents.length,
  ])

  useEffect(() => {
    if (!switchingAgentId) {
      return
    }
    if (!initialLoading) {
      setSwitchingAgentId(null)
    }
  }, [initialLoading, switchingAgentId])

  const selectedAgentBillingStatus = addonsPayload?.status?.billing ?? null
  const currentContextBillingStatus = rosterQuery.data?.billingStatus ?? null
  const sendMessageDisabledReason = !isNewAgent && selectedAgentBillingStatus?.delinquent
    ? resolveSendMessageDisabledMessage()
    : null
  const previewCreateAgentBlocked = !currentContextBillingStatus?.delinquent && personalSignupPreviewAvailable
  const createAgentDisabledReason = currentContextBillingStatus?.delinquent
    ? resolveCreateAgentDisabledMessage(
      currentContextBillingStatus.reason,
      currentContextBillingStatus.actionable,
    )
    : previewCreateAgentBlocked
      ? 'Finish signup to create another agent. Your preview can continue once you start a plan.'
      : null

  const trackSignupPreviewActionBlocked = useCallback((
    action: 'new_agent' | 'settings' | 'collaborate',
    location: 'sidebar' | 'empty_state' | 'not_found' | 'banner_desktop' | 'banner_mobile' | 'insight_card',
  ) => {
    track(AnalyticsEvent.SIGNUP_PREVIEW_ACTION_BLOCKED, {
      action,
      location,
      agentId: activeAgentId ?? undefined,
      signupPreviewState: previewActionState,
    })
  }, [activeAgentId, previewActionState])

  const handleBlockedCreateAgent = useCallback((location: 'sidebar' | 'empty_state' | 'not_found') => {
    trackSignupPreviewActionBlocked('new_agent', location)
  }, [trackSignupPreviewActionBlocked])

  const handleBlockedSettingsClick = useCallback((location: 'banner_desktop' | 'banner_mobile') => {
    trackSignupPreviewActionBlocked('settings', location)
  }, [trackSignupPreviewActionBlocked])

  const handleBlockedCollaborateClick = useCallback((
    location: 'banner_desktop' | 'banner_mobile' | 'insight_card',
  ) => {
    trackSignupPreviewActionBlocked('collaborate', location)
  }, [trackSignupPreviewActionBlocked])

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
        processingActive: agent.processingActive,
        signupPreviewState: agent.signupPreviewState ?? 'none',
        planningState: agent.planningState ?? 'skipped',
      }
      locallySelectedAgentIdsRef.current.add(agent.id)
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
    if (createAgentDisabledReason) {
      return
    }
    // Use the prop callback if provided (for client-side navigation in ImmersiveApp)
    if (onCreateAgent) {
      onCreateAgent()
      return
    }
    // Fall back to full page navigation for console mode
    window.location.assign('/console/agents/create/quick/')
  }, [createAgentDisabledReason, onCreateAgent])

  const handleJumpToLatest = useCallback(() => {
    const currentAgentId = activeAgentIdRef.current
    void (async () => {
      if (timelineHasMoreNewer && currentAgentId) {
        await syncLatestTimeline(currentAgentId, { repinToLatest: true })
        return
      }
      repinAndJumpToBottom()
    })()
  }, [repinAndJumpToBottom, syncLatestTimeline, timelineHasMoreNewer])

  const handleComposerFocus = useCallback(() => {
    if (typeof window === 'undefined') return
    const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0
    if (!isTouch) return

    logScrollSnap('composerFocus:repinAndJump')
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

  useEffect(() => {
    if (!showSignupPreviewPanel || !activeAgentId) {
      return
    }
    if (previewEnteredAgentIdsRef.current.has(activeAgentId)) {
      return
    }
    previewEnteredAgentIdsRef.current.add(activeAgentId)
    track(AnalyticsEvent.SIGNUP_PREVIEW_ENTERED, {
      agentId: activeAgentId,
      signupPreviewState: effectiveSignupPreviewState,
      panelMode: effectiveSignupPreviewState === 'awaiting_signup_completion' ? 'paused' : 'working',
      source: SIGNUP_PREVIEW_PANEL_SOURCE,
    })
  }, [activeAgentId, effectiveSignupPreviewState, showSignupPreviewPanel])

  const createNewAgent = useCallback(
    async (
      body: string,
      tier: IntelligenceTierKey,
      charterOverride?: string | null,
      selectedPipedreamAppSlugs?: string[],
    ) => {
      setCreateAgentError(null)
      setCreateAgentTrialOnboarding(null)
      try {
        const preferredContactMethod = spawnFlow ? 'email' : 'web'
        const result = await createAgent(
          body,
          tier,
          charterOverride,
          selectedPipedreamAppSlugs,
          preferredContactMethod,
        )
        const createdAgentName = result.agent_name?.trim() || 'Agent'
        const createdAgentEmail = result.agent_email?.trim() || null
        const createdPlanningState = normalizePlanningState(result.planning_state)
        const createdAgentEntry: AgentRosterEntry = {
          id: result.agent_id,
          name: createdAgentName,
          avatarUrl: null,
          displayColorHex: null,
          isActive: true,
          processingActive: false,
          lastInteractionAt: new Date().toISOString(),
          miniDescription: '',
          shortDescription: '',
          email: createdAgentEmail,
          signupPreviewState: personalSignupPreviewAvailable ? 'awaiting_first_reply_pause' : 'none',
          planningState: createdPlanningState,
        }
        pendingAgentMetaRef.current = {
          agentId: result.agent_id,
          agentName: createdAgentName,
          signupPreviewState: personalSignupPreviewAvailable ? 'awaiting_first_reply_pause' : 'none',
          planningState: createdPlanningState,
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
        if (errorState.requiresTrialPlanSelection) {
          setCreateAgentTrialOnboarding(errorState.trialOnboardingTarget ?? 'agent_ui')
          openUpgradeModal('trial_onboarding', { dismissible: false })
        }
        setCreateAgentError(errorState)
        console.error('Failed to create agent:', err)
      }
    },
    [
      effectiveContext,
      isProprietaryMode,
      onAgentCreated,
      openUpgradeModal,
      personalSignupPreviewAvailable,
      queryClient,
      setPendingAgentEmails,
      spawnFlow,
      trackPendingAvatarRefresh,
    ],
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

  const handleStopProcessing = useCallback(async () => {
    if (!activeAgentId || stopProcessingBusy) {
      return
    }
    setStopProcessingRequested(true)
    setStopProcessingBusy(true)
    setSendMessageError(null)
    try {
      await stopAgentProcessing(activeAgentId)
      void refreshProcessing()
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
    } catch (error) {
      setStopProcessingRequested(false)
      setSendMessageError(safeErrorMessage(error) || 'Unable to stop agent right now.')
    } finally {
      setStopProcessingBusy(false)
    }
  }, [activeAgentId, queryClient, refreshProcessing, stopProcessingBusy])

  const applyPlanningMutationResult = useCallback((
    targetAgentId: string,
    nextPlanningState: PlanningState,
    pendingActionRequests: PendingActionRequest[],
    pendingHumanInputRequests: PendingHumanInputRequest[],
  ) => {
    replacePendingActionRequestsInCache(queryClient, targetAgentId, pendingActionRequests)
    useAgentChatStore.getState().updateAgentIdentity({
      agentId: targetAgentId,
      planningState: nextPlanningState,
    })
    queryClient.setQueryData<InfiniteData<TimelinePage>>(
      timelineQueryKey(targetAgentId),
      (current) => {
        if (!current?.pages?.length) {
          return current
        }
        return {
          ...current,
          pages: current.pages.map((page) => ({
            ...page,
            raw: {
              ...page.raw,
              planning_state: nextPlanningState,
              pending_action_requests: pendingActionRequests,
              pending_human_input_requests: pendingHumanInputRequests,
            },
          })),
        }
      },
    )
    queryClient.setQueriesData<AgentRosterQueryData>(
      { queryKey: ['agent-roster'] },
      (current) => {
        if (!isAgentRosterQueryData(current)) {
          return current
        }
        return {
          ...current,
          agents: current.agents.map((agent) => (
            agent.id === targetAgentId
              ? { ...agent, planningState: nextPlanningState }
              : agent
          )),
        }
      },
    )
  }, [queryClient])

  const handleSkipPlanning = useCallback(async () => {
    if (!activeAgentId || skipPlanningBusy) {
      return
    }
    setSkipPlanningBusy(true)
    setSendMessageError(null)
    try {
      const result = await skipAgentPlanning(activeAgentId)
      applyPlanningMutationResult(
        activeAgentId,
        result.planningState,
        result.pendingActionRequests,
        result.pendingHumanInputRequests,
      )
    } catch (error) {
      setSendMessageError(safeErrorMessage(error) || 'Unable to skip planning right now.')
    } finally {
      setSkipPlanningBusy(false)
    }
  }, [activeAgentId, applyPlanningMutationResult, skipPlanningBusy])

  // Start/stop insight rotation based on processing state
  const isProcessing = allowAgentRefresh && (timelineProcessingActive || timelineAwaitingResponse || (timelineStreaming && !timelineStreaming.done))
  const insightsQueryEnabled = Boolean(
    activeAgentId
    && allowAgentRefresh
    && !isNewAgent
    && !isCollaboratorOnly
    && !showSignupPreviewPanel,
  )
  const insightsQuery = useAgentInsights(activeAgentId, {
    enabled: insightsQueryEnabled && (isProcessing || idleInsightsAgentId === activeAgentId),
  })
  useEffect(() => {
    if (!isProcessing) {
      setStopProcessingRequested(false)
    }
  }, [isProcessing])
  useEffect(() => {
    setStopProcessingRequested(false)
  }, [activeAgentId])
  useEffect(() => {
    if (isProcessing) {
      startInsightRotation()
    } else {
      stopInsightRotation()
    }
  }, [isProcessing, startInsightRotation, stopInsightRotation])
  useEffect(() => {
    if (!activeAgentId || storeAgentId !== activeAgentId || !insightsQuery.data) {
      return
    }
    setInsightsForAgent(activeAgentId, insightsQuery.data.insights)
  }, [activeAgentId, insightsQuery.data, insightsQuery.dataUpdatedAt, setInsightsForAgent, storeAgentId])
  useEffect(() => {
    setIdleInsightsAgentId(null)
    const hasFreshInsights = Boolean(insightsQuery.data && !insightsQuery.isStale)
    if (!activeAgentId || !insightsQueryEnabled || isProcessing || hasFreshInsights) {
      setIdleInsightsPending(false)
      return () => undefined
    }
    setIdleInsightsPending(true)
    const timeout = window.setTimeout(() => {
      if (activeAgentIdRef.current === activeAgentId) {
        setIdleInsightsAgentId(activeAgentId)
      }
    }, INSIGHTS_IDLE_FETCH_DELAY_MS)
    return () => {
      window.clearTimeout(timeout)
      setIdleInsightsPending(false)
    }
  }, [
    activeAgentId,
    insightsQuery.data,
    insightsQuery.isStale,
    insightsQueryEnabled,
    isProcessing,
  ])
  useEffect(() => {
    if (idleInsightsAgentId !== activeAgentId || insightsQuery.isFetching) {
      return
    }
    setIdleInsightsPending(false)
  }, [activeAgentId, idleInsightsAgentId, insightsQuery.isFetching])

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
  const insightsLoading = Boolean(
    !isNewAgent
    && !isCollaboratorOnly
    && !showSignupPreviewPanel
    && (idleInsightsPending || insightsQuery.isFetching)
    && hydratedInsights.length === 0,
  )

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
      if (timelineStreaming.reasoning && !timelineStreaming.content && activeAgentId) {
        void refreshTimelineLatestInCache(queryClient, activeAgentId)
      }
    }
    if (timeoutMs === 0) {
      handleTimeout()
      return () => undefined
    }
    const timeout = window.setTimeout(handleTimeout, timeoutMs)
    return () => window.clearTimeout(timeout)
  }, [
    activeAgentId,
    allowAgentRefresh,
    finalizeStreaming,
    queryClient,
    timelineProcessingActive,
    timelineStreaming,
    streamingLastUpdatedAt,
  ])

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

  const shouldFetchUsageSummary = Boolean(contextReady && (activeAgentId || isNewAgent || isSelectionView))
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
  const bannerBillingStatus = selectedAgentBillingStatus ?? currentContextBillingStatus
  const billingManageUrl = bannerBillingStatus?.manageBillingUrl || contactPackManageUrl || billingUrl
  const highPriorityBanner = useMemo(() => {
    if (!bannerBillingStatus?.delinquent || !bannerBillingStatus?.actionable || !billingManageUrl) {
      return null
    }
    return {
      id: 'billing-delinquent',
      title: 'Billing issue needs attention',
      message: `${resolveBillingAlertMessage(bannerBillingStatus.reason)} Visit billing to fix this and avoid disruption.`,
      actionLabel: 'Open billing',
      actionHref: billingManageUrl,
      dismissible: false,
      tone: 'critical' as const,
    }
  }, [
    bannerBillingStatus?.actionable,
    bannerBillingStatus?.delinquent,
    bannerBillingStatus?.reason,
    billingManageUrl,
  ])
  const selectionMainClassName = `agent-chat-main${selectionSidebarCollapsed ? ' agent-chat-main--sidebar-collapsed' : ''}`
  const selectionSidebarSettings = useMemo(() => ({
    context: effectiveContext,
    viewerEmail: viewerEmail ?? null,
    isProprietaryMode,
    billingUrl,
    taskCredits: taskQuota
      ? {
          usedToday: usageSummary?.metrics.todayCredits?.total ?? null,
          remaining: taskQuota.available,
          resetOn: usageSummary?.period?.resetOn ?? null,
          unlimited: Boolean(taskQuota.total < 0 || taskQuota.available < 0),
        }
      : null,
  }), [
    billingUrl,
    effectiveContext,
    isProprietaryMode,
    taskQuota,
    usageSummary?.metrics.todayCredits?.total,
    usageSummary?.period?.resetOn,
    viewerEmail,
  ])
  const selectionSidebarProps = {
    agents: sidebarAgents,
    favoriteAgentIds,
    activeAgentId: null,
    loading: rosterLoading,
    errorMessage: rosterErrorMessage,
    onSelectAgent: handleSelectAgent,
    onToggleAgentFavorite: handleToggleAgentFavorite,
    onCreateAgent: handleCreateAgent,
    createAgentDisabledReason,
    rosterSortMode: agentRosterSortMode,
    onRosterSortModeChange: handleAgentRosterSortModeChange,
    defaultCollapsed: selectionSidebarCollapsed,
    onToggle: setSelectionSidebarCollapsed,
    contextSwitcher: contextSwitcher ?? undefined,
    settings: selectionSidebarSettings,
  }
  const agentChatPageStyle = useMemo<AgentChatPageStyle>(() => ({
    '--agent-chat-grain-texture': `url("${RESOLVED_NOISE_LIGHT_TEXTURE_URL}")`,
  }), [])
  const renderSelectionLayout = (content: ReactNode) => (
    <div
      className="agent-chat-page agent-chat-page--framed"
      data-processing="false"
      style={agentChatPageStyle}
    >
      <ChatSidebar {...selectionSidebarProps} />
      <main className={selectionMainClassName}>
        <div id="agent-workspace-root">
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            {content}
          </div>
        </div>
      </main>
    </div>
  )

  useEffect(() => {
    if (
      !isNewAgent
      || !createAgentDisabledReason
      || requiresTrialPlanSelection
      || personalSignupPreviewAvailable
      || typeof window === 'undefined'
    ) {
      return
    }
    if (window.location.pathname !== '/app/agents/new') {
      return
    }
    const selectionUrl = `/app/agents${window.location.search}${window.location.hash}`
    window.history.replaceState({}, '', selectionUrl)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [createAgentDisabledReason, isNewAgent, personalSignupPreviewAvailable, requiresTrialPlanSelection])

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
    void createNewAgent(
      pending.body,
      tierToUse,
      pending.charterOverride,
      pending.selectedPipedreamAppSlugs,
    )
  }, [buildGateAnalytics, closeGate, createNewAgent, intelligenceGate])

  const handleSend = useCallback(async (
    body: string,
    attachments: File[] = [],
    charterOverride?: string | null,
    selectedPipedreamAppSlugs?: string[],
  ) => {
    if (!activeAgentId && !isNewAgent) {
      return
    }
    setSendMessageError(null)
    if (sendMessageDisabledReason) {
      return
    }
    const hasMessageContent = body.trim().length > 0 || attachments.length > 0
    if (!hasMessageContent) {
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
        pendingCreateRef.current = {
          body,
          attachments,
          tier: selectedTier,
          charterOverride,
          selectedPipedreamAppSlugs,
        }
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
      await createNewAgent(
        body,
        selectedTier,
        charterOverride,
        selectedPipedreamAppSlugs,
      )
      return
    }
    if (activeAgentId) {
      const sentAt = new Date().toISOString()
      queryClient.setQueriesData<AgentRosterQueryData>(
        { queryKey: ['agent-roster'] },
        (current) => touchRosterEntryLastInteraction(current, activeAgentId, sentAt),
      )
    }
    try {
      await sendMessage(body, attachments)
    } catch (error) {
      setSendMessageError(safeErrorMessage(error))
      throw error
    }
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
    queryClient,
    receiveRealtimeEvent,
    resolvedIntelligenceTier,
    refetchBurnRateSummary,
    scrollToBottom,
    sendMessage,
    sendMessageDisabledReason,
    shouldFetchUsageBurnRate,
  ])

  const handleRespondHumanInputRequest = useCallback(async (
    response:
      | { requestId: string; selectedOptionKey?: string; freeText?: string }
      | { batchId: string; responses: Array<{ requestId: string; selectedOptionKey?: string; freeText?: string }> },
  ) => {
    if (!activeAgentId) {
      return
    }
    if ('responses' in response) {
      const result = await respondToHumanInputRequestsBatch(activeAgentId, {
        responses: response.responses.map((item) => (
          item.selectedOptionKey
            ? { request_id: item.requestId, selected_option_key: item.selectedOptionKey }
            : { request_id: item.requestId, free_text: item.freeText?.trim() ?? '' }
        )),
      })
      replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
      if (result.event) {
        receiveRealtimeEvent(result.event)
      }
    } else if (response.selectedOptionKey) {
      const result = await respondToHumanInputRequest(activeAgentId, response.requestId, {
        selected_option_key: response.selectedOptionKey,
      })
      replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
      if (result.event) {
        receiveRealtimeEvent(result.event)
      }
    } else if (response.freeText && response.freeText.trim().length > 0) {
      const result = await respondToHumanInputRequest(activeAgentId, response.requestId, {
        free_text: response.freeText.trim(),
      })
      replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
      if (result.event) {
        receiveRealtimeEvent(result.event)
      }
    } else {
      return
    }
    if (!autoScrollPinnedRef.current) {
      return
    }
    scrollToBottom()
  }, [activeAgentId, queryClient, receiveRealtimeEvent, scrollToBottom])

  const handleResolveSpawnRequest = useCallback(async (
    decisionApiUrl: string,
    decision: 'approve' | 'decline',
  ) => {
    if (!activeAgentId) {
      return
    }
    const result = await resolveSpawnRequest(decisionApiUrl, { decision })
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
  }, [activeAgentId, queryClient])

  const handleFulfillRequestedSecrets = useCallback(async (
    values: Record<string, string>,
    makeGlobal: boolean,
  ) => {
    if (!activeAgentId) {
      return
    }
    const result = await fulfillRequestedSecrets(activeAgentId, {
      values,
      make_global: makeGlobal,
    })
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
  }, [activeAgentId, queryClient])

  const handleRemoveRequestedSecrets = useCallback(async (secretIds: string[]) => {
    if (!activeAgentId) {
      return
    }
    const result = await removeRequestedSecrets(activeAgentId, { secret_ids: secretIds })
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
  }, [activeAgentId, queryClient])

  const handleResolveContactRequests = useCallback(async (
    responses: Array<{
      requestId: string
      decision: 'approve' | 'decline'
      allowInbound: boolean
      allowOutbound: boolean
      canConfigure: boolean
    }>,
  ) => {
    if (!activeAgentId) {
      return
    }
    const result = await resolveContactRequests(activeAgentId, {
      responses: responses.map((response) => ({
        request_id: response.requestId,
        decision: response.decision,
        allow_inbound: response.allowInbound,
        allow_outbound: response.allowOutbound,
        can_configure: response.canConfigure,
      })),
    })
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
  }, [activeAgentId, queryClient])

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
    if (!contextReady || rosterQuery.isLoading || rosterContextMismatch) {
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
    const sendPromise = handleSend(
      spawnIntent.charter,
      [],
      spawnIntent.charter_override,
      spawnIntent.selected_pipedream_app_slugs,
    )
    sendPromise.finally(() => setSpawnIntentStatus('done'))
  }, [
    contextReady,
    draftIntelligenceTier,
    handleSend,
    isNewAgent,
    llmIntelligence,
    rosterContextMismatch,
    rosterQuery.isLoading,
    spawnFlow,
    spawnIntent,
    spawnIntentStatus,
    requiresTrialPlanSelection,
  ])

  const showSpawnIntentLoader = Boolean(
    spawnFlow && isNewAgent && (spawnIntentStatus === 'loading' || spawnIntentStatus === 'ready'),
  )

  const activeAuditUrl = useMemo(() => {
    if (!activeAgentId) {
      return null
    }
    const rosterEntry = rosterAgents.find((agent) => agent.id === activeAgentId)
    const rosterAuditUrl = rosterEntry?.auditUrl ?? null
    if (rosterAuditUrl) {
      return rosterAuditUrl
    }
    if (auditUrlTemplate) {
      return auditUrlTemplate.replace(AUDIT_URL_TEMPLATE_PLACEHOLDER, activeAgentId)
    }
    // Fall back to the mount node audit URL for the initially-loaded agent (console shell path).
    // Non-staff users will not be served audit URLs, so this still stays staff-only.
    if (activeAgentId === agentId) {
      return auditUrl ?? null
    }
    return null
  }, [activeAgentId, agentId, auditUrl, auditUrlTemplate, rosterAgents])

  const timelineErrorMessage = timelineQuery.error instanceof Error ? timelineQuery.error.message : null
  const topLevelError = (isStoreSynced ? timelineErrorMessage : null) || (sessionStatus === 'error' ? sessionError : null)

  if (isSelectionView) {
    if (!contextReady || rosterLoading) {
      return renderSelectionLayout(
        <div className="flex min-h-[60vh] items-center justify-center">
          <p className="text-sm font-medium text-slate-500">Loading workspace…</p>
        </div>,
      )
    }
    return renderSelectionLayout(
      <div className="flex min-h-full w-full flex-1 flex-col gap-4 pb-6 pt-0">
        {highPriorityBanner ? (
          <HighPriorityBanner
            title={highPriorityBanner.title}
            message={highPriorityBanner.message}
            actionLabel={highPriorityBanner.actionLabel}
            actionHref={highPriorityBanner.actionHref}
            dismissible={highPriorityBanner.dismissible}
            tone={highPriorityBanner.tone}
          />
        ) : null}
        <div className="mx-auto flex w-full max-w-3xl flex-1 items-center justify-center px-4">
          <AgentSelectState
            hasAgents={rosterAgents.length > 0}
            onCreateAgent={handleCreateAgent}
            createAgentDisabledReason={createAgentDisabledReason}
            onBlockedCreateAgent={previewCreateAgentBlocked ? () => handleBlockedCreateAgent('empty_state') : undefined}
          />
        </div>
      </div>,
    )
  }

  // Show a dedicated not-found state with sidebar still accessible
  if (agentNotFound) {
    return renderSelectionLayout(
      <AgentNotFoundState
        deleted={requestedAgentDeleted}
        hasOtherAgents={rosterAgents.length > 0}
        onCreateAgent={handleCreateAgent}
        createAgentDisabledReason={createAgentDisabledReason}
        onBlockedCreateAgent={previewCreateAgentBlocked ? () => handleBlockedCreateAgent('not_found') : undefined}
      />,
    )
  }

  return (
    <div
      className="agent-chat-page agent-chat-page--framed"
      data-processing={isProcessing ? 'true' : 'false'}
      style={agentChatPageStyle}
    >
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
        auditUrl={activeAuditUrl}
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
        favoriteAgentIds={favoriteAgentIds}
        activeAgentId={activeAgentId}
        insightsPanelExpandedPreference={insightsPanelExpandedPreference}
        switchingAgentId={switchingAgentId}
        rosterLoading={rosterLoading}
        rosterError={rosterErrorMessage}
        onSelectAgent={handleSelectAgent}
        onToggleAgentFavorite={handleToggleAgentFavorite}
        onCreateAgent={handleCreateAgent}
        createAgentDisabledReason={createAgentDisabledReason}
        onBlockedCreateAgent={previewCreateAgentBlocked ? handleBlockedCreateAgent : undefined}
        agentRosterSortMode={agentRosterSortMode}
        onAgentRosterSortModeChange={handleAgentRosterSortModeChange}
        onInsightsPanelExpandedPreferenceChange={handleInsightsPanelExpandedPreferenceChange}
        contextSwitcher={contextSwitcher ?? undefined}
        currentContext={effectiveContext}
        sidebarBillingUrl={billingManageUrl}
        sidebarTodayCreditsUsed={usageSummary?.metrics.todayCredits?.total ?? null}
        sidebarCreditsResetOn={usageSummary?.period?.resetOn ?? null}
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
        highPriorityBanner={highPriorityBanner}
        onRefreshAddons={refetchAddons}
        contactPackManageUrl={contactPackManageUrl}
        onShare={canShareCollaborators ? handleOpenCollaboratorInvite : undefined}
        onBlockedSettingsClick={handleBlockedSettingsClick}
        onBlockedCollaborate={handleBlockedCollaborateClick}
        onUpgrade={handleUpgrade}
        composerError={sendMessageError ?? createAgentError?.message ?? null}
        composerErrorShowUpgrade={sendMessageError ? false : Boolean(createAgentError?.showUpgradeCta)}
        composerDisabled={Boolean(sendMessageDisabledReason)}
        composerDisabledReason={sendMessageDisabledReason}
        showSignupPreviewPanel={showSignupPreviewPanel}
        signupPreviewState={effectiveSignupPreviewState}
        planningState={effectivePlanningState}
        onSkipPlanning={handleSkipPlanning}
        skipPlanningBusy={skipPlanningBusy}
        maxAttachmentBytes={maxChatUploadSizeBytes}
        pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
        pipedreamAppSearchUrl={pipedreamAppSearchUrl}
        pendingActionRequests={pendingActionRequests}
        events={timelineEvents}
        displayEvents={displayEvents}
        statusExpansionTargets={statusExpansionTargets}
        realtimeEventCursors={realtimeEventCursors}
        onRealtimeEventAnimationConsumed={consumeRealtimeEventCursor}
        hasMoreOlder={timelineHasMoreOlder}
        hasMoreNewer={timelineHasMoreNewer}
        showOlderLoadButton={showOlderLoadButton}
        oldestCursor={timelineEvents.length ? timelineEvents[0].cursor : null}
        newestCursor={timelineEvents.length ? timelineEvents[timelineEvents.length - 1].cursor : null}
        processingActive={timelineProcessingActive}
        processingStartedAt={timelineProcessingStartedAt}
        awaitingResponse={timelineAwaitingResponse}
        processingWebTasks={timelineProcessingWebTasks}
        nextScheduledAt={timelineNextScheduledAt}
        streaming={timelineStreaming}
        onLoadOlder={requestPreviousPage}
        onSendMessage={handleSend}
        onRespondHumanInputRequest={handleRespondHumanInputRequest}
        onResolveSpawnRequest={handleResolveSpawnRequest}
        onFulfillRequestedSecrets={handleFulfillRequestedSecrets}
        onRemoveRequestedSecrets={handleRemoveRequestedSecrets}
        onResolveContactRequests={handleResolveContactRequests}
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
        insightsLoading={insightsLoading}
        currentInsightIndex={currentInsightIndex}
        onDismissInsight={dismissInsight}
        onInsightIndexChange={setCurrentInsightIndex}
        onPauseChange={setInsightsPaused}
        isInsightsPaused={insightsPaused}
        llmIntelligence={llmIntelligence}
        currentLlmTier={resolvedIntelligenceTier}
        onLlmTierChange={handleIntelligenceChange}
        allowLockedIntelligenceSelection={isNewAgent}
        llmTierSaving={intelligenceBusy}
        llmTierError={intelligenceError}
        onStopProcessing={handleStopProcessing}
        stopProcessingBusy={stopProcessingBusy}
        stopProcessingRequested={stopProcessingRequested}
        spawnIntentLoading={showSpawnIntentLoader}
        starterPromptsDisabled={Boolean(sendMessageDisabledReason)}
      />
    </div>
  )
}
