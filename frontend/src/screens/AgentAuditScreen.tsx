import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAgentAuditStore } from '../stores/agentAuditStore'
import { useAgentAuditSocket } from '../hooks/useAgentAuditSocket'
import type { AuditCompletionEvent, AuditToolCallEvent, AuditMessageEvent, AuditStepEvent, PromptArchive } from '../types/agentAudit'
import { fetchPromptArchive } from '../api/agentAudit'
import { StructuredDataTable } from '../components/common/StructuredDataTable'
import { normalizeStructuredValue } from '../components/agentChat/toolDetails'
import { AuditTimeline } from '../components/agentAudit/AuditTimeline'

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
      return <div className="rounded-md bg-indigo-50 px-2 py-1 text-[12px] text-slate-800">{value}</div>
    }
    return <StructuredDataTable value={value} />
  }

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">{tool.tool_name || 'Tool call'}</div>
          <div className="text-xs text-slate-600">{tool.timestamp ? new Date(tool.timestamp).toLocaleString() : '—'}</div>
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
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">
            {message.is_outbound ? 'Agent → User' : 'User → Agent'}{' '}
            <span className="text-xs font-normal text-slate-500">({message.channel || 'web'})</span>
          </div>
          <div className="text-xs text-slate-600">{message.timestamp ? new Date(message.timestamp).toLocaleString() : '—'}</div>
        </div>
        <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">Message</span>
      </div>
      {message.body_text ? (
        <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-800">{message.body_text}</div>
      ) : null}
    </div>
  )
}

function StepRow({ step }: { step: AuditStepEvent }) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">Step</div>
          <div className="text-xs text-slate-600">{step.timestamp ? new Date(step.timestamp).toLocaleString() : '—'}</div>
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
        <div>
          <div className="text-sm font-semibold text-slate-900">
            {completionLabel} · {completion.llm_model || 'Unknown model'}{' '}
            <span className="text-xs font-normal text-slate-500">({completion.llm_provider || 'provider'})</span>
          </div>
          <div className="text-xs text-slate-600">{completion.timestamp ? new Date(completion.timestamp).toLocaleString() : '—'}</div>
        </div>
        <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-700">LLM</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <TokenPill label="Prompt" value={completion.prompt_tokens} />
        <TokenPill label="Output" value={completion.completion_tokens} />
        <TokenPill label="Total" value={completion.total_tokens} />
        <TokenPill label="Cached" value={completion.cached_tokens} />
      </div>

      {completion.thinking ? (
        <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">Thinking</div>
          <div className="whitespace-pre-wrap">{completion.thinking}</div>
        </div>
      ) : null}

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
  } = useAgentAuditStore((state) => state)
  const [promptState, setPromptState] = useState<Record<string, PromptState>>({})
  const eventsRef = useRef<HTMLDivElement | null>(null)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)
  const loadingRef = useRef(loading)
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const [timelineMaxHeight, setTimelineMaxHeight] = useState<number | null>(null)
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
    const nodes = Array.from(container.querySelectorAll('[data-day-marker="true"]')) as HTMLElement[]
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
  }, [events, setSelectedDay])

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
          className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-[0_10px_24px_rgba(15,23,42,0.08)]"
        >
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-600">Staff Audit</div>
            <div className="text-2xl font-bold leading-tight text-slate-900">{agentName || 'Agent'}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-800">{agentId}</div>
        </div>

        {error ? <div className="mt-4 text-sm text-rose-600">{error}</div> : null}
        {loading && !events.length ? <div className="mt-6 text-sm text-slate-700">Loading audit data…</div> : null}

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-start">
          <div ref={eventsRef} className="space-y-4">
            {events.map((event) => {
              const timestamp = (event as any).timestamp as string | null | undefined
              const day =
                timestamp && !Number.isNaN(new Date(timestamp).getTime())
                  ? `${new Date(timestamp).getFullYear()}-${String(new Date(timestamp).getMonth() + 1).padStart(2, '0')}-${String(new Date(timestamp).getDate()).padStart(2, '0')}`
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
            if (event.kind === 'step') {
              return (
                <div key={event.id} {...wrapperProps}>
                  <StepRow step={event as AuditStepEvent} />
                </div>
              )
            }
            return null
          })}
            {!events.length ? <div className="text-sm text-slate-600">No events yet.</div> : null}
            <div ref={loadMoreRef} className="h-6 w-full" aria-hidden="true" />
          </div>

          <div
            className="lg:sticky lg:top-6 lg:min-h-[520px]"
            style={timelineMaxHeight ? { maxHeight: `${timelineMaxHeight}px` } : undefined}
          >
            <AuditTimeline buckets={timeline} loading={timelineLoading} error={timelineError} selectedDay={selectedDay} onSelect={handleJumpToTimestamp} processingActive={processingActive} />
          </div>
        </div>
      </div>
    </div>
  )
}
