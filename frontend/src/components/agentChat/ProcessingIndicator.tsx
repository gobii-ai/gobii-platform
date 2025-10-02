import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react'

import { MarkdownViewer } from '../common/MarkdownViewer'
import type { ProcessingWebTask } from '../../types/agentChat'
import { scrollIntoViewIfNeeded } from './scrollIntoView'
import { useAgentChatStore } from '../../stores/agentChatStore'

const DEBUG_PROCESSING_INDICATOR = import.meta.env.DEV

function debugLog(...args: unknown[]) {
  if (!DEBUG_PROCESSING_INDICATOR) {
    return
  }
  // eslint-disable-next-line no-console
  console.debug('[ProcessingIndicator]', ...args)
}

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
  const [isExpanded, setIsExpanded] = useState(false)
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<string>>(new Set())
  const taskCardRefs = useRef<Map<string, HTMLButtonElement>>(new Map())
  const [lastExpandedTaskId, setLastExpandedTaskId] = useState<string | null>(null)
  const panelScrollSnapshotRef = useRef<number | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const wasExpandedRef = useRef<boolean>(false)
  const setAutoScrollPinned = useAgentChatStore((state) => state.setAutoScrollPinned)
  const suppressAutoScrollPin = useAgentChatStore((state) => state.suppressAutoScrollPin)
  const pendingTaskExpansionRef = useRef<string | null>(null)
  const pendingPanelActionRef = useRef<'expand' | 'collapse' | null>(null)

  useEffect(() => {
    debugLog('activeTasks updated', { count: activeTasks.length })
  }, [activeTasks])

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

  const toggleTaskExpanded = (taskId: string) => {
    const wasExpanded = expandedTaskIds.has(taskId)
    debugLog('toggleTaskExpanded', { taskId, wasExpanded })

    if (wasExpanded) {
      panelScrollSnapshotRef.current = window.scrollY
      setExpandedTaskIds((prev) => {
        const next = new Set(prev)
        next.delete(taskId)
        return next
      })
    } else {
      pendingTaskExpansionRef.current = taskId
      setExpandedTaskIds((prev) => {
        const next = new Set(prev)
        next.add(taskId)
        return next
      })
      setLastExpandedTaskId(taskId)
      debugLog('task expanded scheduled', { taskId })
    }
  }

  const togglePanelExpanded = () => {
    if (!activeTasks.length) {
      return
    }

    setIsExpanded((prev) => {
      pendingPanelActionRef.current = prev ? 'collapse' : 'expand'
      if (prev) {
        panelScrollSnapshotRef.current = window.scrollY
        debugLog('panel collapsed')
        return false
      }

      debugLog('panel expansion scheduled')
      return true
    })
  }

  useEffect(() => {
    if (!isExpanded || !lastExpandedTaskId) {
      return
    }

    if (!expandedTaskIds.has(lastExpandedTaskId)) {
      return
    }

    const cardElement = taskCardRefs.current.get(lastExpandedTaskId)
    if (!cardElement) {
      return
    }

    debugLog('scrollIntoViewIfNeeded for expanded task', { taskId: lastExpandedTaskId })
    scrollIntoViewIfNeeded(cardElement)
    setLastExpandedTaskId(null)
    panelScrollSnapshotRef.current = null
  }, [expandedTaskIds, isExpanded, lastExpandedTaskId])

  useEffect(() => {
    const pendingTaskId = pendingTaskExpansionRef.current
    if (!pendingTaskId) {
      return
    }
    if (!expandedTaskIds.has(pendingTaskId)) {
      pendingTaskExpansionRef.current = null
      return
    }
    debugLog('task expansion finalized', { taskId: pendingTaskId })
    suppressAutoScrollPin()
    setAutoScrollPinned(false)
    pendingTaskExpansionRef.current = null
  }, [expandedTaskIds, setAutoScrollPinned, suppressAutoScrollPin])

  useEffect(() => {
    if (!isExpanded && panelScrollSnapshotRef.current !== null) {
      debugLog('restoring scroll snapshot')
      window.scrollTo({ top: panelScrollSnapshotRef.current })
      panelScrollSnapshotRef.current = null
    }
  }, [isExpanded, expandedTaskIds])

  useEffect(() => {
    const wasExpanded = wasExpandedRef.current
    wasExpandedRef.current = isExpanded

    if (!isExpanded || wasExpanded) {
      return
    }

    const container = containerRef.current
    if (container) {
      debugLog('scrollIntoViewIfNeeded for panel container')
      scrollIntoViewIfNeeded(container)
      panelScrollSnapshotRef.current = null
    }
  }, [isExpanded])

  useEffect(() => {
    const pendingAction = pendingPanelActionRef.current
    if (!pendingAction) {
      return
    }
    pendingPanelActionRef.current = null
    if (pendingAction === 'expand') {
      debugLog('panel expansion finalized')
      suppressAutoScrollPin(1500)
      setAutoScrollPinned(false)
    } else {
      debugLog('panel collapse finalized')
    }
  }, [isExpanded, setAutoScrollPinned, suppressAutoScrollPin])

  if (!active) {
    return null
  }

  const now = currentTime
  const classes = combineClassNames('processing-indicator', fade && 'processing-indicator--fade', className)
  const taskCountLabel = activeTasks.length === 1 ? '1 web task running' : `${activeTasks.length} web tasks running`

  return (
    <div
      id="agent-processing-indicator"
      ref={containerRef}
      className={classes}
      data-visible={active ? 'true' : 'false'}
      data-expanded={isExpanded ? 'true' : 'false'}
    >
      <span className="processing-pip" aria-hidden="true" />
      <div className="processing-content">
        <button className="processing-label" onClick={togglePanelExpanded} type="button" disabled={!activeTasks.length}>
          <strong>{agentFirstName}</strong> is working
          {activeTasks.length ? <span className="processing-count">{taskCountLabel}</span> : null}
        </button>
        {activeTasks.length && isExpanded ? (
          <div className="processing-task-list" aria-live="polite">
            {activeTasks.map((task) => {
              const elapsedSeconds = deriveElapsedSeconds(task, now)
              const elapsedLabel = formatDuration(elapsedSeconds)
              const isTaskExpanded = expandedTaskIds.has(task.id)

              // Use full prompt when available, fall back to preview
              let fullPrompt = task.prompt || task.promptPreview || 'Background web task'
              let previewPrompt = task.promptPreview || 'Background web task'

              // Remove "Task:" prefix if present
              if (fullPrompt.toLowerCase().startsWith('task:')) {
                fullPrompt = fullPrompt.slice(5).trim()
              }
              if (previewPrompt.toLowerCase().startsWith('task:')) {
                previewPrompt = previewPrompt.slice(5).trim()
              }

              const handleTaskCardClick = (event: MouseEvent<HTMLButtonElement>) => {
                toggleTaskExpanded(task.id)
                if (event.detail !== 0) {
                  event.currentTarget.blur()
                }
                debugLog('task card clicked', { taskId: task.id, expanded: !isTaskExpanded, pointerEvent: event.detail })
              }

              return (
                <button
                  key={task.id}
                  ref={(el) => {
                    if (el) {
                      taskCardRefs.current.set(task.id, el)
                    } else {
                      taskCardRefs.current.delete(task.id)
                    }
                  }}
                  className="processing-task-card"
                  data-expanded={isTaskExpanded ? 'true' : 'false'}
                  onClick={handleTaskCardClick}
                  title={isTaskExpanded ? 'Click to collapse' : undefined}
                  type="button"
                >
                  <div className="processing-task-header">
                    <span className="processing-task-status" data-status={task.status}>
                      {task.statusLabel}
                    </span>
                    <span className="processing-task-duration">{elapsedLabel}</span>
                  </div>
                  <div className="processing-task-body">
                    {isTaskExpanded ? (
                      <MarkdownViewer content={fullPrompt} className="processing-task-markdown" />
                    ) : (
                      <p className="processing-task-preview">{previewPrompt}</p>
                    )}
                  </div>
                  {isTaskExpanded ? (
                    <div className="processing-task-id">Task ID: {task.id}</div>
                  ) : null}
                </button>
              )
            })}
          </div>
        ) : null}
      </div>
    </div>
  )
}
