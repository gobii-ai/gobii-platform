import { useEffect, useState } from 'react'
import { Brain, Check, ChevronDown, Download, Loader2, RefreshCcw, Settings } from 'lucide-react'
import { Button, Menu, MenuItem, MenuTrigger, Popover, type Key } from 'react-aria-components'

import {
  decideAgentJudgeSuggestion,
  runAgentJudge,
  triggerProcessEvents,
  type ManualJudgeSuggestion,
} from '../../api/agentAudit'
import { Modal } from '../common/Modal'
import { AgentChatButton } from './uiPrimitives'

const EXPORT_RANGES = [
  { key: 'all', label: 'Full audit' },
  { key: '1h', label: '1 hour' },
  { key: '24h', label: '24 hours' },
  { key: '7d', label: '7 days' },
  { key: '30d', label: '30 days' },
] as const

type ExportRange = (typeof EXPORT_RANGES)[number]['key']

export type DeveloperModeActions = {
  agentId: string | null
  processingActive: boolean
  exportRange: ExportRange
  setExportRange: (range: ExportRange) => void
  processQueueing: boolean
  processQueued: boolean
  judgeRunning: boolean
  suggestion: ManualJudgeSuggestion | null
  decisionBusy: 'approve' | 'reject' | null
  error: string | null
  processEvents: () => Promise<void>
  runJudge: () => Promise<void>
  decide: (decision: 'approve' | 'reject') => Promise<void>
}

export function useDeveloperModeActions(
  agentId: string | null,
  processingActive: boolean,
): DeveloperModeActions {
  const [exportRange, setExportRange] = useState<ExportRange>('all')
  const [processQueueing, setProcessQueueing] = useState(false)
  const [processQueued, setProcessQueued] = useState(false)
  const [judgeRunning, setJudgeRunning] = useState(false)
  const [suggestion, setSuggestion] = useState<ManualJudgeSuggestion | null>(null)
  const [decisionBusy, setDecisionBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setExportRange('all')
    setProcessQueueing(false)
    setProcessQueued(false)
    setJudgeRunning(false)
    setSuggestion(null)
    setDecisionBusy(null)
    setError(null)
  }, [agentId])

  useEffect(() => {
    if (processingActive) setProcessQueued(false)
  }, [processingActive])

  const processEvents = async () => {
    if (!agentId) return
    setProcessQueueing(true)
    setError(null)
    try {
      const result = await triggerProcessEvents(agentId)
      setProcessQueued(Boolean(result.queued && !result.processing_active))
    } catch (processError) {
      setError(processError instanceof Error ? processError.message : 'Unable to queue event processing.')
    } finally {
      setProcessQueueing(false)
    }
  }

  const runJudge = async () => {
    if (!agentId) return
    setJudgeRunning(true)
    setError(null)
    try {
      const result = await runAgentJudge(agentId)
      if (result.suggestion) {
        setSuggestion(result.suggestion)
      } else {
        setError(result.status === 'llm_not_configured' ? 'Agent judge LLM is not configured.' : 'Judge completed without a reviewable suggestion.')
      }
    } catch (judgeError) {
      setError(judgeError instanceof Error ? judgeError.message : 'Unable to run the judge.')
    } finally {
      setJudgeRunning(false)
    }
  }

  const decide = async (decision: 'approve' | 'reject') => {
    if (!suggestion?.decisionApiUrl) return
    setDecisionBusy(decision)
    setError(null)
    try {
      await decideAgentJudgeSuggestion(suggestion.decisionApiUrl, decision)
      setSuggestion(null)
    } catch (decisionError) {
      setError(decisionError instanceof Error ? decisionError.message : `Unable to ${decision} the suggestion.`)
    } finally {
      setDecisionBusy(null)
    }
  }

  return {
    agentId,
    processingActive,
    exportRange,
    setExportRange,
    processQueueing,
    processQueued,
    judgeRunning,
    suggestion,
    decisionBusy,
    error,
    processEvents,
    runJudge,
    decide,
  }
}

export function DeveloperModeControls({
  actions,
  group,
}: {
  actions: DeveloperModeActions
  group: 'primary' | 'secondary'
}) {
  const selectedExportRange = EXPORT_RANGES.find((range) => range.key === actions.exportRange) ?? EXPORT_RANGES[0]
  const selectExportRange = (key: Key) => {
    if (typeof key === 'string' && EXPORT_RANGES.some((range) => range.key === key)) {
      actions.setExportRange(key as ExportRange)
    }
  }

  if (group === 'primary') {
    return (
      <div className="developer-mode-controls">
        <AgentChatButton className="banner-action banner-action--pill" size="sm" onClick={() => void actions.processEvents()} disabled={!actions.agentId || actions.processQueueing || actions.processQueued || actions.processingActive}>
          {actions.processQueueing || actions.processQueued || actions.processingActive ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCcw aria-hidden />}
          {actions.processingActive ? 'Processing…' : actions.processQueueing ? 'Queueing…' : actions.processQueued ? 'Queued…' : 'Process events'}
        </AgentChatButton>
        <AgentChatButton className="banner-action banner-action--pill" size="sm" onClick={() => void actions.runJudge()} disabled={!actions.agentId || actions.judgeRunning}>
          <Brain className={actions.judgeRunning ? 'animate-pulse' : undefined} aria-hidden />
          {actions.judgeRunning ? 'Judging…' : 'Run LLM judge'}
        </AgentChatButton>
        {actions.error && !actions.suggestion ? <span className="text-xs font-medium text-rose-700">{actions.error}</span> : null}
      </div>
    )
  }

  const adminUrl = actions.agentId ? `/admin/api/persistentagent/${actions.agentId}/change/` : '#'
  const exportUrl = actions.agentId
    ? `/console/api/staff/agents/${actions.agentId}/developer/export/?range=${encodeURIComponent(actions.exportRange)}`
    : '#'

  return (
    <div className="developer-mode-controls">
      <AgentChatButton as="a" className="banner-action banner-action--pill" size="sm" href={adminUrl} target="_blank" rel="noreferrer" title="Open agent in Django admin" aria-disabled={!actions.agentId ? 'true' : undefined}>
        <Settings aria-hidden />
        Django admin
      </AgentChatButton>
      <div className="developer-export-control">
        <MenuTrigger>
          <Button className="agent-chat-button banner-action banner-action--pill developer-export-trigger" aria-label={`Export range (${selectedExportRange.label})`}>
            <span>{selectedExportRange.label}</span>
            <ChevronDown className="developer-export-chevron" aria-hidden />
          </Button>
          <Popover className="developer-export-popover" placement="bottom end" offset={6}>
            <Menu aria-label="Developer export range" onAction={selectExportRange} className="developer-export-menu">
              {EXPORT_RANGES.map((range) => (
                <MenuItem key={range.key} id={range.key} textValue={range.label} className="agent-chat-menu-item developer-export-option" data-selected={range.key === actions.exportRange ? 'true' : 'false'}>
                  <span>{range.label}</span>
                  {range.key === actions.exportRange ? <Check className="developer-export-check" aria-hidden /> : null}
                </MenuItem>
              ))}
            </Menu>
          </Popover>
        </MenuTrigger>
        <AgentChatButton as="a" className="banner-action banner-action--square developer-export-download" size="sm" href={exportUrl} aria-disabled={!actions.agentId ? 'true' : undefined} aria-label={`Download ${selectedExportRange.label.toLowerCase()} export`} title={`Download ${selectedExportRange.label.toLowerCase()} export`}>
          <Download aria-hidden />
        </AgentChatButton>
      </div>
    </div>
  )
}

export function DeveloperJudgeReviewModal({ actions }: { actions: DeveloperModeActions }) {
  const suggestion = actions.suggestion
  if (!suggestion) return null

  return (
    <Modal
      title="Review judge suggestion"
      subtitle="Approve to activate the directive for the agent, or reject to discard it."
      dismissible={false}
      onClose={() => undefined}
      icon={Brain}
      iconBgClass="bg-violet-100"
      iconColorClass="text-violet-700"
      widthClass="sm:max-w-3xl"
      footer={(
        <div className="flex flex-col gap-3 sm:flex-row-reverse">
          <button type="button" className="rounded-md bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" disabled={Boolean(actions.decisionBusy)} onClick={() => void actions.decide('approve')}>
            {actions.decisionBusy === 'approve' ? 'Approving…' : 'Approve suggestion'}
          </button>
          <button type="button" className="rounded-md border border-rose-300 bg-white px-4 py-2 text-sm font-semibold text-rose-700 disabled:opacity-50" disabled={Boolean(actions.decisionBusy)} onClick={() => void actions.decide('reject')}>
            {actions.decisionBusy === 'reject' ? 'Rejecting…' : 'Reject'}
          </button>
        </div>
      )}
    >
      <div className="space-y-4 text-sm text-slate-800">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-violet-700">{suggestion.suggestionType.replaceAll('_', ' ')}</div>
          <h3 className="mt-1 text-base font-semibold text-slate-950">{suggestion.title}</h3>
          <p className="mt-2 whitespace-pre-wrap">{suggestion.message}</p>
        </div>
        {suggestion.agentDirective ? <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 whitespace-pre-wrap">{suggestion.agentDirective}</div> : null}
        <details className="rounded-lg border border-slate-300 bg-white p-3">
          <summary className="cursor-pointer font-semibold">Thinking</summary>
          <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-3 text-xs text-slate-100">{suggestion.reasoning?.trim() || 'No reasoning was captured.'}</pre>
        </details>
        {actions.error ? <p className="text-rose-700">{actions.error}</p> : null}
      </div>
    </Modal>
  )
}
