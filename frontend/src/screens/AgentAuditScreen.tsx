import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Copy, Cpu, Filter, Megaphone, MessageCircle, RefreshCcw, Stethoscope, StepForward, Wrench, type LucideIcon } from 'lucide-react'
import { useAgentAuditStore } from '../stores/agentAuditStore'
import { useAgentAuditSocket } from '../hooks/useAgentAuditSocket'
import type { AuditCompletionEvent, AuditToolCallEvent, AuditMessageEvent, AuditStepEvent, PromptArchive, AuditSystemMessageEvent } from '../types/agentAudit'
import { createSystemMessage, fetchPromptArchive, triggerProcessEvents, updateSystemMessage } from '../api/agentAudit'
import { StructuredDataTable } from '../components/common/StructuredDataTable'
import { normalizeStructuredValue } from '../components/agentChat/toolDetails'
import { AuditTimeline } from '../components/agentAudit/AuditTimeline'
import { looksLikeHtml, sanitizeHtml } from '../util/sanitize'
import { Modal } from '../components/common/Modal'
import { SystemMessageCard } from '../components/agentAudit/SystemMessageCard'

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

function ToolCallRow({ tool }: { tool: AuditToolCallEvent }) {
  const [expanded, setExpanded] = useState(false)
  const toggle = () => setExpanded((prev) => !prev)
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
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <IconCircle icon={Wrench} bgClass="bg-indigo-50" textClass="text-indigo-700" />
          <div>
            <div className="text-sm font-semibold text-slate-900">{tool.tool_name || 'Tool call'}</div>
            <div className="text-xs text-slate-600">{tool.timestamp ? new Date(tool.timestamp).toLocaleString() : '—'}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {resultPreview ? <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700">Tool</span> : null}
          <button
            type="button"
            onClick={toggle}
            className="rounded-md bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white hover:bg-slate-800"
          >
            {expanded ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      {!expanded && resultPreview ? (
        <div className="mt-2 text-sm text-slate-700">{resultPreview}</div>
      ) : null}
      {expanded && parsedParameters ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Parameters</div>
          {renderValue(parsedParameters)}
        </div>
      ) : null}
      {expanded && parsedResult ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Result</div>
          {renderValue(parsedResult)}
        </div>
      ) : null}
    </div>
  )
}

function MessageRow({ message }: { message: AuditMessageEvent }) {
  const renderBody = () => {
    const body = message.body_text
    if (!body) return null
    return renderHtmlOrText(body)
  }
  const attachments = message.attachments || []

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <IconCircle icon={MessageCircle} bgClass="bg-emerald-50" textClass="text-emerald-700" />
          <div>
            <div className="text-sm font-semibold text-slate-900">
              {message.is_outbound ? 'Agent → User' : 'User → Agent'}{' '}
              <span className="text-xs font-normal text-slate-500">({message.channel || 'web'})</span>
            </div>
            <div className="text-xs text-slate-600">{message.timestamp ? new Date(message.timestamp).toLocaleString() : '—'}</div>
          </div>
        </div>
        <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">Message</span>
      </div>
      <div className="mt-2">{renderBody()}</div>
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
    </div>
  )
}

function StepRow({ step }: { step: AuditStepEvent }) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <IconCircle icon={StepForward} bgClass="bg-slate-100" textClass="text-slate-700" />
          <div>
            <div className="text-sm font-semibold text-slate-900">Step</div>
            <div className="text-xs text-slate-600">{step.timestamp ? new Date(step.timestamp).toLocaleString() : '—'}</div>
          </div>
        </div>
        {step.is_system ? (
          <span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-700">
            {step.system_code || 'System'}
          </span>
        ) : (
          <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">Step</span>
        )}
      </div>
      {step.description ? <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-800">{step.description}</div> : null}
      {step.is_system && step.system_notes ? (
        <div className="mt-2 rounded-md bg-slate-50 px-2 py-1 text-[12px] text-slate-700">{step.system_notes}</div>
      ) : null}
    </div>
  )
}

function CompletionCard({
  completion,
  promptState,
  onLoadPrompt,
}: {
  completion: AuditCompletionEvent
  promptState: PromptState | undefined
  onLoadPrompt: (archiveId: string) => void
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
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <IconCircle icon={Cpu} bgClass="bg-sky-50" textClass="text-sky-700" />
          <div>
            <div className="text-sm font-semibold text-slate-900">
              {completionLabel} · {completion.llm_model || 'Unknown model'}{' '}
              <span className="text-xs font-normal text-slate-500">({completion.llm_provider || 'provider'})</span>
            </div>
            <div className="text-xs text-slate-600">{completion.timestamp ? new Date(completion.timestamp).toLocaleString() : '—'}</div>
          </div>
        </div>
        <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-700">LLM</span>
      </div>

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
  const [timelineMaxHeight, setTimelineMaxHeight] = useState<number | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filters, setFilters] = useState({
    hideTagGeneration: false,
    hideMiniDescription: false,
    hideShortDescription: false,
    hideSystemSteps: false,
    hideAgentSteps: false,
    hideToolCalls: false,
  })
  const [processQueueing, setProcessQueueing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [messageModalOpen, setMessageModalOpen] = useState(false)
  const [editingMessage, setEditingMessage] = useState<AuditSystemMessageEvent | null>(null)
  const [messageBody, setMessageBody] = useState('')
  const [messageActive, setMessageActive] = useState(true)
  const [messageSubmitting, setMessageSubmitting] = useState(false)
  const [messageError, setMessageError] = useState<string | null>(null)
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

  const filteredEvents = useMemo(() => {
    return events.filter((event) => {
      if (event.kind === 'tool_call') {
        if (filters.hideToolCalls) return false
        return true
      }
      if (event.kind === 'step') {
        if (event.is_system && filters.hideSystemSteps) return false
        if (!event.is_system && filters.hideAgentSteps) return false
        return true
      }
      if (event.kind === 'completion') {
        const key = (event.completion_type || '').toLowerCase()
        if (filters.hideTagGeneration && key === 'tag') return false
        if (filters.hideMiniDescription && key === 'mini_description') return false
        if (filters.hideShortDescription && key === 'short_description') return false
        return true
      }
      return true
    })
  }, [events, filters])

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
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
              onClick={handleProcessEvents}
              disabled={processQueueing || processingActive}
            >
              <RefreshCcw
                className={`h-4 w-4 ${processingActive ? 'animate-spin' : ''}`}
                aria-hidden
              />
              {processingActive ? 'Processing…' : processQueueing ? 'Queueing…' : 'Process events'}
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800 shadow-sm transition hover:border-amber-300 hover:text-amber-900"
              onClick={() => {
                setEditingMessage(null)
                setMessageBody('')
                setMessageActive(true)
                setMessageModalOpen(true)
                setMessageError(null)
              }}
            >
              <Megaphone className="h-4 w-4" aria-hidden />
              Add system message
            </button>
            <div className="relative">
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
                onClick={() => setFiltersOpen((open) => !open)}
                aria-expanded={filtersOpen}
              >
                <Filter className="h-4 w-4" aria-hidden />
                Filters
              </button>
              {filtersOpen ? (
                <div className="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-slate-200 bg-white/95 p-3 text-sm shadow-xl backdrop-blur">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Hide events</div>
                  <div className="space-y-2 text-slate-800">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideTagGeneration}
                        onChange={(e) => setFilters((f) => ({ ...f, hideTagGeneration: e.target.checked }))}
                      />
                      <span>Tag generation</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideMiniDescription}
                        onChange={(e) => setFilters((f) => ({ ...f, hideMiniDescription: e.target.checked }))}
                      />
                      <span>Mini description</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideShortDescription}
                        onChange={(e) => setFilters((f) => ({ ...f, hideShortDescription: e.target.checked }))}
                      />
                      <span>Short description</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideSystemSteps}
                        onChange={(e) => setFilters((f) => ({ ...f, hideSystemSteps: e.target.checked }))}
                      />
                      <span>System steps</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideAgentSteps}
                        onChange={(e) => setFilters((f) => ({ ...f, hideAgentSteps: e.target.checked }))}
                      />
                      <span>Persistent agent steps</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                        checked={filters.hideToolCalls}
                        onChange={(e) => setFilters((f) => ({ ...f, hideToolCalls: e.target.checked }))}
                      />
                      <span>Tool calls</span>
                    </label>
                  </div>
                </div>
              ) : null}
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-800">{agentId}</div>
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
              onClick={() => {
                if (!agentId) return
                navigator.clipboard?.writeText(agentId).catch(() => {})
              }}
              aria-label="Copy agent id"
            >
              <Copy className="h-4 w-4" aria-hidden />
              Copy
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
              const wrapperProps = day ? { 'data-day-marker': 'true', 'data-day': day } : {}

              if (event.kind === 'completion') {
                return (
                  <div key={event.id} {...wrapperProps}>
                    <CompletionCard
                      completion={event}
                      promptState={event.prompt_archive?.id ? promptState[event.prompt_archive.id] : undefined}
                      onLoadPrompt={handleLoadPrompt}
                    />
                  </div>
                )
              }
              if (event.kind === 'tool_call') {
                return (
                  <div key={event.id} {...wrapperProps}>
                    <ToolCallRow tool={event as AuditToolCallEvent} />
                  </div>
                )
              }
              if (event.kind === 'message') {
                return (
                  <div key={event.id} {...wrapperProps}>
                    <MessageRow message={event as AuditMessageEvent} />
                  </div>
                )
              }
              if (event.kind === 'system_message') {
                return (
                  <div key={event.id} {...wrapperProps}>
                    <SystemMessageCard
                      message={event as AuditSystemMessageEvent}
                      onEdit={handleEditMessage}
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
                  <div key={event.id} {...wrapperProps}>
                    <StepRow step={event as AuditStepEvent} />
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
