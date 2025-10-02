import { useEffect, useMemo, useState } from 'react'

import type { ProcessingWebTask } from '../../types/agentChat'

function combineClassNames(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(' ')
}

function deriveElapsedSeconds(task: ProcessingWebTask, now: number): number {
  if (task.startedAt) {
    const started = Date.parse(task.startedAt)
    if (!Number.isNaN(started)) {
      return Math.max(0, (now - started) / 1000)
    }
  }
  if (typeof task.elapsedSeconds === 'number' && Number.isFinite(task.elapsedSeconds)) {
    return Math.max(0, task.elapsedSeconds)
  }
  return 0
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return 'just now'
  }

  if (seconds < 60) {
    return `${Math.floor(seconds)}s`
  }

  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)

  if (minutes < 60) {
    if (minutes < 10 && remainingSeconds >= 5) {
      return `${minutes}m ${remainingSeconds}s`
    }
    return `${minutes}m`
  }

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  if (hours < 24) {
    return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`
  }

  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  return remainingHours ? `${days}d ${remainingHours}h` : `${days}d`
}

const REFRESH_INTERVAL_MS = 5000

type ProcessingIndicatorProps = {
  agentFirstName: string
  active: boolean
  className?: string
  fade?: boolean
  tasks?: ProcessingWebTask[]
}

export function ProcessingIndicator({ agentFirstName, active, className, fade = false, tasks }: ProcessingIndicatorProps) {
  const activeTasks = useMemo(() => (Array.isArray(tasks) ? tasks.filter((task) => Boolean(task?.id)) : []), [tasks])
  const [currentTime, setCurrentTime] = useState(() => Date.now())

  useEffect(() => {
    if (!active || !activeTasks.length) {
      return () => undefined
    }
    const interval = window.setInterval(() => {
      setCurrentTime(Date.now())
    }, REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [active, activeTasks.length])

  useEffect(() => {
    if (!active) {
      return
    }
    setCurrentTime(Date.now())
  }, [active, activeTasks])

  if (!active) {
    return null
  }

  const now = currentTime
  const classes = combineClassNames('processing-indicator', fade && 'processing-indicator--fade', className)
  const taskCountLabel = activeTasks.length === 1 ? '1 web task running' : `${activeTasks.length} web tasks running`

  return (
    <div id="agent-processing-indicator" className={classes} data-visible={active ? 'true' : 'false'}>
      <span className="processing-pip" aria-hidden="true" />
      <div className="processing-content">
        <span className="processing-label">
          <strong>{agentFirstName}</strong> is working
          {activeTasks.length ? <span className="processing-count">{taskCountLabel}</span> : null}
        </span>
        {activeTasks.length ? (
          <div className="processing-task-list" aria-live="polite">
            {activeTasks.map((task) => {
              const elapsedSeconds = deriveElapsedSeconds(task, now)
              const elapsedLabel = formatDuration(elapsedSeconds)
              return (
                <div key={task.id} className="processing-task-card">
                  <div className="processing-task-header">
                    <span className="processing-task-status" data-status={task.status}>
                      {task.statusLabel}
                    </span>
                    <span className="processing-task-duration">{elapsedLabel}</span>
                  </div>
                  <p className="processing-task-body" title={task.promptPreview}>
                    {task.promptPreview || 'Background web task'}
                  </p>
                </div>
              )
            })}
          </div>
        ) : null}
      </div>
    </div>
  )
}
