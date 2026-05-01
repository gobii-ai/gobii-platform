import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Cpu } from 'lucide-react'

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

function PromptSection({ label, text, onCopy }: { label: string; text: string; onCopy: (text: string) => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200/80 bg-white/90">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs font-semibold text-slate-700"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp className="h-4 w-4 text-slate-500" aria-hidden /> : <ChevronDown className="h-4 w-4 text-slate-500" aria-hidden />}
          <span>{label}</span>
        </button>
        <button
          type="button"
          className="rounded bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white hover:bg-slate-800"
          onClick={() => onCopy(text)}
        >
          Copy
        </button>
      </div>
      {expanded ? (
        <pre className="whitespace-pre-wrap break-words border-t border-slate-200/80 px-3 py-3 text-[12px] text-slate-800">
          {text}
        </pre>
      ) : null}
    </div>
  )
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
  const llmToolNames = useMemo(
    () => (completion.llm_tool_names || []).filter((name): name is string => typeof name === 'string' && name.length > 0),
    [completion.llm_tool_names],
  )

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
      case 'image_generation':
        return 'Image Generation'
      case 'video_generation':
        return 'Video Generation'
      case 'tool_search':
        return 'Tool Search'
      case 'agent_chat_suggestion':
        return 'Agent Chat Suggestion'
      case 'human_input_request_matching':
        return 'Human Input Request Matching'
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
        right={
          <div className="flex items-center gap-2">
            {llmToolNames.length ? (
              <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-700">
                {llmToolNames.length} tools
              </span>
            ) : null}
            <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-700">LLM</span>
          </div>
        }
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

          {llmToolNames.length ? (
            <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-700">Tools passed to LLM</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {llmToolNames.map((name) => (
                  <span key={name} className="rounded-md bg-white px-2 py-1 text-xs font-medium text-slate-700 ring-1 ring-slate-200">
                    {name}
                  </span>
                ))}
              </div>
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
                    <PromptSection label="System Prompt" text={systemPrompt} onCopy={copyText} />
                  ) : null}
                  {userPrompt ? (
                    <PromptSection label="User Prompt" text={userPrompt} onCopy={copyText} />
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
