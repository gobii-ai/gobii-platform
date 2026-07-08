import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Brain,
  ChevronDown,
  ChevronUp,
  Download,
  ListChevronsDownUp,
  ListChevronsUpDown,
  Megaphone,
  RefreshCcw,
  Search,
  Settings,
  Stethoscope,
} from 'lucide-react'
import { useAgentAuditSocket } from '../hooks/useAgentAuditSocket'
import {
  auditActions,
  initializeAudit,
  jumpAuditToTime,
  loadAuditTimeline,
  loadMoreAudit,
  selectAuditState,
} from '../store/auditSlice'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import type { AuditToolCallEvent, AuditErrorEvent, AuditMessageEvent, AuditStepEvent, AuditSystemMessageEvent, AuditEvent } from '../types/agentAudit'
import {
  createSystemMessage,
  decideAgentJudgeSuggestion,
  fetchPromptArchive,
  runAgentJudge,
  searchStaffAgents,
  triggerProcessEvents,
  updateSystemMessage,
  type ManualJudgeSuggestion,
  type StaffAgentSearchResult,
} from '../api/agentAudit'
import { AuditTimeline } from '../components/agentAudit/AuditTimeline'
import { Modal } from '../components/common/Modal'
import { SystemMessageCard } from '../components/agentAudit/SystemMessageCard'
import { AgentAuditFiltersMenu } from '../components/agentAudit/AgentAuditFiltersMenu'
import { CompletionCard, type PromptState } from '../components/agentAudit/CompletionCard'
import { ErrorRow, MessageRow, StepRow, ToolCallRow } from '../components/agentAudit/EventRows'
import { renderHtmlOrText } from '../components/agentAudit/eventPrimitives'

type AgentAuditScreenProps = {
  agentId: string
  agentName?: string | null
  adminAgentUrl?: string | null
}

function eventKeyFor(event: AuditEvent): string {
  const id = 'id' in event ? event.id : event.run_id
  return `${event.kind}:${id ?? 'unknown'}`
}

function isEventCollapsedByDefault(event: AuditEvent): boolean {
  return event.kind !== 'message'
}

function getTargetMessageId(
  messages: AuditMessageEvent[],
  direction: 'prev' | 'next',
  activeId: string | null,
): string | null {
  if (!messages.length) return null
  const activeIndex = activeId ? messages.findIndex((event) => event.id === activeId) : -1
  if (activeIndex === -1) {
    return direction === 'next' ? messages[0]?.id ?? null : messages[messages.length - 1]?.id ?? null
  }
  const targetIndex = direction === 'next' ? activeIndex + 1 : activeIndex - 1
  if (targetIndex < 0 || targetIndex >= messages.length) return null
  return messages[targetIndex]?.id ?? null
}

const DEFAULT_FILTERS = {
  messages: true,
  toolCalls: true,
  completions: true,
  errors: true,
  systemMessages: true,
  systemSteps: true,
  agentSteps: true,
  tagGeneration: true,
  miniDescription: true,
  shortDescription: true,
} as const

const AGENT_SEARCH_LIMIT = 8
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const AUDIT_EXPORT_RANGES = [
  { key: 'all', label: 'Full audit' },
  { key: '1h', label: 'Last hour' },
  { key: '24h', label: 'Last 24 hours' },
  { key: '7d', label: 'Last 7 days' },
  { key: '30d', label: 'Last 30 days' },
] as const
type AuditExportRangeKey = (typeof AUDIT_EXPORT_RANGES)[number]['key']

function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value)
}

type FilterState = {
  [Key in keyof typeof DEFAULT_FILTERS]: boolean
}

type EventFilterKey =
  | 'messages'
  | 'toolCalls'
  | 'completions'
  | 'errors'
  | 'systemMessages'
  | 'systemSteps'
  | 'agentSteps'

type CompletionFilterKey = 'tagGeneration' | 'miniDescription' | 'shortDescription'

const EVENT_TYPE_FILTERS: {
  key: EventFilterKey
  label: string
  matches: (event: AuditEvent) => boolean
}[] = [
  { key: 'messages', label: 'Messages', matches: (event) => event.kind === 'message' },
  { key: 'toolCalls', label: 'Tool calls', matches: (event) => event.kind === 'tool_call' },
  { key: 'completions', label: 'LLM completions', matches: (event) => event.kind === 'completion' },
  { key: 'errors', label: 'Errors', matches: (event) => event.kind === 'error' },
  { key: 'systemMessages', label: 'System messages', matches: (event) => event.kind === 'system_message' },
  { key: 'systemSteps', label: 'System steps', matches: (event) => event.kind === 'step' && event.is_system },
  { key: 'agentSteps', label: 'Agent steps', matches: (event) => event.kind === 'step' && !event.is_system },
]

const COMPLETION_TYPE_FILTERS: {
  key: CompletionFilterKey
  label: string
  matches: (completionType: string) => boolean
}[] = [
  { key: 'tagGeneration', label: 'Tag generation', matches: (completionType) => completionType === 'tag' },
  { key: 'miniDescription', label: 'Mini description', matches: (completionType) => completionType === 'mini_description' },
  { key: 'shortDescription', label: 'Short description', matches: (completionType) => completionType === 'short_description' },
]

function formatJudgeSuggestionType(value?: string | null): string {
  if (!value) return 'Suggestion'
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

export function AgentAuditScreen({ agentId, agentName, adminAgentUrl }: AgentAuditScreenProps) {
  const dispatch = useAppDispatch()
  const {
    events,
    loading,
    error,
    hasMore,
    processingActive,
  } = useAppSelector(selectAuditState)
  const initialize = useCallback(
    async (targetAgentId: string) => {
      await dispatch(initializeAudit(targetAgentId)).unwrap()
    },
    [dispatch],
  )
  const loadMore = useCallback(
    async () => {
      await dispatch(loadMoreAudit()).unwrap()
    },
    [dispatch],
  )
  const loadTimeline = useCallback(
    async (targetAgentId: string) => {
      await dispatch(loadAuditTimeline(targetAgentId)).unwrap()
    },
    [dispatch],
  )
  const jumpToTime = useCallback(
    async (day: string) => {
      await dispatch(jumpAuditToTime(day)).unwrap()
    },
    [dispatch],
  )
  const setSelectedDay = useCallback(
    (day: string | null) => dispatch(auditActions.setSelectedDay(day)),
    [dispatch],
  )
  const setProcessingActive = useCallback(
    (active: boolean) => dispatch(auditActions.setProcessingActive(active)),
    [dispatch],
  )
  const [promptState, setPromptState] = useState<Record<string, PromptState>>({})
  const eventsRef = useRef<HTMLDivElement | null>(null)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)
  const loadingRef = useRef(loading)
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const messageNodeMap = useRef(new Map<string, HTMLDivElement>())
  const messageIdByNode = useRef(new Map<Element, string>())
  const agentSearchRequestId = useRef(0)
  const [timelineMaxHeight, setTimelineMaxHeight] = useState<number | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filters, setFilters] = useState<FilterState>({ ...DEFAULT_FILTERS })
  const [exportRange, setExportRange] = useState<AuditExportRangeKey>('all')
  const [eventCollapseOverrideKeys, setEventCollapseOverrideKeys] = useState<Set<string>>(() => new Set())
  const [processQueueing, setProcessQueueing] = useState(false)
  const [judgeRunning, setJudgeRunning] = useState(false)
  const [judgeReviewSuggestion, setJudgeReviewSuggestion] = useState<ManualJudgeSuggestion | null>(null)
  const [judgeDecisionBusy, setJudgeDecisionBusy] = useState<'approve' | 'reject' | null>(null)
  const [judgeDecisionError, setJudgeDecisionError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [messageModalOpen, setMessageModalOpen] = useState(false)
  const [editingMessage, setEditingMessage] = useState<AuditSystemMessageEvent | null>(null)
  const [messageBody, setMessageBody] = useState('')
  const [messageSubmitting, setMessageSubmitting] = useState(false)
  const [messageError, setMessageError] = useState<string | null>(null)
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null)
  const [agentSearchOpen, setAgentSearchOpen] = useState(false)
  const [agentSearchQuery, setAgentSearchQuery] = useState('')
  const [agentSearchResults, setAgentSearchResults] = useState<StaffAgentSearchResult[]>([])
  const [agentSearchLoading, setAgentSearchLoading] = useState(false)
  const [agentSearchError, setAgentSearchError] = useState<string | null>(null)
  const latestEventsRef = useRef<AuditEvent[]>(events)

  useEffect(() => {
    latestEventsRef.current = events
  }, [events])
  const pendingMessageScrollId = useRef<string | null>(null)
  useAgentAuditSocket(agentId)
  const auditExportUrl = useMemo(
    () => `/console/api/staff/agents/${agentId}/audit/export/?range=${encodeURIComponent(exportRange)}`,
    [agentId, exportRange],
  )

  useEffect(() => {
    initialize(agentId)
    loadTimeline(agentId)
    const measure = () => {
      const bannerHeight = bannerRef.current?.offsetHeight ?? 0
      const padding = 48 // account for top/bottom page padding
      const available = window.innerHeight - bannerHeight - padding
      setTimelineMaxHeight(Math.max(420, available))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [agentId, initialize, loadTimeline])

  useEffect(() => {
    if (!agentSearchOpen) {
      agentSearchRequestId.current += 1
      setAgentSearchQuery('')
      setAgentSearchResults([])
      setAgentSearchError(null)
      setAgentSearchLoading(false)
    }
  }, [agentSearchOpen])

  useEffect(() => {
    if (!agentSearchOpen) {
      return
    }
    const query = agentSearchQuery.trim()
    if (!query) {
      agentSearchRequestId.current += 1
      setAgentSearchResults([])
      setAgentSearchError(null)
      setAgentSearchLoading(false)
      return
    }

    const requestId = agentSearchRequestId.current + 1
    agentSearchRequestId.current = requestId
    setAgentSearchLoading(true)

    const timeout = window.setTimeout(() => {
      searchStaffAgents(query, { limit: AGENT_SEARCH_LIMIT })
        .then((payload: any) => {
          if (agentSearchRequestId.current !== requestId) return
          setAgentSearchResults(payload.agents)
          setAgentSearchError(null)
        })
        .catch((error: any) => {
          if (agentSearchRequestId.current !== requestId) return
          setAgentSearchResults([])
          setAgentSearchError(error instanceof Error ? error.message : 'Unable to search agents right now.')
        })
        .finally(() => {
          if (agentSearchRequestId.current !== requestId) return
          setAgentSearchLoading(false)
        })
    }, 250)

    return () => window.clearTimeout(timeout)
  }, [agentSearchOpen, agentSearchQuery])

  const handleAgentNavigate = useCallback(
    (targetId: string) => {
      if (!targetId || targetId === agentId) {
        setAgentSearchOpen(false)
        return
      }
      window.location.assign(`/console/staff/agents/${targetId}/audit/`)
    },
    [agentId],
  )

  const handleAgentSearchSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const query = agentSearchQuery.trim()
      if (!query) return
      const matchById = agentSearchResults.find((agent) => agent.id === query)
      const matchByName = agentSearchResults.find((agent) => agent.name?.toLowerCase() === query.toLowerCase())
      const fallback = agentSearchResults.length === 1 ? agentSearchResults[0] : null
      const target = matchById || matchByName || (isUuid(query) ? { id: query } : null) || fallback
      if (target) {
        handleAgentNavigate(target.id)
      }
    },
    [agentSearchQuery, agentSearchResults, handleAgentNavigate],
  )

  const handleFilterChange = useCallback((key: string, value: boolean) => {
    setFilters((current) => ({ ...current, [key as keyof FilterState]: value }))
  }, [])

  const filterEvents = useCallback(
    (eventsToFilter: AuditEvent[]) => {
      return eventsToFilter.filter((event) => {
        const typeFilter = EVENT_TYPE_FILTERS.find((filter) => filter.matches(event))
        if (typeFilter && !filters[typeFilter.key]) {
          return false
        }
        if (event.kind === 'completion') {
          const key = (event.completion_type || '').toLowerCase()
          const completionFilter = COMPLETION_TYPE_FILTERS.find((filter) => filter.matches(key))
          if (completionFilter && !filters[completionFilter.key]) {
            return false
          }
        }
        return true
      })
    },
    [filters],
  )

  const filteredEvents = useMemo(() => filterEvents(events), [events, filterEvents])

  const messageEvents = useMemo(
    () => filteredEvents.filter((event) => event.kind === 'message') as AuditMessageEvent[],
    [filteredEvents],
  )

  const activeMessageIndex = useMemo(
    () => messageEvents.findIndex((event) => event.id === activeMessageId),
    [messageEvents, activeMessageId],
  )
  const messageEventIds = useMemo(() => messageEvents.map((event) => event.id), [messageEvents])
  const messageFilterEnabled = filters.messages
  const canNavigatePrevMessage =
    messageFilterEnabled && messageEvents.length > 0 && (activeMessageIndex === -1 || activeMessageIndex > 0)
  const canNavigateNextMessage = messageFilterEnabled
    ? messageEvents.length > 0
      ? activeMessageIndex === -1 || activeMessageIndex < messageEvents.length - 1 || hasMore
      : hasMore
    : false
  const hasFilteredEvents = filteredEvents.length > 0

  useEffect(() => {
    if (editingMessage) {
      setMessageBody(editingMessage.body || '')
      setMessageModalOpen(true)
    }
  }, [editingMessage])

  const handleLoadPrompt = async (archiveId: string) => {
    setPromptState((current) => ({ ...current, [archiveId]: { loading: true } }))
    try {
      const data = await fetchPromptArchive(archiveId)
      setPromptState((current) => ({ ...current, [archiveId]: { loading: false, data } }))
    } catch (err) {
      setPromptState((current) => ({
        ...current,
        [archiveId]: { loading: false, error: err instanceof Error ? err.message : 'Failed to load prompt' },
      }))
    }
  }

  const handleProcessEvents = async () => {
    if (!agentId || processQueueing) return
    setProcessQueueing(true)
    setActionError(null)
    try {
      const payload = await triggerProcessEvents(agentId)
      const active = Boolean(payload.processing_active || payload.queued)
      setProcessingActive(active)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to queue processing')
    } finally {
      setProcessQueueing(false)
    }
  }

  const handleRunJudge = async () => {
    if (!agentId || judgeRunning) return
    setJudgeRunning(true)
    setActionError(null)
    setJudgeDecisionError(null)
    try {
      const payload = await runAgentJudge(agentId)
      if (!payload.ran) {
        setActionError(payload.status === 'llm_not_configured' ? 'Agent judge LLM is not configured.' : 'Judge did not run.')
      } else if (payload.suggestion) {
        setJudgeReviewSuggestion(payload.suggestion)
      } else if (payload.suggestion_type === 'no_action') {
        setActionError('Judge completed with no suggested action.')
      } else {
        setActionError('Judge completed without a reviewable suggestion.')
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to run judge')
    } finally {
      setJudgeRunning(false)
    }
  }

  const handleJudgeDecision = async (decision: 'approve' | 'reject') => {
    const suggestion = judgeReviewSuggestion
    if (!suggestion?.decisionApiUrl || judgeDecisionBusy) return
    setJudgeDecisionBusy(decision)
    setJudgeDecisionError(null)
    try {
      await decideAgentJudgeSuggestion(suggestion.decisionApiUrl, decision)
      setJudgeReviewSuggestion(null)
    } catch (err) {
      setJudgeDecisionError(err instanceof Error ? err.message : `Failed to ${decision} suggestion`)
    } finally {
      setJudgeDecisionBusy(null)
    }
  }

  const handleEditMessage = (message: AuditSystemMessageEvent) => {
    setEditingMessage(message)
  }

  const resetMessageForm = () => {
    setEditingMessage(null)
    setMessageBody('')
    setMessageModalOpen(false)
    setMessageError(null)
  }

  const handleSubmitMessage = async () => {
    if (!agentId) return
    if (!messageBody.trim()) {
      setMessageError('Message body is required')
      return
    }
    setMessageSubmitting(true)
    setMessageError(null)
    try {
      const payload =
        editingMessage != null
          ? await updateSystemMessage(agentId, editingMessage.id, { body: messageBody })
          : await createSystemMessage(agentId, { body: messageBody })
      dispatch(auditActions.receiveRealtimeEvent(payload))
      resetMessageForm()
    } catch (err) {
      setMessageError(err instanceof Error ? err.message : 'Failed to save system message')
    } finally {
      setMessageSubmitting(false)
    }
  }

  const handleJumpToTimestamp = useCallback(
    async (day: string) => {
      await jumpToTime(day)
      if (eventsRef.current) {
        eventsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
      } else {
        window.scrollTo({ top: 0, behavior: 'smooth' })
      }
    },
    [jumpToTime],
  )

  const handleToggleEventCollapse = useCallback((event: AuditEvent) => {
    const key = eventKeyFor(event)
    setEventCollapseOverrideKeys((current) => {
      const next = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }, [])

  const handleSetAllCollapsed = useCallback(
    (collapsed: boolean) => {
      setEventCollapseOverrideKeys(
        new Set(
          filteredEvents
            .filter((event) => isEventCollapsedByDefault(event) !== collapsed)
            .map((event) => eventKeyFor(event)),
        ),
      )
    },
    [filteredEvents],
  )

  const scrollToMessage = useCallback((messageId: string) => {
    const target = messageNodeMap.current.get(messageId)
    if (!target) return false
    const offset = (bannerRef.current?.offsetHeight ?? 0) + 24
    const top = target.getBoundingClientRect().top + window.scrollY - offset
    window.scrollTo({ top, behavior: 'smooth' })
    return true
  }, [])

  const scrollToMessageAndActivate = useCallback(
    (messageId: string) => {
      if (!scrollToMessage(messageId)) {
        return false
      }
      setActiveMessageId(messageId)
      return true
    },
    [scrollToMessage],
  )

  const handleNavigateMessage = useCallback(
    async (direction: 'prev' | 'next') => {
      if (!messageFilterEnabled) return
      let targetId = getTargetMessageId(messageEvents, direction, activeMessageId)
      if (!targetId && direction === 'next' && hasMore && !loadingRef.current) {
        await loadMore()
        const latestEvents = latestEventsRef.current
        const nextMessages = filterEvents(latestEvents).filter((event) => event.kind === 'message') as AuditMessageEvent[]
        targetId = getTargetMessageId(nextMessages, direction, activeMessageId)
      }
      if (!targetId) return
      if (!scrollToMessageAndActivate(targetId)) {
        pendingMessageScrollId.current = targetId
      }
    },
    [
      activeMessageId,
      filterEvents,
      hasMore,
      loadMore,
      messageEvents,
      messageFilterEnabled,
      scrollToMessageAndActivate,
    ],
  )

  const registerMessageRef = useCallback(
    (messageId: string) => (node: HTMLDivElement | null) => {
      const existingNode = messageNodeMap.current.get(messageId)
      if (existingNode) {
        messageIdByNode.current.delete(existingNode)
      }
      if (node) {
        messageNodeMap.current.set(messageId, node)
        messageIdByNode.current.set(node, messageId)
      } else {
        messageNodeMap.current.delete(messageId)
      }
    },
    [],
  )

  useEffect(() => {
    const container = eventsRef.current
    if (!container) return
    const nodes = Array.from(container.querySelectorAll('[data-day-marker=\"true\"]')) as HTMLElement[]
    if (!nodes.length) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting)
        if (!visible.length) return
        visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        const top = visible[0]?.target as HTMLElement | undefined
        const day = top?.dataset.day
        if (day) {
          setSelectedDay(day)
        }
      },
      {
        root: null,
        threshold: 0.3,
        rootMargin: '-10% 0px -65% 0px',
      },
    )

    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [filteredEvents, setSelectedDay])

  useEffect(() => {
  const nodes = messageEventIds
    .map((messageId) => messageNodeMap.current.get(messageId))
    .filter((node): node is HTMLDivElement => Boolean(node))
    if (!nodes.length) {
      setActiveMessageId(null)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting)
        if (!visible.length) return
        visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        const top = visible[0]?.target as HTMLElement | undefined
        const messageId = top ? messageIdByNode.current.get(top) : undefined
        if (messageId) {
          setActiveMessageId(messageId)
        }
      },
      {
        root: null,
        threshold: 0.3,
        rootMargin: '-10% 0px -65% 0px',
      },
    )

    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [messageEventIds])

  useEffect(() => {
    const pendingId = pendingMessageScrollId.current
    if (!pendingId) return
    if (scrollToMessageAndActivate(pendingId)) {
      pendingMessageScrollId.current = null
    }
  }, [messageEventIds, scrollToMessageAndActivate])

  useEffect(() => {
    loadingRef.current = loading
  }, [loading])

  useEffect(() => {
    const sentinel = loadMoreRef.current
    if (!sentinel || !hasMore) return
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0]
        if (entry.isIntersecting && !loadingRef.current) {
          loadMore()
        }
      },
      { rootMargin: '240px 0px 240px 0px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMore, loadMore])

  return (
    <div className="min-h-screen bg-white">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div
          ref={bannerRef}
          className="sticky top-3 z-20 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white/75 px-5 py-4 shadow-[0_10px_24px_rgba(15,23,42,0.08)] backdrop-blur-md"
        >
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-600">Staff Audit</div>
            <div className="flex items-center gap-2 text-2xl font-bold leading-tight text-slate-900">
              <Stethoscope className="h-6 w-6 text-slate-700" aria-hidden />
              <span>{agentName || 'Agent'}</span>
              <div className="relative">
                <button
                  type="button"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
                  onClick={() => setAgentSearchOpen((open) => !open)}
                  aria-label="Switch agent"
                  aria-expanded={agentSearchOpen}
                >
                  <ChevronDown className="h-4 w-4" aria-hidden />
                </button>
                {agentSearchOpen ? (
                  <div className="absolute left-0 z-30 mt-2 w-72 rounded-xl border border-slate-200 bg-white/95 p-3 text-sm shadow-xl backdrop-blur">
                    <form onSubmit={handleAgentSearchSubmit} className="space-y-2">
                      <label className="relative block">
                        <span className="sr-only">Search agents</span>
                        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-400">
                          <Search className="h-4 w-4" aria-hidden />
                        </span>
                        <input
                          type="search"
                          value={agentSearchQuery}
                          onChange={(event) => setAgentSearchQuery(event.target.value)}
                          placeholder="Search Agents"
                          className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-200/60"
                          autoFocus
                        />
                      </label>
                      {agentSearchLoading ? (
                        <div className="px-1 text-xs text-slate-500">Searching…</div>
                      ) : null}
                      {agentSearchError ? (
                        <div className="px-1 text-xs text-rose-600">{agentSearchError}</div>
                      ) : null}
                      {!agentSearchLoading && !agentSearchError && agentSearchQuery.trim() && !agentSearchResults.length ? (
                        <div className="px-1 text-xs text-slate-500">No matching agents.</div>
                      ) : null}
                      <div className="max-h-56 space-y-1 overflow-y-auto">
                        {agentSearchResults.map((agent) => (
                          <button
                            key={agent.id}
                            type="button"
                            className="w-full rounded-lg border border-transparent px-2 py-2 text-left text-slate-800 transition hover:border-slate-200 hover:bg-slate-900/5"
                            onClick={() => handleAgentNavigate(agent.id)}
                          >
                            <div className="text-sm font-semibold text-slate-900">{agent.name || 'Agent'}</div>
                            <div className="text-[11px] text-slate-500">{agent.id}</div>
                          </button>
                        ))}
                      </div>
                    </form>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
              onClick={handleProcessEvents}
              disabled={processQueueing || processingActive}
              title={processingActive ? 'Processing events' : processQueueing ? 'Queueing events' : 'Process events'}
              aria-label={processingActive ? 'Processing events' : processQueueing ? 'Queueing events' : 'Process events'}
            >
              <RefreshCcw
                className={`h-4 w-4 ${processingActive ? 'animate-spin' : ''}`}
                aria-hidden
              />
              {processingActive ? 'Processing…' : processQueueing ? 'Queueing…' : 'Process events'}
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm font-semibold text-violet-700 shadow-sm transition hover:border-violet-300 hover:text-violet-900 disabled:cursor-not-allowed disabled:text-violet-300"
              onClick={handleRunJudge}
              disabled={judgeRunning}
              title={judgeRunning ? 'Running judge' : 'Run judge now'}
              aria-label={judgeRunning ? 'Running judge' : 'Run judge now'}
            >
              <Brain className={`h-4 w-4 ${judgeRunning ? 'animate-pulse' : ''}`} aria-hidden />
              {judgeRunning ? 'Judging…' : 'Run judge'}
            </button>
            {adminAgentUrl ? (
              <a
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
                href={adminAgentUrl}
                target="_blank"
                rel="noreferrer"
                title="Open agent settings in Django admin"
                aria-label="Open agent settings in Django admin"
              >
                <Settings className="h-4 w-4" aria-hidden />
              </a>
            ) : null}
            <div className="inline-flex h-9 items-center overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
              <label htmlFor="audit-export-range" className="sr-only">Audit export range</label>
              <select
                id="audit-export-range"
                className="h-full border-0 bg-white px-3 text-sm font-semibold text-slate-700 outline-none transition hover:text-slate-900 focus:ring-2 focus:ring-sky-500"
                value={exportRange}
                onChange={(event) => setExportRange(event.target.value as AuditExportRangeKey)}
                aria-label="Audit export range"
              >
                {AUDIT_EXPORT_RANGES.map((range) => (
                  <option key={range.key} value={range.key}>{range.label}</option>
                ))}
              </select>
              <a
                className="inline-flex h-full w-9 items-center justify-center border-l border-slate-200 bg-white text-slate-700 transition hover:text-slate-900"
                href={auditExportUrl}
                title="Download audit export zip"
                aria-label="Download audit export zip"
              >
                <Download className="h-4 w-4" aria-hidden />
              </a>
            </div>
            <button
              type="button"
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
              onClick={() => {
                setEditingMessage(null)
                setMessageBody('')
                setMessageModalOpen(true)
                setMessageError(null)
              }}
              title="Add system message"
              aria-label="Add system message"
            >
              <Megaphone className="h-4 w-4" aria-hidden />
            </button>
            <AgentAuditFiltersMenu
              filtersOpen={filtersOpen}
              onToggle={() => setFiltersOpen((open) => !open)}
              filters={filters}
              eventFilters={EVENT_TYPE_FILTERS}
              completionFilters={COMPLETION_TYPE_FILTERS}
              onFilterChange={handleFilterChange}
            />
            <div
              className="inline-flex items-stretch overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
              role="group"
              aria-label="Message navigation"
            >
              <span className="inline-flex items-center px-3 py-2 text-sm font-semibold text-slate-700">
                Message
              </span>
              <button
                type="button"
                className="inline-flex items-center border-l border-slate-200 px-3 py-2 text-slate-700 transition hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
                onClick={() => handleNavigateMessage('prev')}
                disabled={!canNavigatePrevMessage}
                aria-label="Previous message"
              >
                <ChevronUp className="h-4 w-4" aria-hidden />
              </button>
              <button
                type="button"
                className="inline-flex items-center border-l border-slate-200 px-3 py-2 text-slate-700 transition hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
                onClick={() => handleNavigateMessage('next')}
                disabled={!canNavigateNextMessage}
                aria-label="Next message"
              >
                <ChevronDown className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
              onClick={() => handleSetAllCollapsed(false)}
              disabled={!hasFilteredEvents}
            >
              <ListChevronsUpDown className="h-4 w-4" aria-hidden />
              Expand
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-400"
              onClick={() => handleSetAllCollapsed(true)}
              disabled={!hasFilteredEvents}
            >
              <ListChevronsDownUp className="h-4 w-4" aria-hidden />
              Collapse
            </button>
          </div>
        </div>

        {actionError ? <div className="mt-2 text-sm text-rose-600">{actionError}</div> : null}
        {error ? <div className="mt-4 text-sm text-rose-600">{error}</div> : null}
        {loading && !events.length ? <div className="mt-6 text-sm text-slate-700">Loading audit data…</div> : null}

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-start">
          <div ref={eventsRef} className="space-y-4">
            {filteredEvents.map((event) => {
              const timestamp = (event as any).timestamp as string | null | undefined
              const parsedTimestamp = timestamp ? new Date(timestamp) : null
              const day =
                parsedTimestamp && !Number.isNaN(parsedTimestamp.getTime())
                  ? `${parsedTimestamp.getFullYear()}-${String(parsedTimestamp.getMonth() + 1).padStart(2, '0')}-${String(parsedTimestamp.getDate()).padStart(2, '0')}`
                  : null
              const eventKey = eventKeyFor(event)
              const collapsed = eventCollapseOverrideKeys.has(eventKey)
                ? !isEventCollapsedByDefault(event)
                : isEventCollapsedByDefault(event)
              const messageRef = event.kind === 'message' ? registerMessageRef((event as AuditMessageEvent).id) : undefined
              const wrapperProps = { ...(day ? { 'data-day-marker': 'true', 'data-day': day } : {}) }

              if (event.kind === 'completion') {
                return (
                  <div key={eventKey} {...wrapperProps}>
                    <CompletionCard
                      completion={event}
                      promptState={event.prompt_archive?.id ? promptState[event.prompt_archive.id] : undefined}
                      onLoadPrompt={handleLoadPrompt}
                      collapsed={collapsed}
                      onToggle={() => handleToggleEventCollapse(event)}
                    />
                  </div>
                )
              }
              if (event.kind === 'tool_call') {
                return (
                  <div key={eventKey} {...wrapperProps}>
                    <ToolCallRow tool={event as AuditToolCallEvent} collapsed={collapsed} onToggle={() => handleToggleEventCollapse(event)} />
                  </div>
                )
              }
              if (event.kind === 'message') {
                return (
                  <div key={eventKey} {...wrapperProps} ref={messageRef}>
                    <MessageRow message={event as AuditMessageEvent} collapsed={collapsed} onToggle={() => handleToggleEventCollapse(event)} />
                  </div>
                )
              }
              if (event.kind === 'error') {
                return (
                  <div key={eventKey} {...wrapperProps}>
                    <ErrorRow error={event as AuditErrorEvent} collapsed={collapsed} onToggle={() => handleToggleEventCollapse(event)} />
                  </div>
                )
              }
              if (event.kind === 'system_message') {
                return (
                  <div key={eventKey} {...wrapperProps}>
                    <SystemMessageCard
                      message={event as AuditSystemMessageEvent}
                      onEdit={handleEditMessage}
                      collapsed={collapsed}
                      onToggle={() => handleToggleEventCollapse(event)}
                      renderBody={(body) =>
                        renderHtmlOrText(body, {
                          htmlClassName: 'prose prose-sm max-w-none rounded-md bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
                          textClassName: 'whitespace-pre-wrap break-words rounded-md bg-amber-50/60 px-3 py-2 text-sm text-slate-900 shadow-inner shadow-amber-200/60',
                        })
                      }
                    />
                  </div>
                )
              }
              if (event.kind === 'step') {
                return (
                  <div key={eventKey} {...wrapperProps}>
                    <StepRow step={event as AuditStepEvent} collapsed={collapsed} onToggle={() => handleToggleEventCollapse(event)} />
                  </div>
                )
              }
              return null
            })}
            {!filteredEvents.length ? (
              <div className="text-sm text-slate-600">
                {events.length ? 'No events match the current filters.' : 'No events yet.'}
              </div>
            ) : null}
            <div ref={loadMoreRef} className="h-6 w-full" aria-hidden="true" />
          </div>

          <div
            className="hidden lg:block lg:sticky lg:top-[112px] lg:min-h-[520px] lg:pt-4"
            style={timelineMaxHeight ? { maxHeight: `${timelineMaxHeight}px` } : undefined}
          >
            <AuditTimeline onSelect={handleJumpToTimestamp} />
          </div>
        </div>
      </div>

      {judgeReviewSuggestion ? (
        <Modal
          title="Review judge suggestion"
          subtitle="Approve to activate the directive for the agent, or reject to discard it."
          onClose={() => {
            setJudgeDecisionError(null)
          }}
          dismissible={false}
          icon={Brain}
          iconBgClass="bg-violet-100"
          iconColorClass="text-violet-700"
          widthClass="sm:max-w-3xl"
          footer={
            <div className="flex flex-col gap-3 sm:flex-row-reverse sm:items-center">
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-md bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:bg-violet-400"
                onClick={() => void handleJudgeDecision('approve')}
                disabled={Boolean(judgeDecisionBusy)}
              >
                {judgeDecisionBusy === 'approve' ? 'Approving...' : 'Approve suggestion'}
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-md border border-rose-200 bg-white px-4 py-2 text-sm font-semibold text-rose-700 shadow-sm transition hover:border-rose-300 hover:text-rose-800 disabled:cursor-not-allowed disabled:text-rose-300"
                onClick={() => void handleJudgeDecision('reject')}
                disabled={Boolean(judgeDecisionBusy)}
              >
                {judgeDecisionBusy === 'reject' ? 'Rejecting...' : 'Reject'}
              </button>
            </div>
          }
        >
          <div className="space-y-4">
            <div className="rounded-lg border border-violet-200 bg-violet-50/70 px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-violet-700">
                {formatJudgeSuggestionType(judgeReviewSuggestion.suggestionType)}
              </div>
              <h3 className="mt-1 text-base font-semibold text-slate-900">{judgeReviewSuggestion.title}</h3>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{judgeReviewSuggestion.message}</p>
              {judgeReviewSuggestion.recommendedTier ? (
                <p className="mt-2 text-xs font-medium uppercase tracking-[0.14em] text-violet-700">
                  Recommended: {judgeReviewSuggestion.recommendedTier.replace(/_/g, ' ')}
                </p>
              ) : null}
            </div>

            {judgeReviewSuggestion.agentDirective ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-4 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-amber-800">Directive to activate on approval</div>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">{judgeReviewSuggestion.agentDirective}</p>
              </div>
            ) : null}

            <details className="rounded-lg border border-slate-200 bg-white px-4 py-3">
              <summary className="cursor-pointer text-sm font-semibold text-slate-800">Thinking</summary>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-950 px-3 py-3 text-xs leading-5 text-slate-100">
                {judgeReviewSuggestion.reasoning?.trim() || 'No reasoning was captured for this judge completion.'}
              </pre>
            </details>

            {judgeDecisionError ? <div className="text-sm text-rose-600">{judgeDecisionError}</div> : null}
          </div>
        </Modal>
      ) : null}

      {messageModalOpen ? (
        <Modal
          title={editingMessage ? 'Edit system message' : 'Add system message'}
          subtitle="System directives are delivered into the agent's unified history."
          onClose={resetMessageForm}
          icon={Megaphone}
          iconBgClass="bg-amber-100"
          iconColorClass="text-amber-700"
          widthClass="sm:max-w-2xl"
          footer={
            <div className="flex flex-col gap-3 sm:flex-row-reverse sm:items-center">
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-md bg-amber-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-amber-400"
                onClick={handleSubmitMessage}
                disabled={messageSubmitting}
              >
                {messageSubmitting ? 'Saving…' : editingMessage ? 'Update message' : 'Add message'}
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
                onClick={resetMessageForm}
                disabled={messageSubmitting}
              >
                Cancel
              </button>
            </div>
          }
        >
          <div className="space-y-3">
            <label className="block text-sm font-semibold text-slate-800">
              Message
              <textarea
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-inner shadow-slate-200/60 focus:border-slate-400 focus:outline-none focus:ring-0"
                rows={6}
                value={messageBody}
                onChange={(e) => setMessageBody(e.target.value)}
                placeholder="Enter the directive to deliver to this agent..."
                disabled={messageSubmitting}
              />
            </label>
            {messageError ? <div className="text-sm text-rose-600">{messageError}</div> : null}
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
