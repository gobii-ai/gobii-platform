import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type Dispatch,
  type FormEvent,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from 'react'
import { useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { AlertTriangle, Building2, Plus } from 'lucide-react'
import noiseDarkTextureUrl from '../assets/textures/noise-dark.png'

import { createAgent, updateAgent } from '../api/agents'
import {
  stopAgentProcessing,
  fulfillRequestedSecrets,
  removeRequestedSecrets,
  resolveContactRequests,
  resolveSpawnRequest,
  dismissHumanInputRequest,
  respondToHumanInputRequest,
  respondToHumanInputRequestsBatch,
  skipAgentPlanning,
  markLatestAgentMessageRead,
  normalizeAgentMessageReadState,
  type AgentMessageReadState,
} from '../api/agentChat'
import { fetchAgentSpawnIntent, type AgentSpawnIntent } from '../api/agentSpawnIntent'
import {
  parseBooleanPreference,
  parseNullableBooleanPreference,
  updateUserPreferences,
  parseFavoriteAgentIdsPreference,
  USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED,
  USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
} from '../api/userPreferences'
import type { ConsoleContext } from '../api/context'
import { fetchUsageBurnRate, fetchUsageSummary } from '../components/usage/api'
import { AgentChatLayout } from '../components/agentChat/AgentChatLayout'
import { EmbeddedAgentContactRequestsPanel } from '../components/agentChat/EmbeddedAgentContactRequestsPanel'
import { EmbeddedAgentEmailSettingsPanel } from '../components/agentChat/EmbeddedAgentEmailSettingsPanel'
import { EmbeddedAgentFilesPanel } from '../components/agentChat/EmbeddedAgentFilesPanel'
import { EmbeddedAgentSecretRequestsPanel } from '../components/agentChat/EmbeddedAgentSecretRequestsPanel'
import { EmbeddedAgentSettingsPanel } from '../components/agentChat/EmbeddedAgentSettingsPanel'
import { EmbeddedAgentSecretsPanel } from '../components/agentChat/EmbeddedAgentSecretsPanel'
import { AgentIntelligenceGateModal } from '../components/agentChat/AgentIntelligenceGateModal'
import { CollaboratorInviteDialog } from '../components/agentChat/CollaboratorInviteDialog'
import { PublicAgentShareDialog } from '../components/agentChat/PublicAgentShareDialog'
import { ModalForm } from '../components/common/ModalForm'
import { HelpSupportDialog } from '../components/common/HelpSupportDialog'
import { ChatSidebar } from '../components/agentChat/ChatSidebar'
import { HighPriorityBanner } from '../components/agentChat/HighPriorityBanner'
import { type SelectionShellPage } from '../components/agentChat/SelectionShellPageSwitcher'
import { getInitialAgentChatSidebarMode } from '../components/agentChat/sidebarMode'
import { findLatestStatusExpansionTargets } from '../components/agentChat/statusExpansion'
import { parseToolSearchResult } from '../components/agentChat/tooling/searchUtils'
import type { ConnectionStatusTone } from '../components/agentChat/AgentChatBanner'
import { useTimelineScrollController } from '../components/agentChat/useTimelineScrollController'
import { useAgentChatSocket } from '../hooks/useAgentChatSocket'
import { useAgentWebSession } from '../hooks/useAgentWebSession'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { useAgentQuickSettings } from '../hooks/useAgentQuickSettings'
import { useAgentAddons } from '../hooks/useAgentAddons'
import { useAgentInsights } from '../hooks/useAgentInsights'
import { useAgentChatNotifications } from '../hooks/useAgentChatNotifications'
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
import { HttpError } from '../api/http'
import { safeErrorMessage } from '../api/safeErrorMessage'
import type { AgentRosterEntry, AgentRosterSortMode, PlanningState, SignupPreviewState } from '../types/agentRoster'
import type {
  AgentMessageNotification,
  CreditForecast,
  PendingActionRequest,
  PendingHumanInputRequest,
  PlanSnapshot,
  TimelineEvent,
} from '../types/agentChat'
import type { DailyCreditsUpdatePayload } from '../types/dailyCredits'
import type { AgentSetupMetadata } from '../types/insight'
import type { UsageBurnRateResponse, UsageSummaryResponse } from '../components/usage'
import type { IntelligenceTierKey } from '../types/llmIntelligence'
import { track, AnalyticsEvent } from '../util/analytics'
import { parseAgentRosterSortMode, sortRosterEntries } from '../util/agentRosterSort'
import {
  type AgentChatShellSubview,
  buildAgentChatShellPath,
  buildAgentChatShellSelectionPath,
  extractAgentChatShellAgentId,
  getAgentChatShellSubview,
} from '../util/agentChatShellRoutes'
import { storeConsoleContext } from '../util/consoleContextStorage'
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
const SIGNUP_PREVIEW_PANEL_SOURCE = 'signup_preview_panel'
const INSIGHTS_IDLE_FETCH_DELAY_MS = 1200
const RESOLVED_NOISE_DARK_TEXTURE_URL = new URL(noiseDarkTextureUrl, import.meta.url).toString()
const GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY = 'google_sheets_native'
const APOLLO_NATIVE_SYSTEM_SKILL_KEY = 'apollo_native'
const HUBSPOT_NATIVE_SYSTEM_SKILL_KEY = 'hubspot_native'
const DISCORD_NATIVE_SYSTEM_SKILL_KEY = 'discord_native'

function timelineHasSystemSkillEnablement(events: TimelineEvent[], skillKey: string): boolean {
  for (const event of events) {
    if (event.kind !== 'steps') {
      continue
    }
    for (const entry of event.entries) {
      const toolName = (entry.toolName ?? '').toLowerCase()
      if (toolName !== 'search_tools' || entry.result === null || entry.result === undefined) {
        continue
      }
      const outcome = (() => {
        try {
          return parseToolSearchResult(entry.result)
        } catch {
          return null
        }
      })()
      if (!outcome) {
        continue
      }
      const enabledSystemSkills = [
        ...outcome.enabledSystemSkills,
        ...outcome.alreadyEnabledSystemSkills,
      ]
      if (enabledSystemSkills.includes(skillKey)) {
        return true
      }
    }
  }
  return false
}
const SELECTION_SIDEBAR_MODE_STORAGE_KEY = 'gobii:immersive:selection-sidebar-mode'

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

function navigateToAgentChat(agentId: string): void {
  if (typeof window === 'undefined') {
    return
  }
  const nextPath = buildAgentChatShellPath(window.location.pathname, agentId, 'chat')
  const nextUrl = `${nextPath}${window.location.search}${window.location.hash}`
  window.history.pushState({ agentId }, '', nextUrl)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function mergePlanDeliverablesFromCurrentSnapshot(snapshot: PlanSnapshot, currentPlan?: PlanSnapshot | null): PlanSnapshot {
  if (!currentPlan) {
    return snapshot
  }

  const hasSnapshotFiles = (snapshot.files?.length ?? 0) > 0
  const hasSnapshotMessages = (snapshot.messages?.length ?? 0) > 0
  const hasCurrentFiles = (currentPlan.files?.length ?? 0) > 0
  const hasCurrentMessages = (currentPlan.messages?.length ?? 0) > 0

  if (hasSnapshotFiles || hasSnapshotMessages || (!hasCurrentFiles && !hasCurrentMessages)) {
    return snapshot
  }

  return {
    ...snapshot,
    files: currentPlan.files,
    messages: currentPlan.messages,
  }
}

function getLatestPlanSnapshot(events: TimelineEvent[], currentPlan?: PlanSnapshot | null): PlanSnapshot | null {
  // Find the most recent plan event (events are ordered oldest to newest). Fall back
  // to the API snapshot so the panel survives timeline windowing.
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.kind === 'plan') {
      return mergePlanDeliverablesFromCurrentSnapshot(event.snapshot, currentPlan)
    }
  }
  return currentPlan ?? null
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

function formatAccountPauseResumeLabel(resumeAt?: string | null): string | null {
  if (!resumeAt) return null
  const resumeDate = new Date(resumeAt)
  if (Number.isNaN(resumeDate.getTime())) return null
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(resumeDate)
}

function resolveAccountPauseMessage(resumeAt?: string | null): string {
  const resumeLabel = formatAccountPauseResumeLabel(resumeAt)
  if (resumeLabel) {
    return `Your account is paused until ${resumeLabel}.`
  }
  return 'Your account is paused.'
}

function resolveCreateAgentPausedMessage(resumeAt?: string | null): string {
  return `${resolveAccountPauseMessage(resumeAt)} New agent creation is disabled until billing resumes.`
}

function resolveSendMessagePausedMessage(resumeAt?: string | null): string {
  return `${resolveAccountPauseMessage(resumeAt)} Sending new messages is disabled until billing resumes.`
}

function resolveSendMessageDisabledMessage(): string {
  return 'Resolve billing before sending more messages.'
}

function readSelectionSidebarModePreference(): 'collapsed' | 'list' | 'gallery' | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const stored = window.sessionStorage.getItem(SELECTION_SIDEBAR_MODE_STORAGE_KEY)
    if (stored === 'collapsed' || stored === 'list' || stored === 'gallery') {
      return stored
    }
  } catch {
    return null
  }
  return null
}

function writeSelectionSidebarModePreference(mode: 'collapsed' | 'list' | 'gallery'): void {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.sessionStorage.setItem(SELECTION_SIDEBAR_MODE_STORAGE_KEY, mode)
  } catch {
    // Ignore storage failures; shell mode will simply fall back to defaults.
  }
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

function applyRosterMessageReadState(
  current: AgentRosterQueryData | undefined,
  agentId: string,
  readState: AgentMessageReadState,
): AgentRosterQueryData | undefined {
  if (!isAgentRosterQueryData(current) || !current.agents?.length) {
    return current
  }

  let changed = false
  const nextAgents = current.agents.map((agent) => {
    if (agent.id !== agentId) {
      return agent
    }
    if (
      Boolean(agent.hasUnreadAgentMessage) === Boolean(readState.hasUnreadAgentMessage)
      && (agent.latestAgentMessageId ?? null) === (readState.latestAgentMessageId ?? null)
      && (agent.latestAgentMessageAt ?? null) === (readState.latestAgentMessageAt ?? null)
      && (agent.latestAgentMessageReadAt ?? null) === (readState.latestAgentMessageReadAt ?? null)
    ) {
      return agent
    }
    changed = true
    return {
      ...agent,
      ...readState,
    }
  })

  return changed ? { ...current, agents: nextAgents } : current
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
  agentChatNotificationsEnabled?: boolean
  agents: AgentRosterEntry[]
  llmIntelligence?: unknown
}

type AgentRosterPreferenceField =
  | 'agentRosterSortMode'
  | 'favoriteAgentIds'
  | 'insightsPanelExpanded'
  | 'agentChatNotificationsEnabled'

type AgentRosterPreferenceState = {
  agentRosterSortMode: AgentRosterSortMode
  favoriteAgentIds: string[]
  insightsPanelExpanded: boolean | null
  agentChatNotificationsEnabled: boolean
}

type AgentRosterPreferenceComparator<K extends AgentRosterPreferenceField> = (
  currentValue: AgentRosterQueryData[K] | undefined,
  nextValue: AgentRosterPreferenceState[K],
) => boolean

type PersistAgentRosterPreferenceOptions<K extends AgentRosterPreferenceField> = {
  field: K
  preferenceKey: string
  setState: Dispatch<SetStateAction<AgentRosterPreferenceState[K]>>
  parsePersistedValue: (value: unknown) => AgentRosterPreferenceState[K]
  currentValue?: AgentRosterPreferenceState[K] | undefined
  beforePersist?: (() => void) | undefined
  rollbackOnError?: boolean | undefined
  areEqual?: AgentRosterPreferenceComparator<K> | undefined
}

const AGENT_ROSTER_QUERY_KEY = ['agent-roster'] as const

function useHydratedAgentRosterPreference<StateValue>(
  serverValue: unknown,
  hydratedRef: MutableRefObject<boolean>,
  setValue: Dispatch<SetStateAction<StateValue>>,
  parseValue: (value: unknown) => StateValue,
): void {
  useEffect(() => {
    if (serverValue === undefined || hydratedRef.current) {
      return
    }
    hydratedRef.current = true
    setValue(parseValue(serverValue))
  }, [hydratedRef, parseValue, serverValue, setValue])
}

function favoriteAgentIdsPreferenceEquals(
  currentValue: AgentRosterQueryData['favoriteAgentIds'] | undefined,
  nextValue: string[],
): boolean {
  const normalizedCurrentValue = Array.isArray(currentValue)
    ? currentValue.filter((value): value is string => typeof value === 'string')
    : []
  return areStringArraysEqual(normalizedCurrentValue, nextValue)
}

function updateAgentRosterPreferenceInQueryData<K extends AgentRosterPreferenceField>(
  current: AgentRosterQueryData | undefined,
  field: K,
  nextValue: AgentRosterPreferenceState[K],
  areEqual?: AgentRosterPreferenceComparator<K>,
): AgentRosterQueryData | undefined {
  if (!isAgentRosterQueryData(current)) {
    return current
  }
  const comparator: AgentRosterPreferenceComparator<K> = areEqual ?? ((left, right) => Object.is(left, right))
  if (comparator(current[field], nextValue)) {
    return current
  }
  return {
    ...current,
    [field]: nextValue,
  }
}

type AgentChatPageStyle = CSSProperties & Record<'--agent-chat-grain-texture', string>
type SelectionSidebarProps = ComponentProps<typeof ChatSidebar>
type AppShellOpenHandler = (() => void) | undefined
type AppShellDestinationKey = 'billing' | 'usage' | 'apiKeys' | 'profile' | 'organization' | 'secrets' | 'integrations'
type AppShellDestinations = Record<AppShellDestinationKey, string | null>
type AppShellOpenHandlers = Record<AppShellDestinationKey, () => void>

const EMBEDDED_SETTINGS_TITLES: Record<Exclude<AgentChatShellSubview, 'chat'>, string> = {
  settings: 'Agent Settings',
  secrets: 'Agent Secrets',
  'secret-requests': 'Secret Requests',
  email: 'Email Settings',
  files: 'Agent Files',
  'contact-requests': 'Contact Requests',
}

function openAppShellDestination(onOpen: AppShellOpenHandler, url: string | null): void {
  if (onOpen) {
    onOpen()
    return
  }
  if (url && typeof window !== 'undefined') {
    window.location.assign(url)
  }
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

function consoleContextKey(context: ConsoleContext): string {
  return `${context.type}:${context.id}`
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
  agentAvatarUrl?: string | null
  processingActive?: boolean
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
  creditForecast?: CreditForecast | null
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
  nativeIntegrationsUrl?: string | null
  onClose?: () => void
  onCreateAgent?: () => void
  onAgentCreated?: (agentId: string) => void
  showContextSwitcher?: boolean
  persistContextSession?: boolean
  onContextSwitch?: (context: ConsoleContext) => void
  selectionPage?: SelectionShellPage
  selectionShellPanel?: ReactNode
  selectionMainPanel?: ReactNode
  onSelectionPageChange?: (page: SelectionShellPage) => void
  onOpenBilling?: () => void
  onOpenUsage?: () => void
  onOpenApiKeys?: () => void
  onOpenProfile?: () => void
  onOpenOrganization?: () => void
  onOpenSecrets?: () => void
  onOpenIntegrations?: () => void
}

const STREAMING_STALE_MS = 6000
const STREAMING_REFRESH_INTERVAL_MS = 6000
const RESUME_TIMELINE_BACKFILL_MAX_NEWER_PAGES = DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES

export function AgentChatPage({
  agentId,
  agentName,
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
  nativeIntegrationsUrl = null,
  onClose,
  onCreateAgent,
  onAgentCreated,
  showContextSwitcher = false,
  persistContextSession = true,
  onContextSwitch,
  selectionPage = 'agents',
  selectionShellPanel = null,
  selectionMainPanel = null,
  onSelectionPageChange,
  onOpenBilling,
  onOpenUsage,
  onOpenApiKeys,
  onOpenProfile,
  onOpenOrganization,
  onOpenSecrets,
  onOpenIntegrations,
}: AgentChatPageProps) {
  const [shellPathname, setShellPathname] = useState(() => (
    typeof window === 'undefined' ? '' : window.location.pathname
  ))
  const [activeAgentId, setActiveAgentId] = useState<string | null>(agentId ?? null)
  const activeAgentIdRef = useRef<string | null>(activeAgentId)
  const pendingReadMarkerByAgentRef = useRef<Record<string, string>>({})
  const routeAgentId = typeof agentId === 'string' ? agentId : null
  const shellSubview = useMemo(() => getAgentChatShellSubview(shellPathname), [shellPathname])
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
  const pendingCreateRef = useRef<{
    body: string
    attachments: File[]
    tier: IntelligenceTierKey
    charterOverride?: string | null
    selectedPipedreamAppSlugs?: string[]
  } | null>(null)
  const googleSheetsRosterRefreshAgentsRef = useRef<Set<string>>(new Set())
  const previewEnteredAgentIdsRef = useRef<Set<string>>(new Set())
  const [intelligenceGate, setIntelligenceGate] = useState<IntelligenceGateState | null>(null)
  const pendingAgentMetaRef = useRef<AgentSwitchMeta | null>(null)
  const locallySelectedAgentIdsRef = useRef<Set<string>>(new Set())
  const [manuallySelectedContextKey, setManuallySelectedContextKey] = useState<string | null>(null)
  const resetManualContextForExternalAgent = useCallback((nextAgentId: string | null) => {
    if (
      !nextAgentId
      || nextAgentId === activeAgentIdRef.current
      || locallySelectedAgentIdsRef.current.has(nextAgentId)
    ) {
      return
    }
    setManuallySelectedContextKey(null)
  }, [])
  const selectedFromCurrentRoster = Boolean(activeAgentId && locallySelectedAgentIdsRef.current.has(activeAgentId))
  const shouldResolveContextForRouteAgent = (
    !isNewAgent
    && !isSelectionView
    && !manuallySelectedContextKey
    && !selectedFromCurrentRoster
  )
  const contextLookupAgentId = shouldResolveContextForRouteAgent
    ? routeAgentId ?? activeAgentId ?? undefined
    : undefined

  const handleContextSwitched = useCallback(
    (context: ConsoleContext) => {
      setManuallySelectedContextKey(consoleContextKey(context))
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
    createOrganizationContext,
  } = useConsoleContextSwitcher({
    enabled: true,
    forAgentId: contextLookupAgentId,
    onSwitched: handleContextSwitched,
    persistSession: persistContextSession,
  })

  const [switchingAgentId, setSwitchingAgentId] = useState<string | null>(null)
  const [createOrganizationOpen, setCreateOrganizationOpen] = useState(false)
  const [createOrganizationName, setCreateOrganizationName] = useState('')
  const [createOrganizationBusy, setCreateOrganizationBusy] = useState(false)
  const [createOrganizationErrors, setCreateOrganizationErrors] = useState<string[]>([])
  const [selectionSidebarMode, setSelectionSidebarMode] = useState(() => (
    agentId === undefined
      ? (selectionPage === 'agents' ? (readSelectionSidebarModePreference() ?? 'gallery') : 'gallery')
      : getInitialAgentChatSidebarMode()
  ))
  const [pendingAgentEmails, setPendingAgentEmails] = useState<Record<string, string>>({})
  const [creditForecastByAgentId, setCreditForecastByAgentId] = useState<Record<string, CreditForecast | null>>({})
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
    resetManualContextForExternalAgent(agentId ?? null)
    setShellPathname(typeof window === 'undefined' ? '' : window.location.pathname)
    setActiveAgentId(agentId ?? null)
  }, [agentId, resetManualContextForExternalAgent])

  useEffect(() => {
    activeAgentIdRef.current = activeAgentId
  }, [activeAgentId])

  useEffect(() => {
    if (agentId !== undefined) {
      return
    }
    if (selectionPage !== 'agents') {
      if (selectionSidebarMode !== 'gallery') {
        setSelectionSidebarMode('gallery')
      }
      return
    }
    const storedSelectionMode = readSelectionSidebarModePreference()
    if (storedSelectionMode && storedSelectionMode !== selectionSidebarMode) {
      setSelectionSidebarMode(storedSelectionMode)
      return
    }
  }, [agentId, selectionPage])

  useEffect(() => {
    if (agentId !== undefined || selectionPage !== 'agents') {
      return
    }
    const storedSelectionMode = readSelectionSidebarModePreference()
    if (storedSelectionMode && storedSelectionMode !== selectionSidebarMode) {
      return
    }
    writeSelectionSidebarModePreference(selectionSidebarMode)
  }, [agentId, selectionPage, selectionSidebarMode])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const handleShellLocationChange = () => {
      const nextPathname = window.location.pathname
      setShellPathname(nextPathname)
      const nextAgentId = extractAgentChatShellAgentId(nextPathname)
      if (nextAgentId !== activeAgentIdRef.current) {
        resetManualContextForExternalAgent(nextAgentId)
        setSwitchingAgentId(null)
        setActiveAgentId(nextAgentId)
      }
    }

    window.addEventListener('popstate', handleShellLocationChange)
    return () => window.removeEventListener('popstate', handleShellLocationChange)
  }, [resetManualContextForExternalAgent])

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
    if (Object.prototype.hasOwnProperty.call(initialPageResponse, 'credit_forecast')) {
      setCreditForecastByAgentId((current) => ({
        ...current,
        [activeAgentId]: initialPageResponse.credit_forecast ?? null,
      }))
    }
    // Update agent identity from timeline response
    const name = initialPageResponse.agent_name ?? null
    const avatar = initialPageResponse.agent_avatar_url ?? null
    const signupPreviewState = normalizeSignupPreviewState(initialPageResponse.signup_preview_state)
    const planningState = normalizePlanningState(initialPageResponse.planning_state)
    if (name || avatar || signupPreviewState !== 'none' || planningState !== 'skipped') {
      store.updateAgentIdentity({
        agentId: activeAgentId,
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
  const keepTrailingActivityExpanded = Boolean(
    timelineProcessingActive
    || timelineAwaitingResponse
    || (timelineStreaming && !timelineStreaming.done),
  )
  const displayEvents = useMemo(
    () => collapseDetailedStatusRuns(timelineEvents, statusExpansionTargets, { keepTrailingActivityExpanded }),
    [keepTrailingActivityExpanded, timelineEvents, statusExpansionTargets],
  )
  const latestTimelineCursor = timelineEvents.length ? timelineEvents[timelineEvents.length - 1].cursor : ''
  const timelineStreamingVersion = timelineStreaming
    ? [
        timelineStreaming.streamId,
        timelineStreaming.cursor ?? '',
        timelineStreaming.done ? 'done' : 'active',
        timelineStreaming.content.length,
        timelineStreaming.reasoning.length,
      ].join(':')
    : 'none'
  const timelineScrollContentVersion = [
    timelineEvents.length,
    latestTimelineCursor,
    timelineStreamingVersion,
    timelineLoadingOlder ? 'older' : 'older-idle',
    timelineLoadingNewer ? 'newer' : 'newer-idle',
    timelineHasMoreNewer ? 'has-newer' : 'latest',
    timelineHasUnseenActivity ? 'unseen' : 'seen',
    timelineProcessingActive ? 'processing' : 'idle',
    timelineAwaitingResponse ? 'awaiting' : 'settled',
  ].join('|')
  const {
    autoScrollPinnedRef,
    isNearBottom,
    pinAndJumpToBottom,
    requestPreviousPage,
    scrollOnComposerFocus,
    scrollToBottom,
    showOlderLoadButton,
    timelineContentRef,
    timelineRef: captureTimelineRef,
    composerShellRef,
  } = useTimelineScrollController({
    activeAgentId,
    autoScrollPinned,
    contentVersion: timelineScrollContentVersion,
    eventCount: timelineEvents.length,
    fetchPreviousPage: timelineQuery.fetchPreviousPage,
    hasMoreOlder: timelineHasMoreOlder,
    hasPreviousPage: Boolean(timelineQuery.hasPreviousPage),
    initialLoading,
    isFetchPreviousPageError: timelineQuery.isFetchPreviousPageError,
    isFetchingPreviousPage: timelineQuery.isFetchingPreviousPage,
    isNewAgent,
    loadingOlder: timelineLoadingOlder,
    pageCount: timelineQuery.data?.pages?.length ?? 0,
    setAutoScrollPinned,
    switchingAgentId,
  })
  const pinnedAtSuspendRef = useRef(autoScrollPinned)
  const resumeBackfillInFlightRef = useRef<Promise<void> | null>(null)
  const resumeBackfillRunIdRef = useRef(0)
  const allowAgentRefreshRef = useRef(false)

  const [collaboratorInviteOpen, setCollaboratorInviteOpen] = useState(false)
  const [publicShareOpen, setPublicShareOpen] = useState(false)
  const [supportDialogOpen, setSupportDialogOpen] = useState(false)
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
      const hasAvatar = Object.prototype.hasOwnProperty.call(rawPayload, 'agent_avatar_url')
      const hasShortDescription = Object.prototype.hasOwnProperty.call(rawPayload, 'short_description')
      const hasMiniDescription = Object.prototype.hasOwnProperty.call(rawPayload, 'mini_description')
      const hasProcessingActive = Object.prototype.hasOwnProperty.call(rawPayload, 'processing_active')
      const hasSignupPreviewState = Object.prototype.hasOwnProperty.call(rawPayload, 'signup_preview_state')
      const hasPlanningState = Object.prototype.hasOwnProperty.call(rawPayload, 'planning_state')
      const hasCreditForecast = Object.prototype.hasOwnProperty.call(rawPayload, 'credit_forecast')
      const hasUnreadAgentMessage = Object.prototype.hasOwnProperty.call(rawPayload, 'has_unread_agent_message')
      const hasLatestAgentMessageId = Object.prototype.hasOwnProperty.call(rawPayload, 'latest_agent_message_id')
      const hasLatestAgentMessageAt = Object.prototype.hasOwnProperty.call(rawPayload, 'latest_agent_message_at')
      const hasLatestAgentMessageReadAt = Object.prototype.hasOwnProperty.call(rawPayload, 'latest_agent_message_read_at')
      const hasMessageReadState = hasUnreadAgentMessage
        || hasLatestAgentMessageId
        || hasLatestAgentMessageAt
        || hasLatestAgentMessageReadAt
      if (
        !hasName
        && !hasAvatar
        && !hasShortDescription
        && !hasMiniDescription
        && !hasProcessingActive
        && !hasSignupPreviewState
        && !hasPlanningState
        && !hasCreditForecast
        && !hasMessageReadState
      ) {
        return
      }
      if (hasCreditForecast) {
        const forecast = rawPayload.credit_forecast && typeof rawPayload.credit_forecast === 'object'
          ? rawPayload.credit_forecast as CreditForecast
          : null
        setCreditForecastByAgentId((current) => ({
          ...current,
          [agentIdFromEvent]: forecast,
        }))
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
            if (hasMessageReadState) {
              const readState = normalizeAgentMessageReadState(rawPayload)
              if (readState.hasUnreadAgentMessage !== Boolean(next.hasUnreadAgentMessage)) {
                next.hasUnreadAgentMessage = readState.hasUnreadAgentMessage
                changed = true
              }
              if (readState.latestAgentMessageId !== (next.latestAgentMessageId ?? null)) {
                next.latestAgentMessageId = readState.latestAgentMessageId
                changed = true
              }
              if (readState.latestAgentMessageAt !== (next.latestAgentMessageAt ?? null)) {
                next.latestAgentMessageAt = readState.latestAgentMessageAt
                changed = true
              }
              if (readState.latestAgentMessageReadAt !== (next.latestAgentMessageReadAt ?? null)) {
                next.latestAgentMessageReadAt = readState.latestAgentMessageReadAt
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
  const [agentChatNotificationsEnabled, setAgentChatNotificationsEnabled] = useState<boolean>(() => (
    rosterQuery.data?.agentChatNotificationsEnabled === undefined
      ? false
      : parseBooleanPreference(rosterQuery.data.agentChatNotificationsEnabled)
  ))
  const hasHydratedAgentRosterSortModeRef = useRef(false)
  const hasHydratedInsightsPanelExpandedPreferenceRef = useRef(false)
  const hasHydratedAgentChatNotificationsEnabledRef = useRef(false)
  const notificationPermissionPromptAttemptedRef = useRef(false)

  useHydratedAgentRosterPreference(
    rosterQuery.data?.agentRosterSortMode,
    hasHydratedAgentRosterSortModeRef,
    setAgentRosterSortMode,
    parseAgentRosterSortMode,
  )

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

  useHydratedAgentRosterPreference(
    rosterQuery.data?.insightsPanelExpanded,
    hasHydratedInsightsPanelExpandedPreferenceRef,
    setInsightsPanelExpandedPreference,
    parseNullableBooleanPreference,
  )

  useHydratedAgentRosterPreference(
    rosterQuery.data?.agentChatNotificationsEnabled,
    hasHydratedAgentChatNotificationsEnabledRef,
    setAgentChatNotificationsEnabled,
    parseBooleanPreference,
  )

  const updateAgentRosterPreferenceInCache = useCallback(
    function updateAgentRosterPreferenceInCache<K extends AgentRosterPreferenceField>(
      field: K,
      nextValue: AgentRosterPreferenceState[K],
      areEqual?: AgentRosterPreferenceComparator<K>,
    ) {
      queryClient.setQueriesData<AgentRosterQueryData>(
        { queryKey: AGENT_ROSTER_QUERY_KEY },
        (current) => updateAgentRosterPreferenceInQueryData(current, field, nextValue, areEqual),
      )
    },
    [queryClient],
  )

  const persistAgentRosterPreference = useCallback(
    function persistAgentRosterPreference<K extends AgentRosterPreferenceField>(
      nextValue: AgentRosterPreferenceState[K],
      {
        field,
        preferenceKey,
        setState,
        parsePersistedValue,
        currentValue,
        beforePersist,
        rollbackOnError = true,
        areEqual,
      }: PersistAgentRosterPreferenceOptions<K>,
    ) {
      setState(nextValue)
      updateAgentRosterPreferenceInCache(field, nextValue, areEqual)
      beforePersist?.()

      void updateUserPreferences({
        preferences: {
          [preferenceKey]: nextValue,
        },
      }).then((response) => {
        const persistedValue = parsePersistedValue(response.preferences[preferenceKey])
        setState(persistedValue)
        updateAgentRosterPreferenceInCache(field, persistedValue, areEqual)
      }).catch(() => {
        if (rollbackOnError && currentValue !== undefined) {
          setState(currentValue)
          updateAgentRosterPreferenceInCache(field, currentValue, areEqual)
        }
      })
    },
    [updateAgentRosterPreferenceInCache],
  )

  const handleAgentRosterSortModeChange = useCallback(
    (nextSortMode: AgentRosterSortMode) => {
      persistAgentRosterPreference(nextSortMode, {
        field: 'agentRosterSortMode',
        preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE,
        setState: setAgentRosterSortMode,
        parsePersistedValue: parseAgentRosterSortMode,
        currentValue: agentRosterSortMode,
      })
    },
    [agentRosterSortMode, persistAgentRosterPreference],
  )

  const handleToggleAgentFavorite = useCallback(
    (agentId: string) => {
      const nextFavoriteAgentIds = favoriteAgentIds.includes(agentId)
        ? favoriteAgentIds.filter((candidateId) => candidateId !== agentId)
        : [...favoriteAgentIds, agentId]

      persistAgentRosterPreference(nextFavoriteAgentIds, {
        field: 'favoriteAgentIds',
        preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
        setState: setFavoriteAgentIds,
        parsePersistedValue: parseFavoriteAgentIdsPreference,
        currentValue: favoriteAgentIds,
        areEqual: favoriteAgentIdsPreferenceEquals,
      })
    },
    [favoriteAgentIds, persistAgentRosterPreference],
  )

  const handleInsightsPanelExpandedPreferenceChange = useCallback(
    (nextInsightsPanelExpanded: boolean) => {
      persistAgentRosterPreference(nextInsightsPanelExpanded, {
        field: 'insightsPanelExpanded',
        preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED,
        setState: setInsightsPanelExpandedPreference,
        parsePersistedValue: parseNullableBooleanPreference,
        currentValue: insightsPanelExpandedPreference,
      })
    },
    [insightsPanelExpandedPreference, persistAgentRosterPreference],
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

  useEffect(() => {
    resumeBackfillRunIdRef.current += 1
    resumeBackfillInFlightRef.current = null
    pinnedAtSuspendRef.current = autoScrollPinnedRef.current
  }, [activeAgentId])

  useEffect(() => {
    setCollaboratorInviteOpen(false)
  }, [activeAgentId])

  const runContiguousTimelineBackfill = useCallback(async (agentIdToRefresh: string) => {
    return refreshTimelineLatestInCache(queryClient, agentIdToRefresh, {
      mode: 'contiguous',
      maxNewerPages: RESUME_TIMELINE_BACKFILL_MAX_NEWER_PAGES,
    })
  }, [queryClient])

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
    pinAndJumpToBottom()
  }, [pinAndJumpToBottom, runContiguousTimelineBackfill])

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
  const rosterGoogleSheetsDriveTabEnabled = Boolean(
    activeRosterMeta?.enabledSystemSkills?.includes(GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY),
  )
  const rosterApolloNativeTabEnabled = Boolean(
    activeRosterMeta?.enabledSystemSkills?.includes(APOLLO_NATIVE_SYSTEM_SKILL_KEY),
  )
  const rosterHubSpotNativeTabEnabled = Boolean(
    activeRosterMeta?.enabledSystemSkills?.includes(HUBSPOT_NATIVE_SYSTEM_SKILL_KEY),
  )
  const rosterDiscordNativeTabEnabled = Boolean(
    activeRosterMeta?.enabledSystemSkills?.includes(DISCORD_NATIVE_SYSTEM_SKILL_KEY),
  )
  const liveGoogleSheetsDriveTabEnabled = useMemo(
    () => Boolean(activeAgentId && timelineHasSystemSkillEnablement(timelineEvents, GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY)),
    [activeAgentId, timelineEvents],
  )
  const liveApolloNativeTabEnabled = useMemo(
    () => Boolean(activeAgentId && timelineHasSystemSkillEnablement(timelineEvents, APOLLO_NATIVE_SYSTEM_SKILL_KEY)),
    [activeAgentId, timelineEvents],
  )
  const liveHubSpotNativeTabEnabled = useMemo(
    () => Boolean(activeAgentId && timelineHasSystemSkillEnablement(timelineEvents, HUBSPOT_NATIVE_SYSTEM_SKILL_KEY)),
    [activeAgentId, timelineEvents],
  )
  const liveDiscordNativeTabEnabled = useMemo(
    () => Boolean(activeAgentId && timelineHasSystemSkillEnablement(timelineEvents, DISCORD_NATIVE_SYSTEM_SKILL_KEY)),
    [activeAgentId, timelineEvents],
  )
  const googleSheetsDriveTabEnabled = rosterGoogleSheetsDriveTabEnabled || liveGoogleSheetsDriveTabEnabled
  const apolloNativeTabEnabled = rosterApolloNativeTabEnabled || liveApolloNativeTabEnabled
  const hubspotNativeTabEnabled = rosterHubSpotNativeTabEnabled || liveHubSpotNativeTabEnabled
  const discordNativeTabEnabled = rosterDiscordNativeTabEnabled || liveDiscordNativeTabEnabled
  useEffect(() => {
    if (!activeAgentId || (!liveGoogleSheetsDriveTabEnabled && !liveApolloNativeTabEnabled && !liveHubSpotNativeTabEnabled && !liveDiscordNativeTabEnabled)) {
      return
    }
    const refreshKey = [
      activeAgentId,
      liveGoogleSheetsDriveTabEnabled ? GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY : '',
      liveApolloNativeTabEnabled ? APOLLO_NATIVE_SYSTEM_SKILL_KEY : '',
      liveHubSpotNativeTabEnabled ? HUBSPOT_NATIVE_SYSTEM_SKILL_KEY : '',
      liveDiscordNativeTabEnabled ? DISCORD_NATIVE_SYSTEM_SKILL_KEY : '',
    ].join(':')
    if (googleSheetsRosterRefreshAgentsRef.current.has(refreshKey)) {
      return
    }
    googleSheetsRosterRefreshAgentsRef.current.add(refreshKey)
    void queryClient.invalidateQueries({ queryKey: ['agent-roster'] })
  }, [activeAgentId, liveApolloNativeTabEnabled, liveDiscordNativeTabEnabled, liveGoogleSheetsDriveTabEnabled, liveHubSpotNativeTabEnabled, queryClient])
  const visibleRosterAgentIds = useMemo(
    () => rosterAgents.map((agent) => agent.id),
    [rosterAgents],
  )
  const openAgentChat = useCallback(
    (nextAgentId: string, pendingMeta: Omit<AgentSwitchMeta, 'agentId'> = {}) => {
      if (nextAgentId === activeAgentIdRef.current) {
        return
      }
      const rosterEntry = rosterAgents.find((agent) => agent.id === nextAgentId)
      pendingAgentMetaRef.current = {
        agentId: nextAgentId,
        agentName: pendingMeta.agentName ?? rosterEntry?.name ?? null,
        agentAvatarUrl: pendingMeta.agentAvatarUrl ?? rosterEntry?.avatarUrl ?? null,
        processingActive: pendingMeta.processingActive ?? rosterEntry?.processingActive,
        signupPreviewState: pendingMeta.signupPreviewState ?? rosterEntry?.signupPreviewState ?? 'none',
        planningState: pendingMeta.planningState ?? rosterEntry?.planningState ?? 'skipped',
      }
      locallySelectedAgentIdsRef.current.add(nextAgentId)
      setSwitchingAgentId(nextAgentId)
      setActiveAgentId(nextAgentId)
      navigateToAgentChat(nextAgentId)
    },
    [rosterAgents],
  )
  const {
    notificationPermission,
    notificationStatus,
    requestNotificationPermission,
    handleMessageNotificationEvent,
  } = useAgentChatNotifications({
    enabled: agentChatNotificationsEnabled,
    currentContext: effectiveContext,
    activeAgentId,
    availableAgentIds: visibleRosterAgentIds,
    onOpenAgent: openAgentChat,
  })
  const handleAgentMessageNotificationEvent = useCallback((event: AgentMessageNotification) => {
    const readState = normalizeAgentMessageReadState({
      has_unread_agent_message: event.has_unread_agent_message ?? true,
      latest_agent_message_id: event.latest_agent_message_id ?? event.message.id,
      latest_agent_message_at: event.latest_agent_message_at ?? event.message.timestamp,
      latest_agent_message_read_at: event.latest_agent_message_read_at ?? null,
    })
    queryClient.setQueriesData<AgentRosterQueryData>(
      { queryKey: ['agent-roster'] },
      (current) => applyRosterMessageReadState(current, event.agent_id, readState),
    )
    handleMessageNotificationEvent(event)
  }, [handleMessageNotificationEvent, queryClient])
  useEffect(() => {
    if (!activeAgentId || !activeRosterMeta?.hasUnreadAgentMessage) {
      return
    }
    const markerKey = activeRosterMeta.latestAgentMessageId ?? 'latest'
    if (pendingReadMarkerByAgentRef.current[activeAgentId] === markerKey) {
      return
    }
    pendingReadMarkerByAgentRef.current[activeAgentId] = markerKey
    markLatestAgentMessageRead(activeAgentId)
      .then((readState) => {
        queryClient.setQueriesData<AgentRosterQueryData>(
          { queryKey: ['agent-roster'] },
          (current) => applyRosterMessageReadState(current, activeAgentId, readState),
        )
      })
      .catch((error) => {
        console.warn('Failed to mark agent message read', error)
      })
      .finally(() => {
        if (pendingReadMarkerByAgentRef.current[activeAgentId] === markerKey) {
          delete pendingReadMarkerByAgentRef.current[activeAgentId]
        }
      })
  }, [
    activeAgentId,
    activeRosterMeta?.hasUnreadAgentMessage,
    activeRosterMeta?.latestAgentMessageId,
    queryClient,
  ])
  const persistAgentChatNotificationsEnabled = useCallback(
    (
      nextAgentChatNotificationsEnabled: boolean,
      options: { rollbackOnError?: boolean } = {},
    ) => {
      persistAgentRosterPreference(nextAgentChatNotificationsEnabled, {
        field: 'agentChatNotificationsEnabled',
        preferenceKey: USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED,
        setState: setAgentChatNotificationsEnabled,
        parsePersistedValue: parseBooleanPreference,
        currentValue: agentChatNotificationsEnabled,
        rollbackOnError: options.rollbackOnError,
      })
    },
    [agentChatNotificationsEnabled, persistAgentRosterPreference],
  )

  useEffect(() => {
    if (rosterQuery.data?.agentChatNotificationsEnabled === undefined) {
      return
    }
    if (!agentChatNotificationsEnabled) {
      notificationPermissionPromptAttemptedRef.current = false
      return
    }
    if (notificationPermission === 'denied') {
      notificationPermissionPromptAttemptedRef.current = false
      persistAgentChatNotificationsEnabled(false, { rollbackOnError: false })
      return
    }
    if (
      notificationPermission !== 'default'
      || notificationPermissionPromptAttemptedRef.current
    ) {
      return
    }
    notificationPermissionPromptAttemptedRef.current = true
    void requestNotificationPermission()
  }, [
    agentChatNotificationsEnabled,
    notificationPermission,
    rosterQuery.data?.agentChatNotificationsEnabled,
    persistAgentChatNotificationsEnabled,
    requestNotificationPermission,
  ])

  const handleAgentChatNotificationsEnabledChange = useCallback(
    (nextAgentChatNotificationsEnabled: boolean) => {
      if (!nextAgentChatNotificationsEnabled) {
        persistAgentChatNotificationsEnabled(false)
        return
      }

      notificationPermissionPromptAttemptedRef.current = true
      void requestNotificationPermission().then((permission) => {
        if (permission !== 'granted') {
          return
        }
        persistAgentChatNotificationsEnabled(true)
      })
    },
    [persistAgentChatNotificationsEnabled, requestNotificationPermission],
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
    onMessageNotificationEvent: handleAgentMessageNotificationEvent,
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
    agentName,
    setAgentId,
    agentContextReady,
  ])
  const storeAgentName = isStoreSynced ? storedAgentName : null
  const storeResolvedAvatarUrl = isStoreSynced ? storedAgentAvatarUrl : null
  const resolvedAgentName = storeAgentName ?? activeRosterMeta?.name ?? agentName ?? null
  const resolvedAvatarUrl = storeResolvedAvatarUrl ?? activeRosterMeta?.avatarUrl ?? agentAvatarUrl ?? null
  const pendingAgentEmail = activeAgentId ? pendingAgentEmails[activeAgentId] ?? null : null
  const resolvedAgentEmail = activeRosterMeta?.email ?? pendingAgentEmail ?? agentEmail ?? null
  const resolvedAgentSms = activeRosterMeta?.sms ?? agentSms ?? null
  const effectivePlanningState = isStoreSynced ? planningState : (activeRosterMeta?.planningState ?? 'skipped')
  const activeCreditForecast = activeAgentId
    ? creditForecastByAgentId[activeAgentId] ?? initialPageResponse?.credit_forecast ?? null
    : null
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
  const latestPlanSnapshot = useMemo(
    () => getLatestPlanSnapshot(timelineEvents, initialPageResponse?.current_plan ?? null),
    [timelineEvents, initialPageResponse?.current_plan],
  )
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
  const allowDeferredAgentPanelRequests = allowAgentPanelRequests && !initialLoading
  const {
    data: quickSettingsPayload,
    isLoading: quickSettingsLoading,
    error: quickSettingsError,
    refetch: refetchQuickSettings,
    updateQuickSettings,
    updating: quickSettingsUpdating,
  } = useAgentQuickSettings(activeAgentId, { enabled: allowDeferredAgentPanelRequests })
  const {
    data: addonsPayload,
    refetch: refetchAddons,
    updateAddons,
    updating: addonsUpdating,
  } = useAgentAddons(activeAgentId, { enabled: allowDeferredAgentPanelRequests })
  const contextSwitcher = useMemo(() => {
    if (!showContextSwitcher) {
      return null
    }
    if (!contextData) {
      return null
    }
    return {
      current: contextData.context,
      personal: contextData.personal,
      organizations: contextData.organizations,
      onSwitch: switchContext,
      onCreateOrganization: () => {
        setCreateOrganizationName('')
        setCreateOrganizationErrors([])
        setCreateOrganizationOpen(true)
      },
      isBusy: contextSwitching,
      errorMessage: contextError,
    }
  }, [contextData, contextError, contextSwitching, showContextSwitcher, switchContext])

  const handleCreateOrganizationSubmit = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const name = createOrganizationName.trim()
    if (!name) {
      setCreateOrganizationErrors(['Organization name is required.'])
      return
    }
    setCreateOrganizationBusy(true)
    setCreateOrganizationErrors([])
    try {
      await createOrganizationContext(name)
      setCreateOrganizationOpen(false)
      setCreateOrganizationName('')
    } catch (error) {
      if (error instanceof HttpError && typeof error.body === 'object' && error.body) {
        const body = error.body as Record<string, unknown>
        if (body.errors && typeof body.errors === 'object') {
          const messages = Object.values(body.errors as Record<string, unknown>).flatMap((value) => (
            Array.isArray(value) ? value.map(String) : [String(value)]
          ))
          setCreateOrganizationErrors(messages)
        } else if (body.error) {
          setCreateOrganizationErrors([String(body.error)])
        } else {
          setCreateOrganizationErrors([error.statusText])
        }
      } else {
        setCreateOrganizationErrors([safeErrorMessage(error) || 'Unable to create organization.'])
      }
    } finally {
      setCreateOrganizationBusy(false)
    }
  }, [createOrganizationContext, createOrganizationName])
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
      isActive: true,
      processingActive: false,
      lastInteractionAt: null,
      miniDescription: '',
      shortDescription: '',
      listingDescription: '',
      listingDescriptionSource: null,
      displayTags: [],
      detailUrl: `/app/agents/${activeAgentId}/settings`,
      dailyCreditRemaining: null,
      dailyCreditLow: false,
      last24hCreditBurn: null,
      isOrgOwned: false,
      pendingActionRequestCount: 0,
    }
  }, [activeAgentId, resolvedAgentName, resolvedAvatarUrl])
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
      const nextEmail = resolvedAgentEmail ?? agent.email ?? null
      const nextSms = resolvedAgentSms ?? agent.sms ?? null
      const nextIsOrgOwned = agent.isOrgOwned ?? resolvedIsOrgOwned

      if (
        nextName === agent.name
        && nextAvatarUrl === agent.avatarUrl
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
        email: nextEmail,
        sms: nextSms,
        isOrgOwned: nextIsOrgOwned,
      }
    })

    return changed ? nextAgents : rosterAgents
  }, [
    activeAgentId,
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
      return `/console/api/agents/${activeAgentId}/settings/`
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
  const canSharePublicTemplate = Boolean(
    activeAgentId
      && !isSelectionView
      && !isNewAgent
      && !resolvedIsOrgOwned
      && activeCanManageAgent
      && !isCollaboratorOnly,
  )

  const handleOpenCollaboratorInvite = useCallback(() => {
    setCollaboratorInviteOpen(true)
  }, [])

  const handleCloseCollaboratorInvite = useCallback(() => {
    setCollaboratorInviteOpen(false)
  }, [])

  const handleOpenPublicShare = useCallback(() => {
    setPublicShareOpen(true)
  }, [])

  const handleClosePublicShare = useCallback(() => {
    setPublicShareOpen(false)
  }, [])

  const handleOpenSupport = useCallback(() => {
    setSupportDialogOpen(true)
  }, [])

  const handleCloseSupport = useCallback(() => {
    setSupportDialogOpen(false)
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
  const selectedAgentAccountPause = addonsPayload?.status?.accountPause ?? null
  const currentContextBillingStatus = rosterQuery.data?.billingStatus ?? null
  const currentContextAccountPause = rosterQuery.data?.accountPause ?? null
  const currentContextCanCreateAgents = (
    rosterQuery.data?.context.canCreateAgents
    ?? effectiveContext?.canCreateAgents
    ?? true
  )
  const sendMessageDisabledReason = !isNewAgent && selectedAgentAccountPause?.paused
    ? resolveSendMessagePausedMessage(selectedAgentAccountPause.resumeAt)
    : (!isNewAgent && selectedAgentBillingStatus?.delinquent
      ? resolveSendMessageDisabledMessage()
      : null)
  const previewCreateAgentBlocked = !currentContextBillingStatus?.delinquent
    && !currentContextAccountPause?.paused
    && personalSignupPreviewAvailable
  const createAgentDisabledReason = !currentContextCanCreateAgents
    ? 'You do not have permission to create agents in this organization.'
    : currentContextAccountPause?.paused
      ? resolveCreateAgentPausedMessage(currentContextAccountPause.resumeAt)
      : currentContextBillingStatus?.delinquent
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

  const navigateShellPath = useCallback((nextPath: string, nextAgentId?: string | null) => {
    const nextUrl = `${nextPath}${window.location.search}${window.location.hash}`
    setShellPathname(nextPath)
    if (typeof nextAgentId !== 'undefined' && nextAgentId !== activeAgentIdRef.current) {
      setSwitchingAgentId(null)
      setActiveAgentId(nextAgentId)
    }
    window.history.pushState({ agentId: nextAgentId ?? null }, '', nextUrl)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [])

  const navigateToShellSubview = useCallback((subview: AgentChatShellSubview, nextAgentId?: string | null) => {
    const resolvedAgentId = nextAgentId ?? activeAgentIdRef.current
    if (!resolvedAgentId) {
      return
    }
    setSelectionSidebarMode('gallery')
    const nextPath = buildAgentChatShellPath(window.location.pathname, resolvedAgentId, subview)
    navigateShellPath(nextPath, resolvedAgentId)
  }, [navigateShellPath])

  const handleConfigureAgent = useCallback((agent: AgentRosterEntry) => {
    pendingAgentMetaRef.current = {
      agentId: agent.id,
      agentName: agent.name,
      agentAvatarUrl: agent.avatarUrl,
      processingActive: agent.processingActive,
      signupPreviewState: agent.signupPreviewState ?? 'none',
      planningState: agent.planningState ?? 'skipped',
    }
    locallySelectedAgentIdsRef.current.add(agent.id)
    if (agent.id !== activeAgentIdRef.current) {
      setSwitchingAgentId(agent.id)
      setActiveAgentId(agent.id)
    }
    navigateToShellSubview('settings', agent.id)
  }, [navigateToShellSubview])

  const handleSelectAgent = useCallback(
    (agent: AgentRosterEntry) => {
      openAgentChat(agent.id, {
        agentName: agent.name,
        agentAvatarUrl: agent.avatarUrl,
        processingActive: agent.processingActive,
        signupPreviewState: agent.signupPreviewState ?? 'none',
        planningState: agent.planningState ?? 'skipped',
      })
    },
    [openAgentChat],
  )

  const handleOpenFullSettings = useCallback(() => {
    navigateToShellSubview('settings')
  }, [navigateToShellSubview])

  const handleOpenEmbeddedSecrets = useCallback(() => {
    navigateToShellSubview('secrets')
  }, [navigateToShellSubview])

  const handleOpenEmbeddedSecretRequests = useCallback(() => {
    navigateToShellSubview('secret-requests')
  }, [navigateToShellSubview])

  const handleOpenEmbeddedEmailSettings = useCallback(() => {
    navigateToShellSubview('email')
  }, [navigateToShellSubview])

  const handleOpenEmbeddedFiles = useCallback(() => {
    navigateToShellSubview('files')
  }, [navigateToShellSubview])

  const handleOpenEmbeddedContactRequests = useCallback(() => {
    navigateToShellSubview('contact-requests')
  }, [navigateToShellSubview])

  const handleCloseEmbeddedSettings = useCallback(() => {
    if (!activeAgentIdRef.current) {
      return
    }
    const nextSubview = shellSubview === 'settings' ? 'chat' : 'settings'
    navigateToShellSubview(nextSubview, activeAgentIdRef.current)
  }, [navigateToShellSubview, shellSubview])

  const handleExitEmbeddedSettings = useCallback(() => {
    if (!activeAgentIdRef.current) {
      return
    }
    navigateToShellSubview('chat', activeAgentIdRef.current)
  }, [navigateToShellSubview])

  const handleEmbeddedSettingsDeleted = useCallback(() => {
    const selectionPath = buildAgentChatShellSelectionPath(window.location.pathname)
    if (selectionPath.startsWith('/app')) {
      navigateShellPath(selectionPath, null)
      return
    }
    window.location.assign(selectionPath)
  }, [navigateShellPath])

  const handleEmbeddedSettingsReassigned = useCallback((payload: {
    context?: { type: string; id: string; name?: string | null }
    redirect?: string | null
    organization?: { id: string; name: string } | null
  }) => {
    if (payload.context) {
      storeConsoleContext({
        type: payload.context.type as ConsoleContext['type'],
        id: payload.context.id,
        name: payload.context.name ?? '',
      })
    }
    const currentAgentId = activeAgentIdRef.current
    if (!currentAgentId) {
      return
    }
    const nextPath = buildAgentChatShellPath(window.location.pathname, currentAgentId, 'settings')
    window.location.assign(nextPath)
  }, [])

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
      pinAndJumpToBottom()
    })()
  }, [pinAndJumpToBottom, syncLatestTimeline, timelineHasMoreNewer])

  const handleComposerFocus = useCallback(() => {
    scrollOnComposerFocus()
  }, [scrollOnComposerFocus])

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
        ? '/app/api-keys'
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
      attachments: File[] = [],
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
          attachments,
        )
        const createdAgentName = result.agent_name?.trim() || 'Agent'
        const createdAgentEmail = result.agent_email?.trim() || null
        const createdPlanningState = normalizePlanningState(result.planning_state)
        const createdAgentEntry: AgentRosterEntry = {
          id: result.agent_id,
          name: createdAgentName,
          avatarUrl: null,
          isActive: true,
          processingActive: false,
          lastInteractionAt: new Date().toISOString(),
          miniDescription: '',
          shortDescription: '',
          listingDescription: '',
          listingDescriptionSource: null,
          displayTags: [],
          detailUrl: `/app/agents/${result.agent_id}/settings`,
          dailyCreditRemaining: null,
          dailyCreditLow: false,
          last24hCreditBurn: null,
          email: createdAgentEmail,
          signupPreviewState: personalSignupPreviewAvailable ? 'awaiting_first_reply_pause' : 'none',
          planningState: createdPlanningState,
          pendingActionRequestCount: 0,
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
    && !initialLoading
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
  const organizationPurchasedSeats = usageSummary?.context?.type === 'organization'
    ? usageSummary.billing?.purchasedSeats
    : undefined
  const organizationHasNoPurchasedSeats = organizationPurchasedSeats !== undefined && organizationPurchasedSeats <= 0
  const hasUnlimitedQuota = taskQuota ? taskQuota.total < 0 || taskQuota.available < 0 : false
  // Use < 1 threshold to catch "dust credits" (e.g., 0.001) that aren't enough to do anything
  const isOutOfTaskCredits = Boolean(taskQuota && !hasUnlimitedQuota && taskQuota.available < 1)
  const showTaskCreditsWarning = Boolean(
    taskQuota
    && !organizationHasNoPurchasedSeats
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
  const isImmersiveShellPath = shellPathname.startsWith('/app')
  const billingUrl = useMemo(() => {
    if (isImmersiveShellPath) {
      return '/app/billing'
    }
    if (!effectiveContext) {
      return '/app/billing'
    }
    if (effectiveContext.type === 'organization') {
      return `/app/billing?context_type=organization&context_id=${encodeURIComponent(effectiveContext.id)}`
    }
    return '/app/billing'
  }, [effectiveContext, isImmersiveShellPath])
  const usageUrl = '/app/usage'
  const apiKeysUrl = '/app/api-keys'
  const profileUrl = '/app/profile'
  const organizationUrl = effectiveContext?.type === 'organization'
    ? '/app/organization'
    : null
  const secretsUrl = '/app/secrets'
  const integrationsUrl = '/app/integrations'
  const appShellDestinations = useMemo<AppShellDestinations>(() => ({
    billing: billingUrl,
    usage: usageUrl,
    apiKeys: apiKeysUrl,
    profile: profileUrl,
    organization: organizationUrl,
    secrets: secretsUrl,
    integrations: integrationsUrl,
  }), [billingUrl, organizationUrl])
  const appShellOpenHandlers = useMemo<AppShellOpenHandlers>(() => ({
    billing: () => openAppShellDestination(onOpenBilling, appShellDestinations.billing),
    usage: () => openAppShellDestination(onOpenUsage, appShellDestinations.usage),
    apiKeys: () => openAppShellDestination(onOpenApiKeys, appShellDestinations.apiKeys),
    profile: () => openAppShellDestination(onOpenProfile, appShellDestinations.profile),
    organization: () => openAppShellDestination(onOpenOrganization, appShellDestinations.organization),
    secrets: () => openAppShellDestination(onOpenSecrets, appShellDestinations.secrets),
    integrations: () => openAppShellDestination(onOpenIntegrations, appShellDestinations.integrations),
  }), [
    appShellDestinations,
    onOpenApiKeys,
    onOpenBilling,
    onOpenIntegrations,
    onOpenOrganization,
    onOpenProfile,
    onOpenSecrets,
    onOpenUsage,
  ])
  const bannerBillingStatus = selectedAgentBillingStatus ?? currentContextBillingStatus
  const bannerAccountPause = selectedAgentAccountPause?.paused
    ? selectedAgentAccountPause
    : currentContextAccountPause?.paused
      ? currentContextAccountPause
      : null
  const billingManageUrl = bannerAccountPause?.manageBillingUrl
    || bannerBillingStatus?.manageBillingUrl
    || contactPackManageUrl
    || billingUrl
  const highPriorityBanner = useMemo(() => {
    if (bannerAccountPause?.paused) {
      return {
        id: 'account-paused',
        title: 'Account paused',
        message: resolveCreateAgentPausedMessage(bannerAccountPause.resumeAt),
        actionLabel: 'Open billing',
        actionHref: billingManageUrl,
        dismissible: false,
        tone: 'warning' as const,
      }
    }
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
    bannerAccountPause?.paused,
    bannerAccountPause?.resumeAt,
    bannerBillingStatus?.actionable,
    bannerBillingStatus?.delinquent,
    bannerBillingStatus?.reason,
    billingManageUrl,
  ])
  const selectionMainClassName = 'agent-chat-main'
  const sidebarTaskCredits = useMemo(() => (
    taskQuota
      ? {
          usedToday: usageSummary?.metrics.todayCredits?.total ?? null,
          remaining: taskQuota.available,
          resetOn: usageSummary?.period?.resetOn ?? null,
          unlimited: Boolean(taskQuota.total < 0 || taskQuota.available < 0),
        }
      : null
  ), [
    taskQuota,
    usageSummary?.metrics.todayCredits?.total,
    usageSummary?.period?.resetOn,
  ])
  const immersiveShellOpenHandlers = isImmersiveShellPath ? appShellOpenHandlers : null
  const selectionSidebarSettings = useMemo(() => ({
    context: effectiveContext,
    viewerEmail: viewerEmail ?? null,
    isProprietaryMode,
    billingUrl: appShellDestinations.billing,
    usageUrl: appShellDestinations.usage,
    apiKeysUrl: appShellDestinations.apiKeys,
    profileUrl: appShellDestinations.profile,
    organizationUrl: appShellDestinations.organization,
    secretsUrl: appShellDestinations.secrets,
    integrationsUrl: appShellDestinations.integrations,
    onOpenBilling: onOpenBilling ? appShellOpenHandlers.billing : null,
    onOpenUsage: immersiveShellOpenHandlers?.usage ?? null,
    onOpenApiKeys: immersiveShellOpenHandlers?.apiKeys ?? null,
    onOpenProfile: immersiveShellOpenHandlers?.profile ?? null,
    onOpenOrganization: immersiveShellOpenHandlers?.organization ?? null,
    onOpenSecrets: immersiveShellOpenHandlers?.secrets ?? null,
    onOpenIntegrations: immersiveShellOpenHandlers?.integrations ?? null,
    onOpenHelp: handleOpenSupport,
    taskCredits: sidebarTaskCredits,
  }), [
    appShellDestinations,
    appShellOpenHandlers,
    effectiveContext,
    handleOpenSupport,
    immersiveShellOpenHandlers,
    onOpenBilling,
    isProprietaryMode,
    sidebarTaskCredits,
    viewerEmail,
  ])
  const handleSelectionSidebarModeChange = useCallback((mode: 'collapsed' | 'list' | 'gallery') => {
    writeSelectionSidebarModePreference(mode)
    if (selectionPage !== 'agents' && mode !== 'gallery' && onSelectionPageChange) {
      onSelectionPageChange('agents')
    }
    setSelectionSidebarMode(mode)
  }, [onSelectionPageChange, selectionPage])
  const selectionSidebarProps: SelectionSidebarProps = {
    agents: sidebarAgents,
    favoriteAgentIds,
    activeAgentId: null,
    loading: rosterLoading,
    errorMessage: rosterErrorMessage,
    onSelectAgent: handleSelectAgent,
    onConfigureAgent: handleConfigureAgent,
    onToggleAgentFavorite: handleToggleAgentFavorite,
    onCreateAgent: handleCreateAgent,
    createAgentDisabledReason,
    rosterSortMode: agentRosterSortMode,
    onRosterSortModeChange: handleAgentRosterSortModeChange,
    desktopMode: selectionSidebarMode,
    onDesktopModeChange: handleSelectionSidebarModeChange,
    contextSwitcher: contextSwitcher ?? undefined,
    settings: selectionSidebarSettings,
    galleryShellPage: selectionPage,
    galleryShellPanel: selectionPage !== 'agents' ? selectionShellPanel : null,
    onGalleryShellPageChange: onSelectionPageChange,
  }
  const agentChatPageStyle = useMemo<AgentChatPageStyle>(() => ({
    '--agent-chat-grain-texture': `url("${RESOLVED_NOISE_DARK_TEXTURE_URL}")`,
  }), [])
  const createOrganizationModal = createOrganizationOpen ? (
    <ModalForm
      id="create-organization-form"
      title="Add Organization"
      onClose={() => {
        if (!createOrganizationBusy) {
          setCreateOrganizationOpen(false)
        }
      }}
      widthClass="sm:max-w-lg"
      icon={Building2}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      dismissible={!createOrganizationBusy}
      onSubmit={handleCreateOrganizationSubmit}
      submitLabel="Create Organization"
      submittingLabel="Creating..."
      submitting={createOrganizationBusy}
      errorMessages={createOrganizationErrors}
    >
      <div>
        <label htmlFor="organization-name" className="block text-sm font-medium text-slate-700">
          Organization Name
        </label>
        <input
          id="organization-name"
          type="text"
          required
          value={createOrganizationName}
          onChange={(event) => setCreateOrganizationName(event.target.value)}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
          placeholder="Acme Operations"
          autoFocus
        />
      </div>
    </ModalForm>
  ) : null
  const renderSelectionLayout = (content: ReactNode) => (
    <div
      className="agent-chat-page agent-chat-page--framed"
      data-processing="false"
      style={agentChatPageStyle}
    >
      {createOrganizationModal}
      <HelpSupportDialog
        open={supportDialogOpen}
        onClose={handleCloseSupport}
        agentId={activeAgentId}
        agentName={activeAgentId ? resolvedAgentName : null}
        workspaceContext={effectiveContext}
      />
      <ChatSidebar {...selectionSidebarProps} />
      <main className={selectionMainClassName} data-sidebar-mode={selectionSidebarMode}>
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
      pending.attachments,
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
    const hasMessageContent = isNewAgent ? body.trim().length > 0 : body.trim().length > 0 || attachments.length > 0
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
        attachments,
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
      const payload = {
        responses: response.responses.map((item) => (
          item.selectedOptionKey
            ? { request_id: item.requestId, selected_option_key: item.selectedOptionKey }
            : { request_id: item.requestId, free_text: item.freeText?.trim() ?? '' }
        )),
      }
      const result = await respondToHumanInputRequestsBatch(activeAgentId, payload)
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

  const handleDismissHumanInputRequest = useCallback(async (requestId: string) => {
    if (!activeAgentId) {
      return
    }
    const result = await dismissHumanInputRequest(activeAgentId, requestId)
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
    if (result.event) {
      receiveRealtimeEvent(result.event)
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
    void queryClient.invalidateQueries({ queryKey: ['agent-settings', activeAgentId], exact: true })
    void queryClient.invalidateQueries({ queryKey: ['agent-quick-settings', activeAgentId], exact: true })
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
      smsContactPermissionAttested?: boolean
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
        sms_contact_permission_attested: response.smsContactPermissionAttested ?? null,
      })),
    })
    replacePendingActionRequestsInCache(queryClient, activeAgentId, result.pendingActionRequests)
    return result
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
    window.location.assign('/app/api-keys')
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
  const showEmbeddedSettings = shellSubview !== 'chat' && Boolean(activeAgentId)
  const embeddedSettingsTitle = shellSubview === 'chat'
    ? 'Agent Settings'
    : EMBEDDED_SETTINGS_TITLES[shellSubview]
  const embeddedSettingsPanel = showEmbeddedSettings && activeAgentId ? (
    shellSubview === 'settings' ? (
      <EmbeddedAgentSettingsPanel
        agentId={activeAgentId}
        onBack={handleCloseEmbeddedSettings}
        onDeleted={handleEmbeddedSettingsDeleted}
        onReassigned={handleEmbeddedSettingsReassigned}
        onOpenSecrets={handleOpenEmbeddedSecrets}
        onOpenEmailSettings={handleOpenEmbeddedEmailSettings}
        onOpenFiles={handleOpenEmbeddedFiles}
        onOpenContactRequests={handleOpenEmbeddedContactRequests}
      />
    ) : shellSubview === 'secrets' ? (
      <EmbeddedAgentSecretsPanel
        agentId={activeAgentId}
        agentName={resolvedAgentName || 'Agent'}
        onBack={handleCloseEmbeddedSettings}
        onOpenRequests={handleOpenEmbeddedSecretRequests}
      />
    ) : shellSubview === 'secret-requests' ? (
      <EmbeddedAgentSecretRequestsPanel
        agentId={activeAgentId}
        agentName={resolvedAgentName || 'Agent'}
        onBack={handleCloseEmbeddedSettings}
        onOpenSecrets={handleOpenEmbeddedSecrets}
        onFulfillRequestedSecrets={handleFulfillRequestedSecrets}
        onRemoveRequestedSecrets={handleRemoveRequestedSecrets}
      />
    ) : shellSubview === 'email' ? (
      <EmbeddedAgentEmailSettingsPanel
        agentId={activeAgentId}
        onBack={handleCloseEmbeddedSettings}
      />
    ) : shellSubview === 'contact-requests' ? (
      <EmbeddedAgentContactRequestsPanel
        agentId={activeAgentId}
        agentName={resolvedAgentName || 'Agent'}
        onBack={handleCloseEmbeddedSettings}
        onResolveContactRequests={handleResolveContactRequests}
      />
    ) : (
      <EmbeddedAgentFilesPanel
        agentId={activeAgentId}
        agentName={resolvedAgentName || 'Agent'}
        canManage={activeCanManageAgent}
        onBack={handleCloseEmbeddedSettings}
      />
    )
  ) : null
  const chatLayoutSidebarProps = {
    agentRoster: sidebarAgents,
    favoriteAgentIds,
    activeAgentId,
    insightsPanelExpandedPreference,
    switchingAgentId,
    rosterLoading,
    rosterError: rosterErrorMessage,
    onSelectAgent: handleSelectAgent,
    onConfigureAgent: handleConfigureAgent,
    onToggleAgentFavorite: handleToggleAgentFavorite,
    onCreateAgent: handleCreateAgent,
    createAgentDisabledReason,
    onBlockedCreateAgent: previewCreateAgentBlocked ? handleBlockedCreateAgent : undefined,
    agentRosterSortMode,
    onAgentRosterSortModeChange: handleAgentRosterSortModeChange,
    onInsightsPanelExpandedPreferenceChange: handleInsightsPanelExpandedPreferenceChange,
    contextSwitcher: contextSwitcher ?? undefined,
    currentContext: effectiveContext,
    sidebarBillingUrl: billingManageUrl,
    onOpenBilling: immersiveShellOpenHandlers?.billing,
    sidebarUsageUrl: appShellDestinations.usage,
    onOpenUsage: immersiveShellOpenHandlers?.usage,
    sidebarApiKeysUrl: appShellDestinations.apiKeys,
    onOpenApiKeys: immersiveShellOpenHandlers?.apiKeys,
    sidebarProfileUrl: appShellDestinations.profile,
    onOpenProfile: immersiveShellOpenHandlers?.profile,
    sidebarOrganizationUrl: appShellDestinations.organization,
    onOpenOrganization: immersiveShellOpenHandlers?.organization,
    sidebarSecretsUrl: appShellDestinations.secrets,
    onOpenSecrets: immersiveShellOpenHandlers?.secrets,
    sidebarIntegrationsUrl: appShellDestinations.integrations,
    onOpenIntegrations: immersiveShellOpenHandlers?.integrations,
    onOpenHelp: handleOpenSupport,
    sidebarTodayCreditsUsed: sidebarTaskCredits?.usedToday ?? null,
    sidebarCreditsResetOn: sidebarTaskCredits?.resetOn ?? null,
    sidebarNotificationsEnabled: agentChatNotificationsEnabled,
    sidebarNotificationStatus: notificationStatus,
    onSidebarNotificationsEnabledChange: handleAgentChatNotificationsEnabledChange,
    galleryShellPage: selectionPage,
    galleryShellPanel: selectionPage !== 'agents' ? selectionShellPanel : null,
    onGalleryShellPageChange: immersiveShellOpenHandlers ? onSelectionPageChange : undefined,
    showEmbeddedSettings,
    embeddedSettingsPanel,
    embeddedSettingsTitle,
    onBackFromEmbeddedSettings: handleExitEmbeddedSettings,
  }

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
    if (selectionPage !== 'agents') {
      return renderSelectionLayout(
        selectionMainPanel ? (
          <div className="flex min-h-full w-full flex-1 md:hidden">
            {selectionMainPanel}
          </div>
        ) : (
          <div className="flex min-h-full w-full flex-1" />
        ),
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
      <PublicAgentShareDialog
        open={publicShareOpen}
        agentId={activeAgentId}
        agentName={resolvedAgentName || agentName}
        onClose={handleClosePublicShare}
      />
      <HelpSupportDialog
        open={supportDialogOpen}
        onClose={handleCloseSupport}
        agentId={activeAgentId}
        agentName={isNewAgent ? 'New Agent' : (resolvedAgentName || agentName)}
        workspaceContext={effectiveContext}
      />
      {createOrganizationModal}
      <AgentChatLayout
        agentId={activeAgentId}
        agentFirstName={isNewAgent ? 'New Agent' : agentFirstName}
        agentAvatarUrl={resolvedAvatarUrl}
        agentEmail={resolvedAgentEmail}
        agentSms={resolvedAgentSms}
        agentName={isNewAgent ? 'New Agent' : (resolvedAgentName || 'Agent')}
        auditUrl={activeAuditUrl}
        agentIsOrgOwned={resolvedIsOrgOwned || effectiveContext?.type === 'organization'}
        isCollaborator={isCollaboratorOnly}
        canManageAgent={activeCanManageAgent}
        hideInsightsPanel={isCollaboratorOnly}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        connectionStatus={connectionIndicator.status}
        connectionLabel={connectionIndicator.label}
        connectionDetail={connectionIndicator.detail}
        planSnapshot={latestPlanSnapshot}
        creditForecast={activeCreditForecast}
        {...chatLayoutSidebarProps}
        onComposerFocus={handleComposerFocus}
        onComposerRequestScrollToBottom={scrollToBottom}
        onClose={onClose}
        dailyCredits={dailyCreditsInfo}
        dailyCreditsStatus={dailyCreditsStatus}
        dailyCreditsLoading={canManageDailyCredits ? quickSettingsLoading : false}
        dailyCreditsError={canManageDailyCredits ? dailyCreditsErrorMessage : null}
        onRefreshDailyCredits={canManageDailyCredits ? refetchQuickSettings : undefined}
        onUpdateDailyCredits={canManageDailyCredits ? handleUpdateDailyCredits : undefined}
        dailyCreditsUpdating={canManageDailyCredits ? quickSettingsUpdating : false}
        onOpenFullSettings={handleOpenFullSettings}
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
        showPurchaseSeatsPrompt={organizationHasNoPurchasedSeats}
        showTaskCreditsWarning={showTaskCreditsWarning}
        taskCreditsWarningVariant={taskCreditsWarningVariant}
        showTaskCreditsUpgrade={taskPackShowUpgrade}
        taskCreditsDismissKey={taskCreditsDismissKey}
        highPriorityBanner={highPriorityBanner}
        onRefreshAddons={refetchAddons}
        contactPackManageUrl={contactPackManageUrl}
        onShare={canShareCollaborators ? handleOpenCollaboratorInvite : undefined}
        onPublicShare={canSharePublicTemplate ? handleOpenPublicShare : undefined}
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
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        googleSheetsDriveTabEnabled={googleSheetsDriveTabEnabled}
        apolloNativeTabEnabled={apolloNativeTabEnabled}
        hubspotNativeTabEnabled={hubspotNativeTabEnabled}
        discordNativeTabEnabled={discordNativeTabEnabled}
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
        onDismissHumanInputRequest={handleDismissHumanInputRequest}
        onResolveSpawnRequest={handleResolveSpawnRequest}
        onFulfillRequestedSecrets={handleFulfillRequestedSecrets}
        onRemoveRequestedSecrets={handleRemoveRequestedSecrets}
        onOpenAgentSecrets={handleOpenEmbeddedSecrets}
        onOpenAgentSecretRequests={handleOpenEmbeddedSecretRequests}
        onOpenAgentEmailSettings={handleOpenEmbeddedEmailSettings}
        onOpenAgentFiles={handleOpenEmbeddedFiles}
        onResolveContactRequests={handleResolveContactRequests}
        onViewAllContactRequests={handleOpenEmbeddedContactRequests}
        onJumpToLatest={handleJumpToLatest}
        autoFocusComposer
        autoScrollPinned={autoScrollPinned}
        isNearBottom={isNearBottom}
        hasUnseenActivity={timelineHasUnseenActivity}
        timelineRef={captureTimelineRef}
        timelineContentRef={timelineContentRef}
        composerShellRef={composerShellRef}
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
