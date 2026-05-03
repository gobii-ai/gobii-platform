import { memo, type MouseEvent } from 'react'
import { CheckCircle2, Circle, Download, FileText, LoaderCircle, MessageSquareText } from 'lucide-react'
import type { PlanSnapshot } from '../../types/agentChat'

type PlanPanelProps = {
  plan?: PlanSnapshot | null
  onMessageClick?: (messageId: string) => void
  compact?: boolean
}

type PlanRow = {
  title: string
  status: 'done' | 'doing' | 'todo'
}

export const PlanPanel = memo(function PlanPanel({ plan, onMessageClick, compact = false }: PlanPanelProps) {
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
  const rows: PlanRow[] = [
    ...snapshot.doneTitles.map((title) => ({ title, status: 'done' as const })),
    ...snapshot.doingTitles.map((title) => ({ title, status: 'doing' as const })),
    ...snapshot.todoTitles.map((title) => ({ title, status: 'todo' as const })),
  ]

  const handleMessageClick = (event: MouseEvent<HTMLButtonElement>, messageId: string) => {
    event.preventDefault()
    onMessageClick?.(messageId)
  }

  return (
    <aside className={`plan-panel ${compact ? 'plan-panel--compact' : ''}`} aria-label="Plan">
      <div className="plan-panel-header">
        <div>
          <h2>Progress</h2>
        </div>
      </div>
      {rows.length > 0 ? (
        <ul className="plan-panel-task-list">
          {rows.map((row, index) => (
            <li key={`${row.status}:${index}:${row.title}`} className="plan-panel-task" data-status={row.status}>
              <span className="plan-panel-task-icon" aria-hidden="true">
                {row.status === 'done' ? (
                  <CheckCircle2 size={14} strokeWidth={2.4} />
                ) : row.status === 'doing' ? (
                  <LoaderCircle size={14} strokeWidth={2.4} />
                ) : (
                  <Circle size={14} strokeWidth={2.2} />
                )}
              </span>
              <span className="plan-panel-task-title">{row.title}</span>
            </li>
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
    </aside>
  )
})
