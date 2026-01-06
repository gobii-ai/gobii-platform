import { memo, useEffect, useState, useMemo } from 'react'
import { CircleCheck, Play, Plus, ArrowRight, Sparkles } from 'lucide-react'
import type { KanbanEvent, KanbanCardChange } from './types'
import './kanban.css'

type KanbanEventCardProps = {
  event: KanbanEvent
}

const ACTION_CONFIG = {
  completed: {
    icon: CircleCheck,
    label: 'Completed',
    className: 'kanban-action--completed',
  },
  started: {
    icon: Play,
    label: 'Started',
    className: 'kanban-action--started',
  },
  created: {
    icon: Plus,
    label: 'Created',
    className: 'kanban-action--created',
  },
  updated: {
    icon: ArrowRight,
    label: 'Updated',
    className: 'kanban-action--updated',
  },
} as const

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

function ChangeItem({
  change,
  index,
  animate,
}: {
  change: KanbanCardChange
  index: number
  animate: boolean
}) {
  const config = ACTION_CONFIG[change.action]
  const Icon = config.icon

  return (
    <div
      className={`kanban-change ${config.className} ${animate ? 'kanban-change--animate' : ''}`}
      style={{ '--delay': `${index * 60}ms` } as React.CSSProperties}
    >
      <div className="kanban-change-icon">
        <Icon size={14} strokeWidth={2.5} />
      </div>
      <span className="kanban-change-title">{change.title}</span>
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
}: {
  status: 'todo' | 'doing' | 'done'
  label: string
  count: number
  titles: string[]
  animate: boolean
  delay: number
}) {
  if (count === 0) return null

  const maxVisible = 3
  const visibleTitles = titles.slice(0, maxVisible)
  const remaining = count - visibleTitles.length

  return (
    <div
      className={`kanban-column kanban-column--${status} ${animate ? 'kanban-column--animate' : ''}`}
      style={{ '--column-delay': `${delay}ms` } as React.CSSProperties}
    >
      <div className="kanban-column-header">
        <span className="kanban-column-label">{label}</span>
        <span className="kanban-column-count">{count}</span>
      </div>
      <div className="kanban-column-cards">
        {visibleTitles.map((title, i) => (
          <div
            key={i}
            className="kanban-mini-card"
            style={{ '--card-delay': `${delay + (i + 1) * 40}ms` } as React.CSSProperties}
          >
            <span className="kanban-mini-card-dot" />
            <span className="kanban-mini-card-title">{title}</span>
          </div>
        ))}
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
  const allDone = snapshot.doneCount === total && total > 0

  // Group changes by action type for better visual organization
  const completedChanges = changes.filter((c) => c.action === 'completed')
  const otherChanges = changes.filter((c) => c.action !== 'completed')
  const sortedChanges = [...completedChanges, ...otherChanges]

  return (
    <div
      className={`kanban-card ${hasCompletion ? 'kanban-card--celebration' : ''} ${allDone ? 'kanban-card--all-done' : ''}`}
    >
      {/* Header with progress ring */}
      <div className="kanban-header">
        <ProgressRing done={snapshot.doneCount} total={total} animate={animate} />
        <div className="kanban-header-text">
          <div className="kanban-header-title">
            {hasCompletion && <Sparkles size={14} className="kanban-sparkle-icon" />}
            <span>{event.displayText}</span>
          </div>
          <div className="kanban-header-subtitle">
            {snapshot.doneCount} of {total} tasks complete
          </div>
        </div>
      </div>

      {/* Changes feed */}
      {sortedChanges.length > 0 && (
        <div className="kanban-changes-section">
          <div className="kanban-section-title">What changed</div>
          <div className="kanban-changes-list">
            {sortedChanges.map((change, i) => (
              <ChangeItem key={change.cardId} change={change} index={i} animate={animate} />
            ))}
          </div>
        </div>
      )}

      {/* Mini kanban board */}
      <div className="kanban-board">
        <MiniColumn
          status="doing"
          label="In Progress"
          count={snapshot.doingCount}
          titles={snapshot.doingTitles}
          animate={animate}
          delay={200}
        />
        <MiniColumn
          status="todo"
          label="To Do"
          count={snapshot.todoCount}
          titles={snapshot.todoTitles}
          animate={animate}
          delay={280}
        />
        <MiniColumn
          status="done"
          label="Done"
          count={snapshot.doneCount}
          titles={snapshot.doneTitles}
          animate={animate}
          delay={360}
        />
      </div>

      {/* Celebration effects */}
      <CelebrationParticles active={hasCompletion && animate} />
      {hasCompletion && animate && <div className="kanban-shimmer" aria-hidden="true" />}
    </div>
  )
})
