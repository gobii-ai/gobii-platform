import { useEffect, useState } from 'react'
import { Copy, Loader2 } from 'lucide-react'

import { fetchPromptArchive } from '../../api/agentAudit'
import type { PromptArchive } from '../../types/agentAudit'
import { isRecord } from '../../util/objectUtils'
import { tryParseJson } from './toolDetails/normalize'
import { JsonBlock, OutputBlock, Section } from './toolDetails/shared'
import { stringify } from './toolDetails/utils'
import type { ToolDetailProps } from './tooling/types'

function RawValue({ value }: { value: unknown }) {
  const parsed = typeof value === 'string' ? tryParseJson(value) : value
  if (Array.isArray(parsed) || isRecord(parsed)) {
    return <JsonBlock value={parsed} />
  }
  return <OutputBlock content={stringify(value)} />
}

function CopyablePrompt({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="overflow-hidden rounded-lg border border-indigo-200 bg-white">
      <div className="flex items-center justify-between gap-3 bg-indigo-50 px-3 py-2">
        <span className="text-xs font-semibold text-slate-800">{label}</span>
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-100"
          onClick={() => void copy()}
          title={`Copy ${label.toLowerCase()}`}
        >
          <Copy className="h-3.5 w-3.5" aria-hidden />
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words px-3 py-3 text-xs text-slate-800">{value}</pre>
    </div>
  )
}

function metric(label: string, value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div key={label} className="rounded-lg bg-indigo-50 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 break-all font-mono text-xs text-slate-900">{String(value)}</div>
    </div>
  )
}

export function RawDeveloperToolDetail({ entry }: ToolDetailProps) {
  const tool = entry.sourceEntry?.developerEvent?.kind === 'tool_call'
    ? entry.sourceEntry.developerEvent
    : null
  return (
    <div className="space-y-4 text-sm text-slate-700">
      <div className="grid gap-2 sm:grid-cols-2">
        {metric('Raw tool name', entry.toolName)}
        {metric('Duration', tool?.execution_duration_ms != null
          ? `${tool.execution_duration_ms} ms`
          : null)}
      </div>
      {entry.rawParameters !== null && entry.rawParameters !== undefined ? (
        <Section title="Raw parameters">
          <RawValue value={entry.rawParameters} />
        </Section>
      ) : null}
      {entry.result !== null && entry.result !== undefined ? (
        <Section title="Raw result">
          <RawValue value={entry.result} />
        </Section>
      ) : null}
    </div>
  )
}

export function DeveloperStepDetail({ entry }: ToolDetailProps) {
  const step = entry.sourceEntry?.developerEvent?.kind === 'step'
    ? entry.sourceEntry.developerEvent
    : null
  if (!step) return <p className="text-sm text-slate-600">Step details are unavailable.</p>

  return (
    <div className="space-y-4 text-sm text-slate-700">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {metric('Step type', step.is_system ? 'System step' : 'Agent step')}
        {metric('System code', step.system_code)}
        {metric('Completion ID', step.completion_id)}
      </div>
      {step.description ? (
        <Section title="Description">
          <OutputBlock content={step.description} />
        </Section>
      ) : null}
      {step.system_notes ? (
        <Section title="System notes">
          <OutputBlock content={step.system_notes} />
        </Section>
      ) : null}
    </div>
  )
}

export function DeveloperCompletionDetail({ entry }: ToolDetailProps) {
  const completion = entry.sourceEntry?.developerEvent?.kind === 'completion'
    ? entry.sourceEntry.developerEvent
    : null
  const archiveId = completion?.prompt_archive?.id ?? null
  const [promptArchive, setPromptArchive] = useState<PromptArchive | null>(null)
  const [promptLoading, setPromptLoading] = useState(Boolean(archiveId))
  const [promptError, setPromptError] = useState<string | null>(null)

  useEffect(() => {
    if (!archiveId) return undefined
    let active = true
    void fetchPromptArchive(archiveId)
      .then((archive) => {
        if (active) setPromptArchive(archive)
      })
      .catch(() => {
        if (active) setPromptError('Unable to load the prompt archive.')
      })
      .finally(() => {
        if (active) setPromptLoading(false)
      })
    return () => {
      active = false
    }
  }, [archiveId])

  if (!completion) return <p className="text-sm text-slate-600">Completion details are unavailable.</p>

  const systemPrompt = promptArchive?.payload?.system_prompt
  const userPrompt = promptArchive?.payload?.user_prompt
  const duration = completion.request_duration_ms != null ? `${completion.request_duration_ms} ms` : null
  const ttft = completion.time_to_first_token_ms != null ? `${completion.time_to_first_token_ms} ms` : null

  return (
    <div className="space-y-4 text-sm text-slate-700">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {metric('Completion type', completion.completion_type || 'unknown')}
        {metric('Model', completion.llm_model)}
        {metric('Provider', completion.llm_provider)}
        {metric('Response ID', completion.response_id)}
        {metric('Duration', duration)}
        {metric('Time to first token', ttft)}
        {metric('Prompt tokens', completion.prompt_tokens)}
        {metric('Output tokens', completion.completion_tokens)}
        {metric('Total tokens', completion.total_tokens)}
        {metric('Cached tokens', completion.cached_tokens)}
        {metric('Output tokens/sec', completion.completion_tokens_per_second)}
      </div>

      {completion.llm_tool_names?.length ? (
        <Section title="Tools passed to the LLM">
          <JsonBlock value={completion.llm_tool_names} />
        </Section>
      ) : null}

      {completion.thinking?.trim() ? (
        <Section title="Thinking">
          <OutputBlock content={completion.thinking} />
        </Section>
      ) : null}

      {archiveId ? (
        <Section title="Prompt archive">
          {promptLoading ? (
            <div className="inline-flex items-center gap-2 text-xs text-slate-600">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Loading prompts…
            </div>
          ) : null}
          {promptError ? <p className="text-xs font-medium text-rose-700">{promptError}</p> : null}
          {systemPrompt ? <CopyablePrompt label="System Prompt" value={systemPrompt} /> : null}
          {userPrompt ? <CopyablePrompt label="User Prompt" value={userPrompt} /> : null}
          {!promptLoading && !promptError && !systemPrompt && !userPrompt ? (
            <p className="text-xs text-slate-600">No system or user prompt was archived.</p>
          ) : null}
        </Section>
      ) : null}
    </div>
  )
}
