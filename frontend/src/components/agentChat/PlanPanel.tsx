import { memo, type MouseEvent } from 'react'
import { Download, FileText, MessageSquareText } from 'lucide-react'
import type { PlanSnapshot } from '../../types/agentChat'
import { PlanTaskItem, type PlanTaskStatus } from './PlanTaskItem'
import { AgentChatSurface } from './uiPrimitives'

type PlanPanelProps = {
  plan?: PlanSnapshot | null
  onMessageClick?: (messageId: string) => void
  compact?: boolean
  isAgentWorking?: boolean
}

type PlanRow = {
  title: string
  status: PlanTaskStatus
}

export const PlanPanel = memo(function PlanPanel({
  plan,
  onMessageClick,
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
        <div>
          <h2>Progress</h2>
        </div>
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
        <p className="plan-panel-empty">No plan steps</p>
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
    </aside>
  )
})
