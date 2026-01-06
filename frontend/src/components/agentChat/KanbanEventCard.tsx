import { memo, useEffect, useState } from 'react'
import { Loader2, CheckCircle2 } from 'lucide-react'
import type { KanbanEvent, KanbanCardChange } from './types'
import './kanban.css'

type KanbanEventCardProps = {
  event: KanbanEvent
}

const STATUS_CONFIG = {
  todo: { label: 'Todo', icon: '○', color: 'kanban-status-todo' },
  doing: { label: 'Doing', icon: '◐', color: 'kanban-status-doing' },
  done: { label: 'Done', icon: '●', color: 'kanban-status-done' },
} as const

function getStatusKey(status: string | null | undefined): 'todo' | 'doing' | 'done' {
  if (!status) return 'todo'
  const s = status.toLowerCase()
  if (s === 'done') return 'done'
  if (s === 'doing') return 'doing'
  return 'todo'
}

function ActionIcon({ action }: { action: string }) {
  if (action === 'completed') {
    return (
      <span className="kanban-action-icon kanban-action-completed">
        <CheckCircle2 size={14} strokeWidth={2.5} />
      </span>
    )
  }
  if (action === 'started') {
    return (
      <span className="kanban-action-icon kanban-action-started">
        <Loader2 size={14} strokeWidth={2.5} />
      </span>
    )
  }
  return <span className="kanban-action-icon kanban-action-created" />
}

function ChangeItem({ change, animate }: { change: KanbanCardChange; animate: boolean }) {
  const toStatus = getStatusKey(change.toStatus)
  const config = STATUS_CONFIG[toStatus]

  return (
    <div className={`kanban-change-item ${animate ? 'kanban-change-animate' : ''} ${change.action === 'completed' ? 'kanban-change-completed' : ''}`}>
      <ActionIcon action={change.action} />
      <span className="kanban-change-title">{change.title}</span>
      <span className={`kanban-change-status ${config.color}`}>
        {config.label}
      </span>
    </div>
  )
}

function BoardSummary({ snapshot }: { snapshot: KanbanEvent['snapshot'] }) {
  const total = snapshot.todoCount + snapshot.doingCount + snapshot.doneCount
  if (total === 0) return null

  return (
    <div className="kanban-board-summary">
      <div className="kanban-summary-bar">
        {snapshot.doneCount > 0 && (
          <div
            className="kanban-bar-done"
            style={{ flex: snapshot.doneCount }}
            title={`${snapshot.doneCount} done`}
          />
        )}
        {snapshot.doingCount > 0 && (
          <div
            className="kanban-bar-doing"
            style={{ flex: snapshot.doingCount }}
            title={`${snapshot.doingCount} in progress`}
          />
        )}
        {snapshot.todoCount > 0 && (
          <div
            className="kanban-bar-todo"
            style={{ flex: snapshot.todoCount }}
            title={`${snapshot.todoCount} to do`}
          />
        )}
      </div>
      <div className="kanban-summary-counts">
        {snapshot.doneCount > 0 && (
          <span className="kanban-count-item kanban-status-done">{snapshot.doneCount} done</span>
        )}
        {snapshot.doneCount > 0 && snapshot.doingCount > 0 && (
          <span className="kanban-count-sep">·</span>
        )}
        {snapshot.doingCount > 0 && (
          <span className="kanban-count-item kanban-status-doing">{snapshot.doingCount} doing</span>
        )}
        {(snapshot.doneCount > 0 || snapshot.doingCount > 0) && snapshot.todoCount > 0 && (
          <span className="kanban-count-sep">·</span>
        )}
        {snapshot.todoCount > 0 && (
          <span className="kanban-count-item kanban-status-todo">{snapshot.todoCount} todo</span>
        )}
      </div>
    </div>
  )
}

function BoardPreview({ snapshot }: { snapshot: KanbanEvent['snapshot'] }) {
  const sections = [
    {
      key: 'doing',
      label: 'Doing',
      count: snapshot.doingCount,
      titles: snapshot.doingTitles,
      dotClass: 'kanban-dot-doing',
      labelClass: 'kanban-label-doing',
    },
    {
      key: 'todo',
      label: 'Todo',
      count: snapshot.todoCount,
      titles: snapshot.todoTitles,
      dotClass: 'kanban-dot-todo',
      labelClass: 'kanban-label-todo',
    },
    {
      key: 'done',
      label: 'Done',
      count: snapshot.doneCount,
      titles: snapshot.doneTitles,
      dotClass: 'kanban-dot-done',
      labelClass: 'kanban-label-done',
    },
  ] as const

  const hasCards = sections.some((section) => section.count > 0)
  if (!hasCards) return null

  return (
    <div className="kanban-board-preview">
      {sections.map((section) => {
        if (section.count === 0) return null
        const remaining = section.count - section.titles.length
        const hasTitles = section.titles.length > 0
        return (
          <div className="kanban-preview-row" key={section.key}>
            <div className="kanban-preview-header">
              <span className={`kanban-preview-label ${section.labelClass}`}>{section.label}</span>
              <span className="kanban-preview-count">{section.count}</span>
            </div>
            <div className="kanban-preview-list">
              {section.titles.map((title, index) => (
                <div className="kanban-preview-item" key={`${section.key}-${index}`}>
                  <span className={`kanban-preview-dot ${section.dotClass}`} aria-hidden="true" />
                  <span className="kanban-preview-title">{title}</span>
                </div>
              ))}
              {remaining > 0 && (
                <div className="kanban-preview-more">
                  {hasTitles ? `+${remaining} more` : `${remaining} tasks`}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const KanbanEventCard = memo(function KanbanEventCard({ event }: KanbanEventCardProps) {
  const [animate, setAnimate] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setAnimate(true), 50)
    return () => clearTimeout(timer)
  }, [])

  const hasCompletion = event.primaryAction === 'completed'

  return (
    <div className={`kanban-event-card ${hasCompletion ? 'kanban-event-completed' : ''}`}>
      <div className="kanban-section-label">Changes</div>
      <div className="kanban-changes">
        {event.changes.map((change) => (
          <ChangeItem key={change.cardId} change={change} animate={animate} />
        ))}
      </div>

      <div className="kanban-section-label kanban-section-label--summary">Board now</div>
      <BoardSummary snapshot={event.snapshot} />
      <BoardPreview snapshot={event.snapshot} />

      {/* Celebration shimmer for completions */}
      {hasCompletion && animate && (
        <div className="kanban-shimmer" aria-hidden="true" />
      )}
    </div>
  )
})
