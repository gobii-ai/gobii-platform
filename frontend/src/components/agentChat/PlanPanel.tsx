import { memo, type MouseEvent } from 'react'
import { BarChart3, CreditCard, Download, FileText, Gauge, MessageSquareText } from 'lucide-react'
import type { CreditAwarenessPayload, PlanSnapshot } from '../../types/agentChat'
import { PlanTaskItem, type PlanTaskStatus } from './PlanTaskItem'

type PlanPanelProps = {
  plan?: PlanSnapshot | null
  creditAwareness?: CreditAwarenessPayload | null
  creditAwarenessLoading?: boolean
  onMessageClick?: (messageId: string) => void
  onOpenUsage?: () => void
  onOpenSettings?: () => void
  onOpenTaskPacks?: () => void
  onOpenIntelligenceSettings?: () => void
  compact?: boolean
  isAgentWorking?: boolean
}

type PlanRow = {
  id?: string
  title: string
  status: PlanTaskStatus
  creditsUsed?: number | null
}

const creditFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 1,
})

function formatCredits(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '0'
  }
  return creditFormatter.format(value)
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '0%'
  }
  return `${Math.round(Math.max(0, Math.min(100, value)))}%`
}

function formatResetTime(value: string | null | undefined): string {
  if (!value) {
    return 'Reset time unavailable'
  }
  const resetDate = new Date(value)
  if (Number.isNaN(resetDate.getTime())) {
    return 'Reset time unavailable'
  }
  return `Resets ${new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(resetDate)}`
}

function formatResetDate(value: string | null | undefined): string {
  if (!value) {
    return 'Reset date unavailable'
  }
  const [year, month, day] = value.split('-').map((part) => Number(part))
  const resetDate = year && month && day
    ? new Date(year, month - 1, day, 12)
    : new Date(value)
  if (Number.isNaN(resetDate.getTime())) {
    return 'Reset date unavailable'
  }
  return `Resets ${new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
  }).format(resetDate)}`
}

export const PlanPanel = memo(function PlanPanel({
  plan,
  creditAwareness,
  onMessageClick,
  onOpenUsage,
  compact = false,
  isAgentWorking = true,
}: PlanPanelProps) {
  const snapshot = plan ?? {
    todoCount: 0,
    doingCount: 0,
    doneCount: 0,
    todoTitles: [],
    doingTitles: [],
    doneTitles: [],
    files: [],
    messages: [],
  }
  const files = snapshot.files ?? []
  const messages = snapshot.messages ?? []
  const hasDeliverables = files.length > 0 || messages.length > 0
  const snapshotSteps = snapshot.steps ?? []
  const rows: PlanRow[] = snapshotSteps.length > 0
    ? snapshotSteps.map((step) => ({
        id: step.id,
        title: step.title,
        status: step.status,
        creditsUsed: step.creditsUsed,
      }))
    : [
        ...snapshot.doneTitles.map((title) => ({ title, status: 'done' as const })),
        ...snapshot.doingTitles.map((title) => ({ title, status: 'doing' as const })),
        ...snapshot.todoTitles.map((title) => ({ title, status: 'todo' as const })),
      ]
  const currentPlanCredits = creditAwareness?.currentPlan?.creditsUsed
  const planCredits = typeof currentPlanCredits === 'number'
    ? currentPlanCredits
    : snapshot.usage?.totalCredits ?? 0
  const currentStepCredits = creditAwareness?.currentStep?.creditsUsed ?? snapshot.usage?.currentStepCredits ?? 0
  const dailyCredits = creditAwareness?.dailyCredits
  const quota = creditAwareness?.quota
  const dailyPercent = dailyCredits?.softPercentUsed ?? dailyCredits?.percentUsed ?? null
  const billingPercent = quota?.used_pct ?? null
  const dailyResetLabel = formatResetTime(dailyCredits?.nextResetIso)
  const billingResetLabel = formatResetDate(creditAwareness?.billingPeriod?.resetOn)

  const handleMessageClick = (event: MouseEvent<HTMLButtonElement>, messageId: string) => {
    event.preventDefault()
    onMessageClick?.(messageId)
  }

  return (
    <aside className={`plan-panel ${compact ? 'plan-panel--compact' : ''}`} aria-label="Plan">
      <section className="plan-panel-card" aria-label="Progress">
        <div className="plan-panel-header">
          <div>
            <h2>Progress</h2>
          </div>
        </div>
        {rows.length > 0 ? (
          <ul className="plan-panel-task-list">
            {rows.map((row, index) => (
              <PlanTaskItem
                key={row.id ?? `${row.status}:${index}:${row.title}`}
                title={row.title}
                status={row.status}
                creditsUsed={row.creditsUsed}
                isAgentWorking={isAgentWorking}
              />
            ))}
          </ul>
        ) : (
          <p className="plan-panel-empty">No plan steps</p>
        )}
        {hasDeliverables ? (
          <div className="plan-panel-deliverables">
            <div className="plan-panel-deliverables-title">Deliverables</div>
            {files.map((file) => (
              <div key={`file:${file.path}`} className="plan-panel-deliverable plan-panel-deliverable--file">
                <span className="plan-panel-deliverable-main">
                  <FileText size={14} />
                  <span>{file.label || file.path}</span>
                </span>
                {file.downloadUrl ? (
                  <a
                    className="plan-panel-deliverable-download"
                    aria-label={`Download ${file.label || file.path}`}
                    title={`Download ${file.label || file.path}`}
                    download
                    href={file.downloadUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Download size={13} strokeWidth={2.2} />
                  </a>
                ) : null}
              </div>
            ))}
            {messages.map((message) => (
              <button
                key={`message:${message.messageId}`}
                type="button"
                className="plan-panel-deliverable"
                onClick={(event) => handleMessageClick(event, message.messageId)}
              >
                <span className="plan-panel-deliverable-main">
                  <MessageSquareText size={14} />
                  <span>{message.label || 'Message'}</span>
                </span>
              </button>
            ))}
          </div>
        ) : null}
      </section>
      <section className="plan-panel-usage-card" aria-label="Credit usage">
        <div className="plan-panel-usage-title">Usage</div>
        <div className="plan-panel-usage-grid">
          <div className="plan-panel-usage-metric">
            <Gauge size={14} aria-hidden="true" />
            <span>
              <strong>{formatPercent(dailyPercent)}</strong>
              <small>{dailyResetLabel}</small>
            </span>
          </div>
          <div className="plan-panel-usage-metric">
            <BarChart3 size={14} aria-hidden="true" />
            <span>
              <strong>{formatPercent(billingPercent)}</strong>
              <small>{billingResetLabel}</small>
            </span>
          </div>
          <div className="plan-panel-usage-metric">
            <CreditCard size={14} aria-hidden="true" />
            <span>
              <strong>{formatCredits(planCredits)}</strong>
              <small>This task</small>
            </span>
          </div>
          <div className="plan-panel-usage-metric">
            <CreditCard size={14} aria-hidden="true" />
            <span>
              <strong>{formatCredits(currentStepCredits)}</strong>
              <small>Current step</small>
            </span>
          </div>
        </div>
        <div className="plan-panel-usage-actions">
          {onOpenUsage ? (
            <button type="button" className="plan-panel-usage-action plan-panel-usage-action--details" onClick={onOpenUsage}>
              <BarChart3 size={13} aria-hidden="true" />
              <span>Details</span>
            </button>
          ) : null}
        </div>
      </section>
    </aside>
  )
})
