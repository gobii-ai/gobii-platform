import { useEffect, useMemo, useState } from 'react'
import { useAgentAuditStore } from '../stores/agentAuditStore'
import { useAgentAuditSocket } from '../hooks/useAgentAuditSocket'
import type { AuditCompletionEvent, AuditToolCallEvent, AuditMessageEvent, PromptArchive } from '../types/agentAudit'
import { fetchPromptArchive } from '../api/agentAudit'

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
      {expanded && tool.parameters ? (
        <pre className="mt-2 whitespace-pre-wrap break-all rounded-md bg-indigo-50 px-2 py-1 text-[12px] text-slate-800">{JSON.stringify(tool.parameters, null, 2)}</pre>
      ) : null}
      {expanded && tool.result ? (
        <pre className="mt-2 whitespace-pre-wrap break-all rounded-md bg-indigo-50 px-2 py-1 text-[12px] text-slate-800">{tool.result}</pre>
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
              onClick={() => onLoadPrompt(archiveId)}
              disabled={promptState?.loading}
            >
              {promptState?.loading ? 'Loading…' : 'Load'}
            </button>
          </div>
          {promptState?.error ? <div className="mt-2 text-xs text-rose-600">{promptState.error}</div> : null}
          {promptPayload ? (
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
  const { initialize, events, loading, error, loadMore, hasMore } = useAgentAuditStore((state) => state)
  const [promptState, setPromptState] = useState<Record<string, PromptState>>({})
  useAgentAuditSocket(agentId)

  useEffect(() => {
    initialize(agentId)
  }, [agentId, initialize])

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

  return (
    <div className="min-h-screen bg-white">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-600">Staff Audit</div>
            <div className="text-2xl font-bold leading-tight text-slate-900">{agentName || 'Agent'}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-800">{agentId}</div>
        </div>

        {error ? <div className="mt-4 text-sm text-rose-600">{error}</div> : null}
        {loading && !events.length ? <div className="mt-6 text-sm text-slate-700">Loading audit data…</div> : null}

        <div className="mt-6 space-y-4">
          {events.map((event) => {
            if (event.kind === 'completion') {
              return (
                <CompletionCard
                  key={event.id}
                  completion={event}
                  promptState={event.prompt_archive?.id ? promptState[event.prompt_archive.id] : undefined}
                  onLoadPrompt={handleLoadPrompt}
                />
              )
            }
            if (event.kind === 'tool_call') {
              return <ToolCallRow key={event.id} tool={event as AuditToolCallEvent} />
            }
            if (event.kind === 'message') {
              return <MessageRow key={event.id} message={event as AuditMessageEvent} />
            }
            return null
          })}
          {!events.length ? <div className="text-sm text-slate-600">No events yet.</div> : null}
        </div>

        {hasMore ? (
          <div className="mt-6 flex justify-center">
            <button
              type="button"
              onClick={() => loadMore()}
              className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(15,23,42,0.2)] transition hover:-translate-y-[1px] hover:bg-slate-800"
              disabled={loading}
            >
              {loading ? 'Loading…' : 'Load older events'}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
