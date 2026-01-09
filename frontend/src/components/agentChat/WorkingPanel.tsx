import { type MouseEvent, useEffect, useRef, useState, useCallback } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

import { MarkdownViewer } from '../common/MarkdownViewer'
import { InsightEventCard } from './insights'
import type { ProcessingWebTask } from '../../types/agentChat'
import type { InsightEvent } from '../../types/insight'
import { scrollIntoViewIfNeeded } from './scrollIntoView'
import { useAgentChatStore } from '../../stores/agentChatStore'

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

type WorkingPanelProps = {
  agentFirstName: string
  active: boolean
  tasks?: ProcessingWebTask[]
  insights?: InsightEvent[]
  currentInsightIndex?: number
  onDismissInsight?: (insightId: string) => void
  onInsightIndexChange?: (index: number) => void
  onPauseChange?: (paused: boolean) => void
  isPaused?: boolean
}

export function WorkingPanel({
  agentFirstName,
  active,
  tasks,
  insights = [],
  currentInsightIndex = 0,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange: _onPauseChange,
  isPaused: _isPaused = false,
}: WorkingPanelProps) {
  const activeTasks = Array.isArray(tasks) ? tasks.filter((task) => Boolean(task?.id)) : []
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

  // Carousel controls
  const totalInsights = insights.length
  const hasMultipleInsights = totalInsights > 1
  const currentInsight = insights[currentInsightIndex % totalInsights] ?? null

  const handlePrev = useCallback(() => {
    if (!hasMultipleInsights) return
    const newIndex = (currentInsightIndex - 1 + totalInsights) % totalInsights
    onInsightIndexChange?.(newIndex)
  }, [currentInsightIndex, totalInsights, hasMultipleInsights, onInsightIndexChange])

  const handleNext = useCallback(() => {
    if (!hasMultipleInsights) return
    const newIndex = (currentInsightIndex + 1) % totalInsights
    onInsightIndexChange?.(newIndex)
  }, [currentInsightIndex, totalInsights, hasMultipleInsights, onInsightIndexChange])

  const handleDotClick = useCallback((index: number) => {
    onInsightIndexChange?.(index)
  }, [onInsightIndexChange])

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

  if (!active) {
    return null
  }

  const now = currentTime
  const taskCountLabel = activeTasks.length === 1 ? '1 web task' : `${activeTasks.length} web tasks`

  return (
    <div
      id="working-panel"
      ref={containerRef}
      className="working-panel"
      data-visible={active ? 'true' : 'false'}
      data-expanded={isExpanded ? 'true' : 'false'}
      data-has-insight={currentInsight ? 'true' : 'false'}
    >
      {/* Header: Agent is working */}
      <div className="working-panel-header">
        <span className="working-panel-pip" aria-hidden="true" />
        <span className="working-panel-status">
          <strong>{agentFirstName}</strong> is working
          <span className="working-panel-ellipsis" aria-label="working">
            <span className="working-panel-ellipsis-dot" />
            <span className="working-panel-ellipsis-dot" />
            <span className="working-panel-ellipsis-dot" />
          </span>
        </span>
      </div>

      {/* Insight section */}
      {totalInsights > 0 ? (
        <div className="working-panel-insight">
          <div className="working-panel-insight-content" key={currentInsight?.insightId}>
            {currentInsight ? (
              <InsightEventCard insight={currentInsight} onDismiss={onDismissInsight} />
            ) : null}
          </div>

          {/* Inline carousel controls */}
          {hasMultipleInsights ? (
            <div className="working-panel-carousel-inline">
              <div className="working-panel-carousel-dots">
                {insights.map((_, index) => (
                  <button
                    key={index}
                    type="button"
                    className="working-panel-carousel-dot"
                    data-active={index === currentInsightIndex % totalInsights ? 'true' : 'false'}
                    onClick={() => handleDotClick(index)}
                    aria-label={`Go to insight ${index + 1}`}
                  />
                ))}
              </div>
              <button
                type="button"
                className="working-panel-carousel-nav"
                onClick={handlePrev}
                aria-label="Previous"
              >
                <ChevronLeft size={14} />
              </button>
              <button
                type="button"
                className="working-panel-carousel-nav"
                onClick={handleNext}
                aria-label="Next"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Web tasks section */}
      {activeTasks.length > 0 ? (
        <div className="working-panel-tasks">
          <button
            type="button"
            className="working-panel-tasks-toggle"
            onClick={togglePanelExpanded}
            aria-expanded={isExpanded}
          >
            <span className="working-panel-tasks-pip" aria-hidden="true" />
            <span className="working-panel-tasks-label">{taskCountLabel}</span>
            <svg
              className="working-panel-tasks-chevron"
              data-expanded={isExpanded ? 'true' : 'false'}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {isExpanded ? (
            <div className="working-panel-task-list" aria-live="polite">
              {activeTasks.map((task) => {
                const elapsedSeconds = deriveElapsedSeconds(task, now)
                const elapsedLabel = formatDuration(elapsedSeconds)
                const isTaskExpanded = expandedTaskIds.has(task.id)

                let fullPrompt = task.prompt || task.promptPreview || 'Background web task'
                let previewPrompt = task.promptPreview || 'Background web task'

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
                    className="working-panel-task-card"
                    data-expanded={isTaskExpanded ? 'true' : 'false'}
                    onClick={handleTaskCardClick}
                    title={isTaskExpanded ? 'Click to collapse' : undefined}
                    type="button"
                  >
                    <div className="working-panel-task-header">
                      <span className="working-panel-task-status" data-status={task.status}>
                        {task.statusLabel}
                      </span>
                      <span className="working-panel-task-duration">{elapsedLabel}</span>
                    </div>
                    <div className="working-panel-task-body">
                      {isTaskExpanded ? (
                        <MarkdownViewer content={fullPrompt} className="working-panel-task-markdown" />
                      ) : (
                        <p className="working-panel-task-preview">{previewPrompt}</p>
                      )}
                    </div>
                    {isTaskExpanded ? (
                      <div className="working-panel-task-id">Task ID: {task.id}</div>
                    ) : null}
                  </button>
                )
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
