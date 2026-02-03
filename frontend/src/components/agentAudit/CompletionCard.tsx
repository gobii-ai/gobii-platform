import { useMemo, useState } from 'react'
import { Cpu } from 'lucide-react'

import type { AuditCompletionEvent, PromptArchive } from '../../types/agentAudit'
import { EventHeader } from './EventHeader'
import { IconCircle, TokenPill } from './eventPrimitives'
import { ToolCallRow } from './EventRows'

const OPENROUTER_GENERATION_URL = 'https://openrouter.ai/api/v1/generation'

export type PromptState = {
  loading: boolean
  data?: PromptArchive
  error?: string
}

type CompletionCardProps = {
  completion: AuditCompletionEvent
  promptState: PromptState | undefined
  onLoadPrompt: (archiveId: string) => void
  collapsed?: boolean
  onToggle?: () => void
}

export function CompletionCard({
  completion,
  promptState,
  onLoadPrompt,
  collapsed = false,
  onToggle,
}: CompletionCardProps) {
  const archiveId = completion.prompt_archive?.id
  const promptPayload = archiveId ? promptState?.data?.payload : null
  const systemPrompt = promptPayload?.system_prompt
  const userPrompt = promptPayload?.user_prompt
  const [expanded, setExpanded] = useState(false)
  const responseId = completion.response_id
  const isOpenRouter = completion.llm_model?.toLowerCase().startsWith('openrouter')
  const openRouterUrl =
    responseId && isOpenRouter ? `${OPENROUTER_GENERATION_URL}?id=${encodeURIComponent(String(responseId))}` : null

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
      case 'prompt_summarization':
        return 'Prompt Summarization'
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
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <TokenPill label="Prompt" value={completion.prompt_tokens} />
            <TokenPill label="Output" value={completion.completion_tokens} />
            <TokenPill label="Total" value={completion.total_tokens} />
            <TokenPill label="Cached" value={completion.cached_tokens} />
            {openRouterUrl ? (
              <a
                href={openRouterUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="View OpenRouter response"
                className="inline-flex items-center gap-2 rounded-full bg-indigo-100 px-2 py-1 text-xs font-medium text-slate-800 transition hover:bg-indigo-200"
              >
                OpenRouter Response
              </a>
            ) : null}
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
