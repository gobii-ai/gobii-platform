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

export const KanbanEventCard = memo(function KanbanEventCard({ event }: KanbanEventCardProps) {
  const [animate, setAnimate] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setAnimate(true), 50)
    return () => clearTimeout(timer)
  }, [])

  const hasCompletion = event.primaryAction === 'completed'

  return (
    <div className={`kanban-event-card ${hasCompletion ? 'kanban-event-completed' : ''}`}>
      {/* What changed - the important part */}
      <div className="kanban-changes">
        {event.changes.map((change) => (
          <ChangeItem
            key={change.cardId}
            change={change}
            animate={animate}
          />
        ))}
      </div>

      {/* Board state summary */}
      <BoardSummary snapshot={event.snapshot} />

      {/* Celebration shimmer for completions */}
      {hasCompletion && animate && (
        <div className="kanban-shimmer" aria-hidden="true" />
      )}
    </div>
  )
})
