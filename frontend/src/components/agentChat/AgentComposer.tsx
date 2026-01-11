import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowUp, Paperclip, X, ChevronDown, ChevronUp } from 'lucide-react'

import { InsightEventCard } from './insights'
import type { ProcessingWebTask } from '../../types/agentChat'
import type { InsightEvent, BurnRateMetadata, AgentSetupMetadata } from '../../types/insight'
import { INSIGHT_TIMING } from '../../types/insight'

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
  if (insight.insightType === 'agent_setup') {
    return '#0ea5e9' // sky-500
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
  if (insight.insightType === 'agent_setup') {
    const meta = insight.metadata as AgentSetupMetadata
    switch (meta.panel) {
      case 'always_on':
        return '24/7'
      case 'sms':
        return 'SMS'
      case 'org_transfer':
        return 'Org'
      case 'upsell_pro':
        return 'Go Pro'
      case 'upsell_scale':
        return 'Go Scale'
      default:
        return '24/7'
    }
  }
  return 'Insight'
}

// Get background gradient for insight wrapper
function getInsightBackground(insight: InsightEvent): string {
  if (insight.insightType === 'time_saved') {
    return 'linear-gradient(135deg, #ecfdf5 0%, #d1fae5 50%, #a7f3d0 100%)'
  }
  if (insight.insightType === 'burn_rate') {
    const meta = insight.metadata as BurnRateMetadata
    const percent = meta.percentUsed
    if (percent >= 90) return 'linear-gradient(135deg, #fef2f2 0%, #fee2e2 50%, #fecaca 100%)'
    if (percent >= 70) return 'linear-gradient(135deg, #fffbeb 0%, #fef3c7 50%, #fde68a 100%)'
    return 'linear-gradient(135deg, #f5f3ff 0%, #ede9fe 50%, #ddd6fe 100%)'
  }
  if (insight.insightType === 'agent_setup') {
    return 'linear-gradient(135deg, #e0f2fe 0%, #eef2ff 45%, #ffffff 100%)'
  }
  return 'transparent'
}

type AgentComposerProps = {
  onSubmit?: (message: string, attachments?: File[]) => void | Promise<void>
  disabled?: boolean
  autoFocus?: boolean
  // Working panel props
  agentFirstName?: string
  isProcessing?: boolean
  processingTasks?: ProcessingWebTask[]
  insights?: InsightEvent[]
  currentInsightIndex?: number
  onDismissInsight?: (insightId: string) => void
  onInsightIndexChange?: (index: number) => void
  onPauseChange?: (paused: boolean) => void
  isInsightsPaused?: boolean
}

export function AgentComposer({
  onSubmit,
  disabled = false,
  autoFocus = false,
  agentFirstName = 'Agent',
  isProcessing = false,
  processingTasks = [],
  insights = [],
  currentInsightIndex = 0,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange,
  isInsightsPaused = false,
}: AgentComposerProps) {
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const [isWorkingExpanded, setIsWorkingExpanded] = useState(true)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const attachmentInputId = useId()
  const dragCounter = useRef(0)

  // Countdown timer state for auto-rotation indicator
  const [countdownProgress, setCountdownProgress] = useState(0)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastRotationTimeRef = useRef<number>(Date.now())

  // Track previous processing state for auto-expand/collapse
  const wasProcessingRef = useRef(isProcessing)

  // Auto-expand when processing starts, auto-collapse when it ends
  useEffect(() => {
    if (!wasProcessingRef.current && isProcessing) {
      // Processing just started - auto-expand
      setIsWorkingExpanded(true)
    } else if (wasProcessingRef.current && !isProcessing) {
      // Processing just ended - auto-collapse
      setIsWorkingExpanded(false)
    }
    wasProcessingRef.current = isProcessing
  }, [isProcessing])

  const MAX_COMPOSER_HEIGHT = 320

  // Insight carousel logic
  const totalInsights = insights.length
  const hasMultipleInsights = totalInsights > 1
  const currentInsight = insights[currentInsightIndex % Math.max(1, totalInsights)] ?? null
  const hasInsights = totalInsights > 0

  // Handle tab click - select that insight, expand panel if collapsed, and pause auto-rotation
  const handleTabClick = useCallback((index: number) => {
    // Expand panel if collapsed
    if (!isWorkingExpanded) {
      setIsWorkingExpanded(true)
    }
    onInsightIndexChange?.(index)
    onPauseChange?.(true) // Pause when user manually selects
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [isWorkingExpanded, onInsightIndexChange, onPauseChange])

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
    if (!hasMultipleInsights || isInsightsPaused) {
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
  }, [hasMultipleInsights, isInsightsPaused])

  // Reset countdown when insight changes
  useEffect(() => {
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [currentInsightIndex])

  const adjustTextareaHeight = useCallback(
    (reset = false) => {
      const node = textareaRef.current
      if (!node) return
      if (reset) {
        node.style.height = ''
      }
      node.style.height = 'auto'
      const nextHeight = Math.min(node.scrollHeight, MAX_COMPOSER_HEIGHT)
      node.style.height = `${nextHeight}px`
      node.style.overflowY = node.scrollHeight > MAX_COMPOSER_HEIGHT ? 'auto' : 'hidden'
    },
    [MAX_COMPOSER_HEIGHT],
  )

  useEffect(() => {
    adjustTextareaHeight()
  }, [body, adjustTextareaHeight])

  useEffect(() => {
    adjustTextareaHeight(true)
  }, [adjustTextareaHeight])

  // Auto-focus the textarea when autoFocus prop is true
  useEffect(() => {
    if (!autoFocus) return
    // Use a small delay to ensure the DOM is ready after navigation
    const timer = setTimeout(() => {
      textareaRef.current?.focus()
    }, 100)
    return () => clearTimeout(timer)
  }, [autoFocus])

  useEffect(() => {
    const node = shellRef.current
    if (!node || typeof window === 'undefined') return

    const updateComposerHeight = () => {
      const height = node.getBoundingClientRect().height
      document.documentElement.style.setProperty('--composer-height', `${height}px`)
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.setProperty('--composer-height', `${height}px`)
      }
    }

    updateComposerHeight()

    const observer = new ResizeObserver(updateComposerHeight)
    observer.observe(node)

    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--composer-height')
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.removeProperty('--composer-height')
      }
    }
  }, [])

  const submitMessage = useCallback(async () => {
    const trimmed = body.trim()
    if ((!trimmed && attachments.length === 0) || disabled || isSending) {
      return
    }
    const attachmentsSnapshot = attachments.slice()
    if (onSubmit) {
      try {
        setIsSending(true)
        setBody('')
        setAttachments([])
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        requestAnimationFrame(() => adjustTextareaHeight(true))
        await onSubmit(trimmed, attachmentsSnapshot)
      } finally {
        setIsSending(false)
      }
    } else {
      setBody('')
      setAttachments([])
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      requestAnimationFrame(() => adjustTextareaHeight(true))
    }
  }, [adjustTextareaHeight, attachments, body, disabled, isSending, onSubmit])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await submitMessage()
  }

  const handleKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) {
      return
    }
    const shouldSend = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
    if (!shouldSend) {
      return
    }
    event.preventDefault()
    await submitMessage()
  }

  const addAttachments = useCallback((files: File[]) => {
    if (disabled || isSending) {
      return
    }
    if (!files.length) {
      return
    }
    setAttachments((current) => [...current, ...files])
  }, [disabled, isSending])

  const handleAttachmentChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    addAttachments(files)
    event.target.value = ''
  }, [addAttachments])

  const removeAttachment = useCallback((index: number) => {
    setAttachments((current) => current.filter((_, currentIndex) => currentIndex !== index))
  }, [])

  useEffect(() => {
    const hasFiles = (event: DragEvent) => {
      const types = Array.from(event.dataTransfer?.types ?? [])
      return types.includes('Files')
    }

    const handleDragEnter = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current += 1
      setIsDragActive(true)
    }

    const handleDragOver = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
    }

    const handleDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = Math.max(0, dragCounter.current - 1)
      if (dragCounter.current === 0) {
        setIsDragActive(false)
      }
    }

    const handleDrop = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = 0
      setIsDragActive(false)
      const files = Array.from(event.dataTransfer?.files ?? [])
      addAttachments(files)
    }

    window.addEventListener('dragenter', handleDragEnter)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('dragleave', handleDragLeave)
    window.addEventListener('drop', handleDrop)

    return () => {
      window.removeEventListener('dragenter', handleDragEnter)
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('dragleave', handleDragLeave)
      window.removeEventListener('drop', handleDrop)
    }
  }, [addAttachments, disabled, isSending])

  // Show the panel when processing OR when there are insights to display
  const showWorkingPanel = isProcessing || hasInsights
  const taskCount = processingTasks.length

  return (
    <div
      className="composer-shell"
      id="agent-composer-shell"
      ref={shellRef}
      data-processing={isProcessing ? 'true' : 'false'}
      data-expanded={isWorkingExpanded ? 'true' : 'false'}
      data-panel-visible={showWorkingPanel ? 'true' : 'false'}
    >
      <div className="composer-surface">
        {/* Working panel - integrated above input */}
        {showWorkingPanel ? (
          <div
            className="composer-working-panel"
            data-expanded={isWorkingExpanded ? 'true' : 'false'}
            style={currentInsight ? { background: getInsightBackground(currentInsight) } : undefined}
          >
            {/* Header row - clickable to toggle, with tabs and chevron */}
            <div
              className="composer-working-header-row"
              onClick={() => setIsWorkingExpanded(!isWorkingExpanded)}
              role="button"
              tabIndex={0}
              aria-expanded={isWorkingExpanded}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setIsWorkingExpanded(!isWorkingExpanded)
                }
              }}
            >
              {isProcessing ? (
                <>
                  <span className="composer-working-pip" aria-hidden="true" />
                  <span className="composer-working-status">
                    <strong>{agentFirstName}</strong> is working
                    <span className="composer-working-ellipsis" aria-label="working">
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                    </span>
                  </span>
                  {taskCount > 0 ? (
                    <span className="composer-working-tasks-badge">
                      {taskCount} {taskCount === 1 ? 'task' : 'tasks'}
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="composer-working-status">
                  <strong>Insights</strong>
                </span>
              )}

              {/* Colored pill tabs in header */}
              {hasMultipleInsights ? (
                <div
                  className="composer-insight-tabs"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <div className="composer-insight-tabs-scroll">
                    {insights.map((insight, index) => {
                      const isActive = index === currentInsightIndex % totalInsights
                      const color = getInsightTabColor(insight)
                      const label = getInsightTabLabel(insight)
                      return (
                        <button
                          key={insight.insightId}
                          type="button"
                          className="composer-insight-tab"
                          data-active={isActive ? 'true' : 'false'}
                          onClick={() => handleTabClick(index)}
                          aria-label={`View ${insight.insightType.replace('_', ' ')} insight`}
                          style={{
                            '--tab-color': color,
                            '--tab-progress': isActive && !isInsightsPaused ? `${countdownProgress}%` : '0%',
                          } as React.CSSProperties}
                        >
                          <span className="composer-insight-tab-inner" />
                          <span className="composer-insight-tab-label">{label}</span>
                          {isActive && !isInsightsPaused && (
                            <span className="composer-insight-tab-progress" />
                          )}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ) : null}

              <span className="composer-working-toggle">
                {isWorkingExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
              </span>
            </div>

            {/* Expanded content */}
            {isWorkingExpanded && hasInsights ? (
              <div
                className="composer-working-content"
                onMouseEnter={handleInsightMouseEnter}
                onMouseLeave={handleInsightMouseLeave}
              >
                <div className="composer-working-insight" key={currentInsight?.insightId}>
                  {currentInsight ? (
                    <InsightEventCard insight={currentInsight} onDismiss={onDismissInsight} />
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Main input form */}
        <form className="flex flex-col" onSubmit={handleSubmit}>
          {isDragActive ? (
            <div className="agent-chat-drop-overlay" aria-hidden="true">
              <div className="agent-chat-drop-overlay__panel">Drop files to upload</div>
            </div>
          ) : null}
          <div className="composer-input-surface flex flex-col gap-2 rounded-[1.25rem] border border-slate-200/60 bg-white px-4 py-3.5 transition">
            <div className="flex items-center gap-3">
              <input
                ref={fileInputRef}
                id={attachmentInputId}
                type="file"
                className="sr-only"
                multiple
                disabled={disabled || isSending}
                onChange={handleAttachmentChange}
              />
              <label
                htmlFor={attachmentInputId}
                className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-slate-200/60 text-slate-400 transition-all duration-200 hover:border-indigo-200 hover:bg-indigo-50/50 hover:text-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:ring-offset-1"
                aria-label="Attach file"
                title="Attach file"
              >
                <Paperclip className="h-4 w-4" aria-hidden="true" />
              </label>
              <textarea
                name="body"
                rows={1}
                required={attachments.length === 0}
                className="block min-h-[1.8rem] w-full flex-1 resize-none border-0 bg-transparent px-0 py-1 text-[0.9375rem] leading-relaxed tracking-[-0.01em] text-slate-800 placeholder:text-slate-400/80 focus:outline-none focus:ring-0"
                placeholder="Send a message..."
                value={body}
                onChange={(event) => setBody(event.target.value)}
                onKeyDown={handleKeyDown}
                disabled={disabled}
                ref={textareaRef}
              />
              <button
                type="submit"
                className="composer-send-button"
                disabled={disabled || isSending || (!body.trim() && attachments.length === 0)}
                title={isSending ? 'Sending' : 'Send (Cmd/Ctrl+Enter)'}
                aria-label={isSending ? 'Sending message' : 'Send message (Cmd/Ctrl+Enter)'}
              >
                {isSending ? (
                  <span className="inline-flex items-center justify-center">
                    <span
                      className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-white"
                      aria-hidden="true"
                    />
                    <span className="sr-only">Sending</span>
                  </span>
                ) : (
                  <>
                    <ArrowUp className="h-4 w-4" aria-hidden="true" />
                    <span className="sr-only">Send</span>
                  </>
                )}
              </button>
            </div>
            {attachments.length > 0 ? (
              <div className="flex flex-wrap gap-2 pt-0.5 text-xs">
                {attachments.map((file, index) => (
                  <span
                    key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                    className="inline-flex max-w-full items-center gap-2 rounded-full border border-indigo-100 bg-indigo-50/60 px-3 py-1 text-indigo-700 transition-colors hover:bg-indigo-50"
                  >
                    <span className="max-w-[160px] truncate font-medium" title={file.name}>
                      {file.name}
                    </span>
                    <button
                      type="button"
                      className="-mr-0.5 inline-flex items-center justify-center rounded-full p-0.5 text-indigo-400 transition-colors hover:bg-indigo-100 hover:text-indigo-600"
                      onClick={() => removeAttachment(index)}
                      disabled={disabled || isSending}
                      aria-label={`Remove ${file.name}`}
                    >
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <p className="composer-shortcut-hint">Cmd/Ctrl+Enter to send</p>
          </div>
        </form>
      </div>
    </div>
  )
}
