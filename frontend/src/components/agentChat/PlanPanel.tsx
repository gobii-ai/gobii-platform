import { memo, type MouseEvent } from 'react'
import { Download, FileText, MessageSquareText } from 'lucide-react'
import type { CreditForecast, PlanSnapshot } from '../../types/agentChat'
import { PlanTaskItem, type PlanTaskStatus } from './PlanTaskItem'
import { AgentChatSurface } from './uiPrimitives'

type PlanPanelProps = {
  plan?: PlanSnapshot | null
  onMessageClick?: (messageId: string) => void
  compact?: boolean
  isAgentWorking?: boolean
  creditForecast?: CreditForecast | null
}

type PlanRow = {
  title: string
  status: PlanTaskStatus
}

type ForecastRow = {
  value: string
  suffix: string
}

function formatCreditEstimate(value: number | null | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return null
  }
  const formatted = Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return `${formatted} ${value === 1 ? 'credit' : 'credits'}`
}

function buildForecastRows(forecast: CreditForecast | null | undefined): ForecastRow[] {
  if (!forecast) {
    return []
  }
  const current = formatCreditEstimate(forecast.perRunCredits)
  const daily = formatCreditEstimate(forecast.dailyCredits)
  const monthly = formatCreditEstimate(forecast.monthlyCredits)
  return [
    current ? { value: current, suffix: '/ current plan' } : null,
    daily ? { value: daily, suffix: '/ day' } : null,
    monthly ? { value: monthly, suffix: '/ month' } : null,
  ].filter((item): item is ForecastRow => Boolean(item))
}

export const PlanPanel = memo(function PlanPanel({
  plan,
  onMessageClick,
  compact = false,
  isAgentWorking = true,
  creditForecast = null,
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
  const forecastRows = buildForecastRows(creditForecast)
  const rows: PlanRow[] = [
    ...snapshot.doneTitles.map((title) => ({ title, status: 'done' as const })),
    ...snapshot.doingTitles.map((title) => ({ title, status: 'doing' as const })),
    ...snapshot.todoTitles.map((title) => ({ title, status: 'todo' as const })),
  ]

  const handleMessageClick = (event: MouseEvent<HTMLElement>, messageId: string) => {
    event.preventDefault()
    onMessageClick?.(messageId)
  }

  return (
    <aside className={`plan-panel ${compact ? 'plan-panel--compact' : ''}`} aria-label="Plan">
      <div className="plan-panel-header">
        <h2>Progress</h2>
      </div>
      {rows.length > 0 ? (
        <ul className="plan-panel-task-list">
          {rows.map((row, index) => (
            <PlanTaskItem
              key={`${row.status}:${index}:${row.title}`}
              title={row.title}
              status={row.status}
              isAgentWorking={isAgentWorking}
            />
          ))}
        </ul>
      ) : (
        <p className="plan-panel-empty">No active steps yet</p>
      )}
      {hasDeliverables ? (
        <div className="plan-panel-deliverables">
          <div className="plan-panel-deliverables-title">Deliverables</div>
          {files.map((file) => (
            <AgentChatSurface key={`file:${file.path}`} className="plan-panel-deliverable plan-panel-deliverable--file">
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
            </AgentChatSurface>
          ))}
          {messages.map((message) => (
            <AgentChatSurface
              as="button"
              key={`message:${message.messageId}`}
              type="button"
              className="plan-panel-deliverable"
              onClick={(event) => handleMessageClick(event, message.messageId)}
            >
              <span className="plan-panel-deliverable-main">
                <MessageSquareText size={14} />
                <span>{message.label || 'Message'}</span>
              </span>
            </AgentChatSurface>
          ))}
        </div>
      ) : null}
      {forecastRows.length > 0 ? (
        <div className="plan-panel-estimate-footer">
          <h3>Estimated Usage</h3>
          <ul className="plan-panel-estimate-list" aria-label="Estimated task credits">
            {forecastRows.map((item) => (
              <li key={`${item.value}:${item.suffix}`}>
                <span className="plan-panel-estimate-value">{item.value}</span>
                <span className="plan-panel-estimate-suffix">{item.suffix}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </aside>
  )
})
