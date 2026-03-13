import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import { memo, useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowUp, Paperclip, X, ChevronDown, ChevronUp } from 'lucide-react'

import { InsightEventCard } from './insights'
import { AgentIntelligenceSelector } from './AgentIntelligenceSelector'
import { HumanInputComposerPanel } from './HumanInputComposerPanel'
import type { PendingHumanInputRequest, ProcessingWebTask } from '../../types/agentChat'
import type { InsightEvent, BurnRateMetadata, AgentSetupMetadata } from '../../types/insight'
import { INSIGHT_TIMING } from '../../types/insight'
import { useLocalStorageState } from '../../hooks/useLocalStorageState'
import { useSubscriptionStore } from '../../stores/subscriptionStore'
import { track, AnalyticsEvent } from '../../util/analytics'
import { appendReturnTo } from '../../util/returnTo'
import type { LlmIntelligenceConfig } from '../../types/llmIntelligence'

// Detect if user is on macOS
function isMacOS(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPod|iPhone|iPad/.test(navigator.platform)
}

function shouldShowSubmitShortcutHint(): boolean {
  if (typeof window === 'undefined') return true
  return window.innerWidth >= 768
}

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
      case 'template':
        return 'Share'
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

const workingPanelStorageCodec = {
  serialize: (value: boolean | null) => (value ? 'expanded' : 'collapsed'),
  deserialize: (raw: string): boolean | null => {
    if (raw === 'expanded') return true
    if (raw === 'collapsed') return false
    try {
      const parsed = JSON.parse(raw) as unknown
      return typeof parsed === 'boolean' ? parsed : null
    } catch {
      return null
    }
  },
}

type HumanInputComposerResponse = {
  requestId: string
  selectedOptionKey?: string
  freeText?: string
}

type HumanInputComposerBatchResponse = {
  batchId: string
  responses: HumanInputComposerResponse[]
}

type AgentComposerProps = {
  onSubmit?: (message: string, attachments?: File[]) => void | Promise<void>
  pendingHumanInputRequests?: PendingHumanInputRequest[]
  onRespondHumanInput?: (response: HumanInputComposerResponse | HumanInputComposerBatchResponse) => Promise<void>
  disabled?: boolean
  disabledReason?: string | null
  autoFocus?: boolean
  // Key that triggers re-focus when changed (e.g., agentId for switching agents)
  focusKey?: string | null
  onFocus?: () => void
  insightsPanelStorageKey?: string | null
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
  onCollaborate?: () => void
  hideInsightsPanel?: boolean
  intelligenceConfig?: LlmIntelligenceConfig | null
  intelligenceTier?: string | null
  onIntelligenceChange?: (tier: string) => Promise<boolean>
  allowLockedIntelligenceSelection?: boolean
  intelligenceBusy?: boolean
  intelligenceError?: string | null
  onOpenTaskPacks?: () => void
  canManageAgent?: boolean
  submitError?: string | null
  showSubmitErrorUpgrade?: boolean
}

export const AgentComposer = memo(function AgentComposer({
  onSubmit,
  pendingHumanInputRequests = [],
  onRespondHumanInput,
  disabled = false,
  disabledReason = null,
  autoFocus = false,
  focusKey,
  onFocus,
  insightsPanelStorageKey,
  agentFirstName = 'Agent',
  isProcessing = false,
  processingTasks = [],
  insights = [],
  currentInsightIndex = 0,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange,
  isInsightsPaused = false,
  onCollaborate,
  hideInsightsPanel = false,
  intelligenceConfig = null,
  intelligenceTier = null,
  onIntelligenceChange,
  allowLockedIntelligenceSelection = false,
  intelligenceBusy = false,
  intelligenceError = null,
  onOpenTaskPacks,
  canManageAgent = true,
  submitError = null,
  showSubmitErrorUpgrade = false,
}: AgentComposerProps) {
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const [activeHumanInputRequestId, setActiveHumanInputRequestId] = useState<string | null>(null)
  const [busyHumanInputRequestId, setBusyHumanInputRequestId] = useState<string | null>(null)
  const [draftHumanInputResponses, setDraftHumanInputResponses] = useState<Record<string, HumanInputComposerResponse>>({})
  const [autoWorkingExpanded, setAutoWorkingExpanded] = useState(true)
  const { isProprietaryMode, openUpgradeModal, ensureAuthenticated } = useSubscriptionStore()
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const focusScrollTimeoutRef = useRef<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const attachmentInputId = useId()
  const dragCounter = useRef(0)

  // Countdown timer state for auto-rotation indicator
  const [countdownProgress, setCountdownProgress] = useState(0)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastRotationTimeRef = useRef<number>(Date.now())
  const feedbackMessage = disabledReason || submitError
  const showSubmitErrorAlert = Boolean(submitError && !disabledReason)

  // Track previous processing state for auto-expand/collapse
  const wasProcessingRef = useRef(isProcessing)
  const isProcessingRef = useRef(isProcessing)

  const workingPanelStorageKey = insightsPanelStorageKey
    ? `agent-chat:insights-panel:${insightsPanelStorageKey}`
    : null

  const [isWorkingExpanded, setIsWorkingExpanded] = useLocalStorageState<boolean | null>(
    workingPanelStorageKey,
    null,
    workingPanelStorageCodec,
  )

  const resolvedWorkingExpanded = isWorkingExpanded ?? autoWorkingExpanded

  useEffect(() => {
    isProcessingRef.current = isProcessing
  }, [isProcessing])

  useEffect(() => {
    setAutoWorkingExpanded(true)
    wasProcessingRef.current = isProcessingRef.current
  }, [workingPanelStorageKey])

  // Auto-expand when processing starts, auto-collapse when it ends
  useEffect(() => {
    if (isWorkingExpanded === null) {
      if (!wasProcessingRef.current && isProcessing) {
        // Processing just started - auto-expand
        setAutoWorkingExpanded(true)
      } else if (wasProcessingRef.current && !isProcessing) {
        // Processing just ended - auto-collapse
        setAutoWorkingExpanded(false)
      }
    }
    wasProcessingRef.current = isProcessing
  }, [isProcessing, isWorkingExpanded])

  const MAX_COMPOSER_HEIGHT = 320

  const showIntelligenceSelector = Boolean(intelligenceConfig && intelligenceTier && onIntelligenceChange)
  const handleIntelligenceUpsell = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    if (isProprietaryMode) {
      openUpgradeModal('intelligence_selector')
      return
    }
    if (intelligenceConfig?.upgradeUrl) {
      track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
        source: 'intelligence_selector',
        target: 'upgrade_url',
      })
      window.open(appendReturnTo(intelligenceConfig.upgradeUrl), '_top')
    }
  }, [ensureAuthenticated, intelligenceConfig?.upgradeUrl, isProprietaryMode, openUpgradeModal])

  const handleSubmitErrorUpgrade = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    openUpgradeModal('agent_limit_error')
  }, [ensureAuthenticated, openUpgradeModal])

  // Insight carousel logic
  const totalInsights = insights.length
  const hasMultipleInsights = totalInsights > 1
  const currentInsight = insights[currentInsightIndex % Math.max(1, totalInsights)] ?? null
  const hasInsights = totalInsights > 0
  const isTouchDevice = typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0)

  const scrollToBottom = useCallback(() => {
    if (!isTouchDevice) return
    // Container scrolling: scroll the timeline-shell, not the window
    const container = document.getElementById('timeline-shell')
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [isTouchDevice])

  // Handle tab click - select that insight, expand panel if collapsed, and pause auto-rotation
  const handleTabClick = useCallback((index: number) => {
    // Expand panel if collapsed
    if (!resolvedWorkingExpanded) {
      setIsWorkingExpanded(true)
    }
    onInsightIndexChange?.(index)
    onPauseChange?.(true) // Pause when user manually selects
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)

    // Track the tab click
    const clickedInsight = insights[index]
    if (clickedInsight) {
      track(AnalyticsEvent.INSIGHT_TAB_CLICKED + " - " + clickedInsight.title, {
        insightType: clickedInsight.insightType,
        insightId: clickedInsight.insightId,
        title: clickedInsight.title,
        tabIndex: index,
        totalInsights: insights.length,
      })
    }
  }, [onInsightIndexChange, onPauseChange, resolvedWorkingExpanded, setIsWorkingExpanded, insights])

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

  // Handle panel expand/collapse toggle
  const handlePanelToggle = useCallback(() => {
    const newExpanded = !resolvedWorkingExpanded
    setIsWorkingExpanded(newExpanded)
    track(AnalyticsEvent.INSIGHT_PANEL_TOGGLED + " - " + (newExpanded ? "Open" : "Close"), {
      expanded: newExpanded,
      hasInsights,
      currentInsightType: currentInsight?.insightType ?? null,
    })
  }, [resolvedWorkingExpanded, setIsWorkingExpanded, hasInsights, currentInsight?.insightType])

  // Wrap dismiss handler to track dismissals
  const handleDismissInsight = useCallback((insightId: string) => {
    const dismissedInsight = insights.find((i) => i.insightId === insightId)
    if (dismissedInsight) {
      track(AnalyticsEvent.INSIGHT_DISMISSED, {
        insightType: dismissedInsight.insightType,
        insightId: dismissedInsight.insightId,
      })
    }
    onDismissInsight?.(insightId)
  }, [insights, onDismissInsight])

  // Update countdown progress for the timer indicator (only when processing)
  useEffect(() => {
    if (!hasMultipleInsights || isInsightsPaused || !isProcessing) {
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
  }, [hasMultipleInsights, isInsightsPaused, isProcessing])

  // Reset countdown when insight changes
  useEffect(() => {
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [currentInsightIndex])

  useEffect(() => {
    return () => {
      if (focusScrollTimeoutRef.current !== null) {
        window.clearTimeout(focusScrollTimeoutRef.current)
      }
    }
  }, [])

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

  useEffect(() => {
    const node = textareaRef.current
    if (!node || typeof ResizeObserver === 'undefined') {
      return
    }

    const observer = new ResizeObserver(() => {
      adjustTextareaHeight(true)
    })
    observer.observe(node)

    return () => {
      observer.disconnect()
    }
  }, [adjustTextareaHeight])

  useEffect(() => {
    if (!pendingHumanInputRequests.length) {
      setActiveHumanInputRequestId(null)
      setBusyHumanInputRequestId(null)
      setDraftHumanInputResponses({})
      return
    }
    const hasActiveRequest = pendingHumanInputRequests.some((request) => request.id === activeHumanInputRequestId)
    if (!hasActiveRequest) {
      const latestBatchId = pendingHumanInputRequests[0]?.batchId
      const latestBatchRequests = pendingHumanInputRequests
        .filter((request) => request.batchId === latestBatchId)
        .sort((left, right) => left.batchPosition - right.batchPosition)
      setActiveHumanInputRequestId(latestBatchRequests[0]?.id ?? pendingHumanInputRequests[0]?.id ?? null)
    }
  }, [activeHumanInputRequestId, pendingHumanInputRequests])

  useEffect(() => {
    const pendingIds = new Set(pendingHumanInputRequests.map((request) => request.id))
    setDraftHumanInputResponses((current) => {
      const nextEntries = Object.entries(current).filter(([requestId]) => pendingIds.has(requestId))
      if (nextEntries.length === Object.keys(current).length) {
        return current
      }
      return Object.fromEntries(nextEntries)
    })
  }, [pendingHumanInputRequests])

  // Auto-focus the textarea when autoFocus prop is true or when focusKey changes (agent switch)
  useEffect(() => {
    if (!autoFocus) return
    // Use a small delay to ensure the DOM is ready after navigation
    const timer = setTimeout(() => {
      textareaRef.current?.focus()
    }, 100)
    return () => clearTimeout(timer)
  }, [autoFocus, focusKey])

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

  const activeHumanInputRequest =
    pendingHumanInputRequests.find((request) => request.id === activeHumanInputRequestId)
    ?? null
  const activeHumanInputDraftText = activeHumanInputRequestId
    ? (draftHumanInputResponses[activeHumanInputRequestId]?.freeText ?? '')
    : ''

  useEffect(() => {
    if (!activeHumanInputRequestId) {
      return
    }
    setBody((current) => (current === activeHumanInputDraftText ? current : activeHumanInputDraftText))
  }, [activeHumanInputDraftText, activeHumanInputRequestId])

  const submitShortcutHint = shouldShowSubmitShortcutHint()
    ? `${isMacOS() ? '⌘↵' : 'Ctrl+↵'} to send`
    : ''
  const composerPlaceholder = disabledReason || (activeHumanInputRequest
    ? ['Other option', submitShortcutHint].filter(Boolean).join(' · ')
    : ['Message', submitShortcutHint].filter(Boolean).join(' · '))

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const frame = window.requestAnimationFrame(() => {
      adjustTextareaHeight(true)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [
    activeHumanInputRequestId,
    adjustTextareaHeight,
    composerPlaceholder,
    pendingHumanInputRequests.length,
    resolvedWorkingExpanded,
  ])

  const submitHumanInputResponse = useCallback(async (
    request: PendingHumanInputRequest,
    response: HumanInputComposerResponse,
  ) => {
    if (!onRespondHumanInput || disabled || isSending || busyHumanInputRequestId) {
      return false
    }

    const batchRequests = pendingHumanInputRequests
      .filter((candidate) => candidate.batchId === request.batchId)
      .sort((left, right) => left.batchPosition - right.batchPosition)
    const nextDrafts = {
      ...draftHumanInputResponses,
      [request.id]: response,
    }

    if (batchRequests.length > 1) {
      const nextUnanswered = batchRequests.find((candidate) => !nextDrafts[candidate.id])
      if (nextUnanswered) {
        setDraftHumanInputResponses(nextDrafts)
        setActiveHumanInputRequestId(nextUnanswered.id)
        setBody('')
        requestAnimationFrame(() => adjustTextareaHeight(true))
        return true
      }
    }

    try {
      setBusyHumanInputRequestId(request.id)
      if (batchRequests.length > 1) {
        const responses = batchRequests
          .map((candidate) => nextDrafts[candidate.id])
          .filter((candidate): candidate is HumanInputComposerResponse => Boolean(candidate))
        await onRespondHumanInput({
          batchId: request.batchId,
          responses,
        })
        setDraftHumanInputResponses((current) => {
          const remaining = { ...current }
          batchRequests.forEach((candidate) => {
            delete remaining[candidate.id]
          })
          return remaining
        })
      } else {
        await onRespondHumanInput(response)
        setDraftHumanInputResponses((current) => {
          if (!current[request.id]) {
            return current
          }
          const remaining = { ...current }
          delete remaining[request.id]
          return remaining
        })
      }
      setBody('')
      requestAnimationFrame(() => adjustTextareaHeight(true))
      return true
    } finally {
      setBusyHumanInputRequestId(null)
    }
  }, [
    adjustTextareaHeight,
    busyHumanInputRequestId,
    disabled,
    draftHumanInputResponses,
    isSending,
    onRespondHumanInput,
    pendingHumanInputRequests,
  ])

  const handleActiveHumanInputRequestChange = useCallback((nextRequestId: string) => {
    const currentRequest = pendingHumanInputRequests.find((request) => request.id === activeHumanInputRequestId) ?? null
    const trimmedBody = body.trim()
    if (currentRequest) {
      setDraftHumanInputResponses((current) => {
        const next = { ...current }
        const existing = next[currentRequest.id]
        if (trimmedBody) {
          next[currentRequest.id] = {
            ...existing,
            requestId: currentRequest.id,
            freeText: trimmedBody,
          }
        } else if (existing?.selectedOptionKey) {
          next[currentRequest.id] = {
            ...existing,
            requestId: currentRequest.id,
            freeText: '',
          }
        } else {
          delete next[currentRequest.id]
        }
        return next
      })
    }
    setActiveHumanInputRequestId(nextRequestId)
  }, [activeHumanInputRequestId, body, pendingHumanInputRequests])

  const submitMessage = useCallback(async () => {
    const trimmed = body.trim()
    if ((!trimmed && attachments.length === 0) || disabled || isSending) {
      return
    }
    const attachmentsSnapshot = attachments.slice()
    const activeRequest = pendingHumanInputRequests.find((request) => request.id === activeHumanInputRequestId) ?? null
    if (activeRequest && trimmed && attachmentsSnapshot.length === 0 && onRespondHumanInput) {
      const submitted = await submitHumanInputResponse(activeRequest, {
        requestId: activeRequest.id,
        freeText: trimmed,
      })
      if (submitted) {
        return
      }
    }
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
  }, [
    activeHumanInputRequestId,
    adjustTextareaHeight,
    attachments,
    body,
    disabled,
    isSending,
    onRespondHumanInput,
    onSubmit,
    pendingHumanInputRequests,
    submitHumanInputResponse,
  ])

  const handleSelectHumanInputOption = useCallback(async (requestId: string, optionKey: string) => {
    const request = pendingHumanInputRequests.find((candidate) => candidate.id === requestId)
    if (!request) {
      return
    }
    await submitHumanInputResponse(request, { requestId, selectedOptionKey: optionKey })
  }, [pendingHumanInputRequests, submitHumanInputResponse])

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
  const showWorkingPanel = !hideInsightsPanel && (isProcessing || hasInsights)
  const taskCount = processingTasks.length

  return (
    <div
      className="composer-shell"
      id="agent-composer-shell"
      ref={shellRef}
      data-processing={isProcessing ? 'true' : 'false'}
      data-expanded={resolvedWorkingExpanded ? 'true' : 'false'}
      data-panel-visible={showWorkingPanel ? 'true' : 'false'}
    >
      <div className="composer-surface">
        {/* Working panel - integrated above input */}
        {showWorkingPanel ? (
          <div
            className="composer-working-panel"
            data-expanded={resolvedWorkingExpanded ? 'true' : 'false'}
            style={currentInsight ? { background: getInsightBackground(currentInsight) } : undefined}
          >
            {/* Header row - clickable to toggle, with tabs and chevron */}
            <div
              className="composer-working-header-row"
              onClick={handlePanelToggle}
              role="button"
              tabIndex={0}
              aria-expanded={resolvedWorkingExpanded}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handlePanelToggle()
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
                            '--tab-progress': isActive && !isInsightsPaused && isProcessing ? `${countdownProgress}%` : '0%',
                          } as React.CSSProperties}
                        >
                          <span className="composer-insight-tab-inner" />
                          <span className="composer-insight-tab-label">{label}</span>
                          {isActive && !isInsightsPaused && isProcessing && (
                            <span className="composer-insight-tab-progress" />
                          )}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ) : null}

              <span className="composer-working-toggle">
                {resolvedWorkingExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
              </span>
            </div>

            {/* Expanded content */}
            {resolvedWorkingExpanded && hasInsights ? (
              <div
                className="composer-working-content"
                onMouseEnter={handleInsightMouseEnter}
                onMouseLeave={handleInsightMouseLeave}
              >
                <div className="composer-working-insight" key={currentInsight?.insightId}>
                  {currentInsight ? (
                    <InsightEventCard
                      insight={currentInsight}
                      onDismiss={handleDismissInsight}
                      onCollaborate={onCollaborate}
                    />
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {pendingHumanInputRequests.length > 0 ? (
          <div>
            <HumanInputComposerPanel
              requests={pendingHumanInputRequests}
              activeRequestId={activeHumanInputRequestId}
              draftResponses={draftHumanInputResponses}
              disabled={disabled || isSending}
              busyRequestId={busyHumanInputRequestId}
              onActiveRequestChange={handleActiveHumanInputRequestChange}
              onSelectOption={handleSelectHumanInputOption}
            />
          </div>
        ) : null}

        {/* Main input form */}
        <form className="flex flex-col" onSubmit={handleSubmit}>
          {isDragActive ? (
            <div className="agent-chat-drop-overlay" aria-hidden="true">
              <div className="agent-chat-drop-overlay__panel">Drop files to upload</div>
            </div>
          ) : null}
          <div className="composer-input-surface flex flex-col rounded-[1.25rem] border border-slate-200/60 bg-white px-4 py-3 transition">
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
                className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-slate-200/60 text-slate-400 transition-all ${
                  disabled
                    ? 'cursor-not-allowed opacity-60'
                    : 'cursor-pointer hover:border-slate-300 hover:text-slate-500'
                }`}
                aria-label="Attach file"
                title={disabledReason || 'Attach file'}
              >
                <Paperclip className="h-4 w-4" aria-hidden="true" />
              </label>
              <textarea
                name="body"
                rows={1}
                required={attachments.length === 0}
                className="block min-h-[1.8rem] w-full flex-1 resize-none border-0 bg-transparent px-0 py-1 text-[0.9375rem] leading-relaxed tracking-[-0.01em] text-slate-800 placeholder:text-slate-400/80 focus:outline-none focus:ring-0"
                placeholder={composerPlaceholder}
                value={body}
                onChange={(event) => setBody(event.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => {
                  onFocus?.()
                  if (!isTouchDevice) return
                  if (focusScrollTimeoutRef.current !== null) {
                    window.clearTimeout(focusScrollTimeoutRef.current)
                  }
                  focusScrollTimeoutRef.current = window.setTimeout(scrollToBottom, 60)
                }}
                disabled={disabled}
                ref={textareaRef}
              />
              {showIntelligenceSelector ? (
                <AgentIntelligenceSelector
                  config={intelligenceConfig as LlmIntelligenceConfig}
                  currentTier={intelligenceTier ?? 'standard'}
                  onSelect={(tier) => onIntelligenceChange?.(tier)}
                  onUpsell={allowLockedIntelligenceSelection ? undefined : handleIntelligenceUpsell}
                  onOpenTaskPacks={onOpenTaskPacks}
                  allowLockedSelection={allowLockedIntelligenceSelection}
                  disabled={!canManageAgent}
                  busy={intelligenceBusy}
                  error={intelligenceError}
                />
              ) : null}
              <button
                type="submit"
                className="composer-send-button"
                disabled={disabled || isSending || (!body.trim() && attachments.length === 0)}
                title={disabledReason || (isSending ? 'Sending' : `Send (${isMacOS() ? '⌘↵' : 'Ctrl+Enter'})`)}
                aria-label={isSending ? 'Sending message' : 'Send message'}
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
            {feedbackMessage ? (
              <div
                className="composer-submit-error"
                role={showSubmitErrorAlert ? 'alert' : undefined}
                aria-live={showSubmitErrorAlert ? 'polite' : undefined}
              >
                <span className="composer-submit-error-text">{feedbackMessage}</span>
                {!disabledReason && showSubmitErrorUpgrade && isProprietaryMode && canManageAgent ? (
                  <button
                    type="button"
                    className="composer-submit-error-upgrade"
                    onClick={() => void handleSubmitErrorUpgrade()}
                  >
                    Upgrade plan
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </form>
      </div>
    </div>
  )
})
