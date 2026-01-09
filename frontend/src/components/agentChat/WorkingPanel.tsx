import { type MouseEvent, useEffect, useRef, useState, useCallback } from 'react'

import { MarkdownViewer } from '../common/MarkdownViewer'
import { InsightEventCard } from './insights'
import type { ProcessingWebTask } from '../../types/agentChat'
import type { InsightEvent, BurnRateMetadata } from '../../types/insight'
import { INSIGHT_TIMING } from '../../types/insight'
import { scrollIntoViewIfNeeded } from './scrollIntoView'
import { useAgentChatStore } from '../../stores/agentChatStore'

// Get the color for an insight tab based on its type
function getInsightTabColor(insight: InsightEvent): string {
  if (insight.insightType === 'time_saved') {
    return '#10b981' // emerald-500
  }
  if (insight.insightType === 'burn_rate') {
    const meta = insight.metadata as BurnRateMetadata
    const percent = meta.percentUsed
    if (percent >= 90) return '#ef4444' // red-500
    if (percent >= 70) return '#f59e0b' // amber-500
    return '#8b5cf6' // violet-500
  }
  return '#6b7280' // gray-500 fallback
}

// Get a short label for the insight tab
function getInsightTabLabel(insight: InsightEvent): string {
  if (insight.insightType === 'time_saved') {
    return 'Time'
  }
  if (insight.insightType === 'burn_rate') {
    return 'Usage'
  }
  return 'Insight'
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
  onPauseChange,
  isPaused = false,
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

  // Countdown timer state for auto-rotation indicator
  const [countdownProgress, setCountdownProgress] = useState(0)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastRotationTimeRef = useRef<number>(Date.now())

  // Carousel controls
  const totalInsights = insights.length
  const hasMultipleInsights = totalInsights > 1
  const currentInsight = insights[currentInsightIndex % totalInsights] ?? null

  // Handle tab click - select that insight and pause auto-rotation
  const handleTabClick = useCallback((index: number) => {
    onInsightIndexChange?.(index)
    onPauseChange?.(true) // Pause when user manually selects
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [onInsightIndexChange, onPauseChange])

  // Handle hover - pause auto-rotation
  const handleInsightMouseEnter = useCallback(() => {
    if (hasMultipleInsights) {
      onPauseChange?.(true)
    }
  }, [hasMultipleInsights, onPauseChange])

  const handleInsightMouseLeave = useCallback(() => {
    if (hasMultipleInsights) {
      onPauseChange?.(false)
      lastRotationTimeRef.current = Date.now()
      setCountdownProgress(0)
    }
  }, [hasMultipleInsights, onPauseChange])

  // Update countdown progress for the timer indicator
  useEffect(() => {
    if (!hasMultipleInsights || isPaused) {
      setCountdownProgress(0)
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current)
        countdownIntervalRef.current = null
      }
      return
    }

    const updateProgress = () => {
      const elapsed = Date.now() - lastRotationTimeRef.current
      const progress = Math.min(100, (elapsed / INSIGHT_TIMING.rotationIntervalMs) * 100)
      setCountdownProgress(progress)
    }

    // Update every 100ms for smooth animation
    countdownIntervalRef.current = setInterval(updateProgress, 100)
    updateProgress()

    return () => {
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current)
        countdownIntervalRef.current = null
      }
    }
  }, [hasMultipleInsights, isPaused])

  // Reset countdown when insight changes
  useEffect(() => {
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [currentInsightIndex])

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
      {/* Header: Agent is working + insight tabs */}
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

        {/* Colored pill tabs in header */}
        {hasMultipleInsights ? (
          <div className="working-panel-insight-tabs">
            <div className="working-panel-insight-tabs-scroll">
              {insights.map((insight, index) => {
                const isActive = index === currentInsightIndex % totalInsights
                const color = getInsightTabColor(insight)
                const label = getInsightTabLabel(insight)
                return (
                  <button
                    key={insight.insightId}
                    type="button"
                    className="working-panel-insight-tab"
                    data-active={isActive ? 'true' : 'false'}
                    onClick={() => handleTabClick(index)}
                    aria-label={`View ${insight.insightType.replace('_', ' ')} insight`}
                    style={{
                      '--tab-color': color,
                      '--tab-progress': isActive && !isPaused ? `${countdownProgress}%` : '0%',
                    } as React.CSSProperties}
                  >
                    <span className="working-panel-insight-tab-inner" />
                    <span className="working-panel-insight-tab-label">{label}</span>
                    {isActive && !isPaused && (
                      <span className="working-panel-insight-tab-progress" />
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        ) : null}
      </div>

      {/* Insight section */}
      {totalInsights > 0 ? (
        <div
          className="working-panel-insight"
          onMouseEnter={handleInsightMouseEnter}
          onMouseLeave={handleInsightMouseLeave}
        >
          <div className="working-panel-insight-content" key={currentInsight?.insightId}>
            {currentInsight ? (
              <InsightEventCard insight={currentInsight} onDismiss={onDismissInsight} />
            ) : null}
          </div>
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
