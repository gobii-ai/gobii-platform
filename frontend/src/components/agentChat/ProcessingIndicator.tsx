import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react'

import { MarkdownViewer } from '../common/MarkdownViewer'
import type { ProcessingWebTask, StreamState } from '../../types/agentChat'
import { scrollIntoViewIfNeeded } from './scrollIntoView'
import { useAgentChatStore } from '../../stores/agentChatStore'
import { looksLikeHtml, sanitizeHtml } from '../../util/sanitize'

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
  streaming?: StreamState | null
}

export function ProcessingIndicator({
  agentFirstName,
  active,
  className,
  fade = false,
  tasks,
  streaming,
}: ProcessingIndicatorProps) {
  const activeTasks = useMemo(() => (Array.isArray(tasks) ? tasks.filter((task) => Boolean(task?.id)) : []), [tasks])
  const hasStreaming = Boolean(streaming)
  const streamingReasoning = streaming?.reasoning ?? ''
  const streamingContent = streaming?.content ?? ''
  const hasStreamingReasoning = streamingReasoning.trim().length > 0
  const hasStreamingContent = streamingContent.trim().length > 0
  const streamingStatusLabel = streaming?.done ? 'Draft ready' : 'Live response'
  const htmlReply = useMemo(() => {
    if (!streaming || !streaming.done) {
      return null
    }
    if (!streamingContent || !looksLikeHtml(streamingContent)) {
      return null
    }
    return sanitizeHtml(streamingContent)
  }, [streaming, streamingContent])
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
        return false
      }
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
    suppressAutoScrollPin()
    setAutoScrollPinned(false)
    pendingTaskExpansionRef.current = null
  }, [expandedTaskIds, setAutoScrollPinned, suppressAutoScrollPin])

  useEffect(() => {
    if (!isExpanded && panelScrollSnapshotRef.current !== null) {
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
      suppressAutoScrollPin(1500)
      setAutoScrollPinned(false)
    }
  }, [isExpanded, setAutoScrollPinned, suppressAutoScrollPin])

  if (!active && !hasStreaming) {
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
      data-has-streaming={hasStreaming ? 'true' : 'false'}
    >
      <div className="processing-content">
        <div className="processing-header">
          <span className="processing-pip" aria-hidden="true" />
          <button className="processing-label" onClick={togglePanelExpanded} type="button" disabled={!activeTasks.length}>
            <strong>{agentFirstName}</strong> is working
          </button>
          {hasStreaming || activeTasks.length ? (
            <div className="processing-meta">
              {hasStreaming ? <span className="processing-stream-badge">{streamingStatusLabel}</span> : null}
              {activeTasks.length ? <span className="processing-count">{taskCountLabel}</span> : null}
            </div>
          ) : null}
        </div>
        {hasStreaming ? (
          <div className="processing-stream" aria-live="polite">
            {hasStreamingReasoning ? (
              <div className="processing-stream-section">
                <div className="processing-stream-label">Thinking</div>
                <MarkdownViewer content={streamingReasoning} className="processing-stream-markdown" enableHighlight={false} />
              </div>
            ) : null}
            {hasStreamingContent ? (
              <div className="processing-stream-section">
                <div className="processing-stream-label">Reply</div>
                {htmlReply ? (
                  <div
                    className="processing-stream-markdown"
                    dangerouslySetInnerHTML={{ __html: htmlReply }}
                  />
                ) : (
                  <MarkdownViewer content={streamingContent} className="processing-stream-markdown" enableHighlight={false} />
                )}
              </div>
            ) : (
              <div className="processing-stream-placeholder">
                <span className="processing-stream-dot" aria-hidden="true" />
                <span>{streaming?.done ? 'No message content.' : 'Drafting reply...'}</span>
              </div>
            )}
          </div>
        ) : null}
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
