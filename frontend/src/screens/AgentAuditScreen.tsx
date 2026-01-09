import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Cpu,
  Filter,
  ListChevronsDownUp,
  ListChevronsUpDown,
  Megaphone,
  MessageCircle,
  RefreshCcw,
  Search,
  Stethoscope,
  StepForward,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useAgentAuditStore } from '../stores/agentAuditStore'
import { useAgentAuditSocket } from '../hooks/useAgentAuditSocket'
import type { AuditCompletionEvent, AuditToolCallEvent, AuditMessageEvent, AuditStepEvent, PromptArchive, AuditSystemMessageEvent, AuditEvent } from '../types/agentAudit'
import { createSystemMessage, fetchPromptArchive, searchStaffAgents, triggerProcessEvents, updateSystemMessage, type StaffAgentSearchResult } from '../api/agentAudit'
import { StructuredDataTable } from '../components/common/StructuredDataTable'
import { normalizeStructuredValue } from '../components/agentChat/toolDetails'
import { AuditTimeline } from '../components/agentAudit/AuditTimeline'
import { looksLikeHtml, sanitizeHtml } from '../util/sanitize'
import { Modal } from '../components/common/Modal'
import { SystemMessageCard } from '../components/agentAudit/SystemMessageCard'
import { MessageContent } from '../components/agentChat/MessageContent'
import { EventHeader } from '../components/agentAudit/EventHeader'

type AgentAuditScreenProps = {
  agentId: string
  agentName?: string | null
  agentColor?: string | null
}

type PromptState = {
  loading: boolean
  data?: PromptArchive
  error?: string
}

function eventKeyFor(event: AuditEvent): string {
  const id = 'id' in event ? event.id : event.run_id
  return `${event.kind}:${id ?? 'unknown'}`
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
  systemMessages: true,
  systemSteps: true,
  agentSteps: true,
  tagGeneration: true,
  miniDescription: true,
  shortDescription: true,
} as const

const AGENT_SEARCH_LIMIT = 8
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

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

function renderHtmlOrText(
  value: string,
  {
    htmlClassName = 'prose prose-sm max-w-none rounded-md bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
    textClassName = 'whitespace-pre-wrap break-words text-sm text-slate-800',
  }: { htmlClassName?: string; textClassName?: string } = {},
) {
  if (looksLikeHtml(value)) {
    return <div className={htmlClassName} dangerouslySetInnerHTML={{ __html: sanitizeHtml(value) }} />
  }
  return <div className={textClassName}>{value}</div>
}

function IconCircle({ icon: Icon, bgClass, textClass }: { icon: LucideIcon; bgClass: string; textClass: string }) {
  return (
    <div className={`mt-0.5 flex h-9 w-9 items-center justify-center rounded-full ${bgClass} ${textClass}`}>
      <Icon className="h-4 w-4" aria-hidden />
    </div>
  )
}

function TokenPill({ label, value }: { label: string; value: number | null | undefined }) {
  if (value == null) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-1 text-xs font-medium text-slate-800">
      <span className="text-[10px] uppercase tracking-wide text-slate-600">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  )
}

function ToolCallRow({
  tool,
  collapsed,
  onToggle,
}: {
  tool: AuditToolCallEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  const [expanded, setExpanded] = useState(true)
  const isControlled = collapsed !== undefined
  const isExpanded = isControlled ? !collapsed : expanded
  const toggle = () => {
    if (isControlled) {
      onToggle?.()
    } else {
      setExpanded((prev) => !prev)
    }
  }
  const resultPreview = useMemo(() => {
    if (!tool.result) return null
    const trimmed = tool.result.length > 160 ? `${tool.result.slice(0, 160)}…` : tool.result
    return trimmed
  }, [tool.result])

  const parsedParameters = useMemo(() => {
    if (tool.parameters === null || tool.parameters === undefined) return null
    try {
      return normalizeStructuredValue(tool.parameters, { depth: 0, maxDepth: 6, seen: new WeakSet<object>() })
    } catch {
      return tool.parameters
    }
  }, [tool.parameters])

  const parsedResult = useMemo(() => {
    if (tool.result === null || tool.result === undefined) return null
    let value: unknown = tool.result
    if (typeof value === 'string') {
      try {
        const maybeJson = JSON.parse(value)
        value = maybeJson
      } catch {
        // leave as string
      }
    }
    try {
      return normalizeStructuredValue(value, { depth: 0, maxDepth: 6, seen: new WeakSet<object>() })
    } catch {
      return value
    }
  }, [tool.result])

  const renderValue = (value: unknown) => {
    if (value === null || value === undefined) return null
    if (typeof value === 'string') {
      return renderHtmlOrText(value, {
        htmlClassName: 'prose prose-sm max-w-none rounded-md bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
        textClassName: 'rounded-md bg-indigo-50 px-2 py-1 text-[12px] text-slate-800',
      })
    }
    return <StructuredDataTable value={value} />
  }

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={Wrench} bgClass="bg-indigo-50" textClass="text-indigo-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">{tool.tool_name || 'Tool call'}</div>
              <div className="text-xs text-slate-600">{tool.timestamp ? new Date(tool.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={resultPreview ? <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700">Tool</span> : null}
        collapsed={!isExpanded}
        onToggle={toggle}
      />
      {!isExpanded && resultPreview ? (
        <div className="mt-2 text-sm text-slate-700">{resultPreview}</div>
      ) : null}
      {isExpanded && parsedParameters ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Parameters</div>
          {renderValue(parsedParameters)}
        </div>
      ) : null}
      {isExpanded && parsedResult ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Result</div>
          {renderValue(parsedResult)}
        </div>
      ) : null}
    </div>
  )
}

function MessageRow({
  message,
  collapsed = false,
  onToggle,
}: {
  message: AuditMessageEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  const htmlBody = message.body_html && looksLikeHtml(message.body_html) ? message.body_html : null
  const textBody = message.body_text || (htmlBody ? null : message.body_html)
  const hasBody = Boolean(htmlBody || (textBody && textBody.trim().length > 0))
  const attachments = message.attachments || []

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={MessageCircle} bgClass="bg-emerald-50" textClass="text-emerald-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">
                {message.is_outbound ? 'Agent → User' : 'User → Agent'}{' '}
                <span className="text-xs font-normal text-slate-500">({message.channel || 'web'})</span>
              </div>
              <div className="text-xs text-slate-600">{message.timestamp ? new Date(message.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={<span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">Message</span>}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {!collapsed ? (
        <>
          {hasBody ? (
            <div className="mt-2 prose prose-sm max-w-none text-slate-800">
              <MessageContent bodyHtml={htmlBody} bodyText={textBody} showEmptyState={false} />
            </div>
          ) : null}
          {attachments.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {attachments.map((attachment) => {
                const href = attachment.download_url || attachment.url
                const label = attachment.filespace_path || attachment.filename
                return (
                  <a
                    key={attachment.id}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                    title={attachment.filespace_path || attachment.filename}
                  >
                    <span className="max-w-[240px] truncate">
                      {label}
                    </span>
                    {attachment.file_size_label ? <span className="text-indigo-500">{attachment.file_size_label}</span> : null}
                  </a>
                )
              })}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function StepRow({
  step,
  collapsed = false,
  onToggle,
}: {
  step: AuditStepEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={StepForward} bgClass="bg-slate-100" textClass="text-slate-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">Step</div>
              <div className="text-xs text-slate-600">{step.timestamp ? new Date(step.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={
          step.is_system ? (
            <span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-700">
              {step.system_code || 'System'}
            </span>
          ) : (
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">Step</span>
          )
        }
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {!collapsed ? (
        <>
          {step.description ? <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-800">{step.description}</div> : null}
          {step.is_system && step.system_notes ? (
            <div className="mt-2 rounded-md bg-slate-50 px-2 py-1 text-[12px] text-slate-700">{step.system_notes}</div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function CompletionCard({
  completion,
  promptState,
  onLoadPrompt,
  collapsed = false,
  onToggle,
}: {
  completion: AuditCompletionEvent
  promptState: PromptState | undefined
  onLoadPrompt: (archiveId: string) => void
  collapsed?: boolean
  onToggle?: () => void
}) {
  const archiveId = completion.prompt_archive?.id
  const promptPayload = archiveId ? promptState?.data?.payload : null
  const systemPrompt = promptPayload?.system_prompt
  const userPrompt = promptPayload?.user_prompt
  const [expanded, setExpanded] = useState(false)

  const copyText = async (text?: string | null) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch (err) {
      console.error('Copy failed', err)
    }
  }

  const completionLabel = useMemo(() => {
    const key = (completion.completion_type || '').toLowerCase()
    switch (key) {
      case 'orchestrator':
        return 'Orchestrator'
      case 'compaction':
        return 'Comms Compaction'
      case 'step_compaction':
        return 'Step Compaction'
      case 'tag':
        return 'Tag Generation'
      case 'short_description':
        return 'Short Description Generation'
      case 'mini_description':
        return 'Mini Description Generation'
      case 'tool_search':
        return 'Tool Search'
      default:
        return 'Other'
    }
  }, [completion.completion_type])

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white px-4 py-3 shadow-[0_1px_3px_rgba(15,23,42,0.1)]">
      <EventHeader
        className="gap-4"
        left={
          <>
            <IconCircle icon={Cpu} bgClass="bg-sky-50" textClass="text-sky-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">
                {completionLabel} · {completion.llm_model || 'Unknown model'}{' '}
                <span className="text-xs font-normal text-slate-500">({completion.llm_provider || 'provider'})</span>
              </div>
              <div className="text-xs text-slate-600">{completion.timestamp ? new Date(completion.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={<span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-700">LLM</span>}
        collapsed={collapsed}
        onToggle={onToggle}
      />

      {!collapsed ? (
        <>
          <div className="mt-3 flex flex-wrap gap-2">
            <TokenPill label="Prompt" value={completion.prompt_tokens} />
            <TokenPill label="Output" value={completion.completion_tokens} />
            <TokenPill label="Total" value={completion.total_tokens} />
            <TokenPill label="Cached" value={completion.cached_tokens} />
          </div>

          {archiveId ? (
            <div className="mt-3 rounded-lg border border-slate-200/70 bg-indigo-50/70 px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-700">Prompt</div>
                <button
                  type="button"
                  className="rounded-md bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-slate-800"
                  onClick={() => {
                    const next = !expanded
                    setExpanded(next)
                    if (next && !promptPayload && !promptState?.loading) {
                      onLoadPrompt(archiveId)
                    }
                  }}
                  disabled={promptState?.loading && !expanded}
                >
                  {expanded ? 'Collapse' : promptState?.loading ? 'Loading…' : 'Expand'}
                </button>
              </div>
              {promptState?.error ? <div className="mt-2 text-xs text-rose-600">{promptState.error}</div> : null}
              {expanded && promptPayload ? (
                <div className="mt-2 space-y-2">
                  {systemPrompt ? (
                    <div>
                      <div className="mb-1 flex items-center justify-between text-xs font-semibold text-slate-700">
                        <span>System Prompt</span>
                        <button
                          type="button"
                          className="rounded bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white hover:bg-slate-800"
                          onClick={() => copyText(systemPrompt)}
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="whitespace-pre-wrap break-words rounded-md bg-white px-2 py-2 text-[12px] text-slate-800 shadow-inner shadow-slate-200/80">
                        {systemPrompt}
                      </pre>
                    </div>
                  ) : null}
                  {userPrompt ? (
                    <div>
                      <div className="mb-1 flex items-center justify-between text-xs font-semibold text-slate-700">
                        <span>User Prompt</span>
                        <button
                          type="button"
                          className="rounded bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white hover:bg-slate-800"
                          onClick={() => copyText(userPrompt)}
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="whitespace-pre-wrap break-words rounded-md bg-white px-2 py-2 text-[12px] text-slate-800 shadow-inner shadow-slate-200/80">
                        {userPrompt}
                      </pre>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {completion.tool_calls && completion.tool_calls.length ? (
            <div className="mt-4 space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-700">Tool Calls</div>
              {completion.tool_calls.map((tool) => (
                <ToolCallRow key={tool.id} tool={tool} />
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

export function AgentAuditScreen({ agentId, agentName }: AgentAuditScreenProps) {
  const {
    initialize,
    events,
    loading,
    error,
    loadMore,
    hasMore,
    loadTimeline,
    timeline,
    timelineLoading,
    timelineError,
    jumpToTime,
    selectedTimestamp: selectedDay,
    processingActive,
    setSelectedDay,
    setProcessingActive,
  } = useAgentAuditStore((state) => state)
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
  const [collapsedEventKeys, setCollapsedEventKeys] = useState<Set<string>>(() => new Set())
  const [processQueueing, setProcessQueueing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [messageModalOpen, setMessageModalOpen] = useState(false)
  const [editingMessage, setEditingMessage] = useState<AuditSystemMessageEvent | null>(null)
  const [messageBody, setMessageBody] = useState('')
  const [messageActive, setMessageActive] = useState(true)
  const [messageSubmitting, setMessageSubmitting] = useState(false)
  const [messageError, setMessageError] = useState<string | null>(null)
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null)
  const [agentSearchOpen, setAgentSearchOpen] = useState(false)
  const [agentSearchQuery, setAgentSearchQuery] = useState('')
  const [agentSearchResults, setAgentSearchResults] = useState<StaffAgentSearchResult[]>([])
  const [agentSearchLoading, setAgentSearchLoading] = useState(false)
  const [agentSearchError, setAgentSearchError] = useState<string | null>(null)
  const pendingMessageScrollId = useRef<string | null>(null)
  useAgentAuditSocket(agentId)

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
        .then((payload) => {
          if (agentSearchRequestId.current !== requestId) return
          setAgentSearchResults(payload.agents)
          setAgentSearchError(null)
        })
        .catch((error) => {
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
      setMessageActive(editingMessage.is_active)
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

  const handleEditMessage = (message: AuditSystemMessageEvent) => {
    setEditingMessage(message)
  }

  const resetMessageForm = () => {
    setEditingMessage(null)
    setMessageBody('')
    setMessageActive(true)
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
          ? await updateSystemMessage(agentId, editingMessage.id, { body: messageBody, is_active: messageActive })
          : await createSystemMessage(agentId, { body: messageBody, is_active: messageActive })
      useAgentAuditStore.getState().receiveRealtimeEvent(payload)
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
    setCollapsedEventKeys((current) => {
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
      if (!collapsed) {
        setCollapsedEventKeys(new Set())
        return
      }
      setCollapsedEventKeys(new Set(filteredEvents.map((event) => eventKeyFor(event))))
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
        const latestEvents = useAgentAuditStore.getState().events
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
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
              onClick={() => {
                setEditingMessage(null)
                setMessageBody('')
                setMessageActive(true)
                setMessageModalOpen(true)
                setMessageError(null)
              }}
              title="Add system message"
              aria-label="Add system message"
            >
              <Megaphone className="h-4 w-4" aria-hidden />
            </button>
            <div className="relative">
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
                onClick={() => setFiltersOpen((open) => !open)}
                aria-expanded={filtersOpen}
                title="Filters"
                aria-label="Filters"
              >
                <Filter className="h-4 w-4" aria-hidden />
              </button>
              {filtersOpen ? (
                <div className="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-slate-200 bg-white/95 p-3 text-sm shadow-xl backdrop-blur">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Event types</div>
                  <div className="space-y-2 text-slate-800">
                    {EVENT_TYPE_FILTERS.map((filter) => (
                      <label key={filter.key} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                          checked={filters[filter.key]}
                          onChange={(e) => setFilters((current) => ({ ...current, [filter.key]: e.target.checked }))}
                        />
                        <span>{filter.label}</span>
                      </label>
                    ))}
                  </div>
                  <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Completion types</div>
                  <div className="mt-2 space-y-2 text-slate-800">
                    {COMPLETION_TYPE_FILTERS.map((filter) => (
                      <label key={filter.key} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                          checked={filters[filter.key]}
                          onChange={(e) => setFilters((current) => ({ ...current, [filter.key]: e.target.checked }))}
                          disabled={!filters.completions}
                        />
                        <span className={filters.completions ? '' : 'text-slate-400'}>{filter.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
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
              const collapsed = collapsedEventKeys.has(eventKey)
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
            className="lg:sticky lg:top-[112px] lg:min-h-[520px] lg:pt-4"
            style={timelineMaxHeight ? { maxHeight: `${timelineMaxHeight}px` } : undefined}
          >
            <AuditTimeline buckets={timeline} loading={timelineLoading} error={timelineError} selectedDay={selectedDay} onSelect={handleJumpToTimestamp} processingActive={processingActive} />
          </div>
        </div>
      </div>

      {messageModalOpen ? (
        <Modal
          title={editingMessage ? 'Edit system message' : 'Add system message'}
          subtitle="System directives are injected ahead of the agent instructions."
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
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300 text-amber-700 focus:ring-amber-600"
                checked={messageActive}
                onChange={(e) => setMessageActive(e.target.checked)}
                disabled={messageSubmitting}
              />
              Keep active for future prompts
            </label>
            {messageError ? <div className="text-sm text-rose-600">{messageError}</div> : null}
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
