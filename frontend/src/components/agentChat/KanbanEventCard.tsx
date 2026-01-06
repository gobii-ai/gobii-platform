import { memo, useEffect, useState, useMemo } from 'react'
import { CircleCheck, Sparkles } from 'lucide-react'
import type { KanbanEvent, KanbanCardChange } from './types'
import './kanban.css'

type KanbanEventCardProps = {
  event: KanbanEvent
}

// Map change types to their visual treatment
type ChangeInfo = {
  action: KanbanCardChange['action']
  title: string
}

type ColumnChangeMap = {
  todo: ChangeInfo[]
  doing: ChangeInfo[]
  done: ChangeInfo[]
}

function ProgressRing({
  done,
  total,
  animate,
}: {
  done: number
  total: number
  animate: boolean
}) {
  const percentage = total > 0 ? (done / total) * 100 : 0
  const radius = 28
  const strokeWidth = 5
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (percentage / 100) * circumference

  const isComplete = done === total && total > 0

  return (
    <div className={`kanban-progress-ring ${isComplete ? 'kanban-progress-complete' : ''}`}>
      <svg viewBox="0 0 70 70" className="kanban-ring-svg">
        {/* Background track */}
        <circle
          cx="35"
          cy="35"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="kanban-ring-track"
        />
        {/* Progress arc */}
        <circle
          cx="35"
          cy="35"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={animate ? offset : circumference}
          className="kanban-ring-progress"
          transform="rotate(-90 35 35)"
        />
      </svg>
      <div className="kanban-ring-content">
        <span className="kanban-ring-done">{done}</span>
        <span className="kanban-ring-divider">/</span>
        <span className="kanban-ring-total">{total}</span>
      </div>
      {isComplete && animate && (
        <div className="kanban-ring-glow" aria-hidden="true" />
      )}
    </div>
  )
}

function MiniColumn({
  status,
  label,
  count,
  titles,
  animate,
  delay,
  changes,
}: {
  status: 'todo' | 'doing' | 'done'
  label: string
  count: number
  titles: string[]
  animate: boolean
  delay: number
  changes: ChangeInfo[]
}) {
  if (count === 0) return null

  const maxVisible = 3
  const visibleTitles = titles.slice(0, maxVisible)
  const remaining = count - visibleTitles.length
  const hasChanges = changes.length > 0

  // Create a map of title -> action for quick lookup
  const changeMap = useMemo(() => {
    const map = new Map<string, KanbanCardChange['action']>()
    changes.forEach(c => map.set(c.title, c.action))
    return map
  }, [changes])

  // Determine column-level effect based on primary change type
  const columnEffect = useMemo(() => {
    if (!hasChanges) return ''
    const actions = changes.map(c => c.action)
    if (actions.includes('completed')) return 'kanban-column--pulse-done'
    if (actions.includes('started')) return 'kanban-column--pulse-doing'
    if (actions.includes('created')) return 'kanban-column--pulse-created'
    return ''
  }, [changes, hasChanges])

  return (
    <div
      className={`kanban-column kanban-column--${status} ${animate ? 'kanban-column--animate' : ''} ${animate ? columnEffect : ''}`}
      style={{ '--column-delay': `${delay}ms` } as React.CSSProperties}
    >
      <div className="kanban-column-header">
        <span className="kanban-column-label">{label}</span>
        <span className={`kanban-column-count ${hasChanges && animate ? 'kanban-column-count--changed' : ''}`}>
          {count}
        </span>
      </div>
      <div className="kanban-column-cards">
        {visibleTitles.map((title, i) => {
          const changeAction = changeMap.get(title)
          const cardClass = changeAction
            ? `kanban-mini-card kanban-mini-card--${changeAction}`
            : 'kanban-mini-card'

          return (
            <div
              key={i}
              className={`${cardClass} ${animate && changeAction ? 'kanban-mini-card--changed' : ''}`}
              style={{ '--card-delay': `${delay + (i + 1) * 40}ms` } as React.CSSProperties}
            >
              <span className={`kanban-mini-card-dot ${changeAction && animate ? 'kanban-mini-card-dot--pulse' : ''}`} />
              <span className="kanban-mini-card-title">{title}</span>
              {changeAction === 'completed' && animate && (
                <CircleCheck size={11} className="kanban-mini-card-check" strokeWidth={2.5} />
              )}
            </div>
          )
        })}
        {remaining > 0 && (
          <div className="kanban-column-more">+{remaining} more</div>
        )}
      </div>
    </div>
  )
}

function CelebrationParticles({ active }: { active: boolean }) {
  const particles = useMemo(
    () =>
      Array.from({ length: 12 }, (_, i) => ({
        id: i,
        angle: (i * 30) + Math.random() * 20 - 10,
        distance: 40 + Math.random() * 25,
        size: 3 + Math.random() * 3,
        delay: Math.random() * 100,
        hue: [142, 45, 200, 340][i % 4], // green, gold, purple, pink
      })),
    []
  )

  if (!active) return null

  return (
    <div className="kanban-particles" aria-hidden="true">
      {particles.map((p) => (
        <div
          key={p.id}
          className="kanban-particle"
          style={
            {
              '--angle': `${p.angle}deg`,
              '--distance': `${p.distance}px`,
              '--size': `${p.size}px`,
              '--delay': `${p.delay}ms`,
              '--hue': p.hue,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  )
}

export const KanbanEventCard = memo(function KanbanEventCard({
  event,
}: KanbanEventCardProps) {
  const [animate, setAnimate] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setAnimate(true), 50)
    return () => clearTimeout(timer)
  }, [])

  const { snapshot, changes, primaryAction } = event
  const total = snapshot.todoCount + snapshot.doingCount + snapshot.doneCount
  const hasCompletion = primaryAction === 'completed'
  const hasDeletion = primaryAction === 'deleted'
  const allDone = snapshot.doneCount === total && total > 0
  const boardCleared = total === 0

  // Build change map for each column based on toStatus
  const columnChanges = useMemo<ColumnChangeMap>(() => {
    const map: ColumnChangeMap = { todo: [], doing: [], done: [] }

    changes.forEach(change => {
      // Map toStatus to column, defaulting based on action if not specified
      let column: keyof ColumnChangeMap | null = null

      if (change.toStatus === 'todo' || change.toStatus === 'pending') {
        column = 'todo'
      } else if (change.toStatus === 'doing' || change.toStatus === 'in_progress') {
        column = 'doing'
      } else if (change.toStatus === 'done' || change.toStatus === 'completed') {
        column = 'done'
      } else {
        // Infer from action type
        if (change.action === 'completed') column = 'done'
        else if (change.action === 'started') column = 'doing'
        else if (change.action === 'created') column = 'todo'
      }

      if (column) {
        map[column].push({ action: change.action, title: change.title })
      }
    })

    return map
  }, [changes])

  // Determine card variant class
  const cardClass = [
    'kanban-card',
    hasCompletion && 'kanban-card--celebration',
    hasDeletion && 'kanban-card--deletion',
    allDone && 'kanban-card--all-done',
    boardCleared && 'kanban-card--cleared',
  ].filter(Boolean).join(' ')

  return (
    <div className={cardClass}>
      {/* Header with progress ring */}
      <div className="kanban-header">
        <ProgressRing done={snapshot.doneCount} total={total} animate={animate} />
        <div className="kanban-header-text">
          <div className="kanban-header-title">
            {hasCompletion && <Sparkles size={14} className="kanban-sparkle-icon" />}
            <span>{event.displayText}</span>
          </div>
          <div className="kanban-header-subtitle">
            {boardCleared ? 'Board cleared' : `${snapshot.doneCount} of ${total} tasks complete`}
          </div>
        </div>
      </div>

      {/* Mini kanban board - show empty state message when board is cleared */}
      {boardCleared ? (
        <div className="kanban-empty-board">
          <span className="kanban-empty-board-text">No tasks on board</span>
        </div>
      ) : (
        <div className="kanban-board">
          <MiniColumn
            status="todo"
            label="To Do"
            count={snapshot.todoCount}
            titles={snapshot.todoTitles}
            animate={animate}
            delay={200}
            changes={columnChanges.todo}
          />
          <MiniColumn
            status="doing"
            label="Doing"
            count={snapshot.doingCount}
            titles={snapshot.doingTitles}
            animate={animate}
            delay={280}
            changes={columnChanges.doing}
          />
          <MiniColumn
            status="done"
            label="Done"
            count={snapshot.doneCount}
            titles={snapshot.doneTitles}
            animate={animate}
            delay={360}
            changes={columnChanges.done}
          />
        </div>
      )}

      {/* Celebration effects */}
      <CelebrationParticles active={hasCompletion && animate} />
      {hasCompletion && animate && <div className="kanban-shimmer" aria-hidden="true" />}
    </div>
  )
})
