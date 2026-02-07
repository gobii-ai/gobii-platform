import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { ArrowUpRight, CheckCircle2, Search } from 'lucide-react'

import { useAgentChatStore } from '../../stores/agentChatStore'
import { formatRelativeTimestamp } from '../../util/time'
import { ToolIconSlot } from './ToolIconSlot'
import { deriveSemanticPreview } from './tooling/clusterPreviewText'
import type { ToolClusterTransform, ToolEntryDisplay } from './tooling/types'

type ToolClusterLivePreviewProps = {
  cluster: ToolClusterTransform
  isLatestEvent: boolean
  timelineDialogId: string
  timelineOpen: boolean
  onOpenTimeline: () => void
}

type PreviewEntry = {
  entry: ToolEntryDisplay
  activity: ActivityDescriptor
  relativeTime: string | null
}

type ActivityKind = 'linkedin' | 'search' | 'snapshot' | 'thinking' | 'kanban' | 'tool'
type PreviewPulse = 'start' | 'finish' | null
type PreviewState = 'active' | 'error' | 'complete'

type ActivityDescriptor = {
  kind: ActivityKind
  label: string
  detail: string | null
  activeHeadline: string
  completeHeadline: string
}

const MAX_DETAIL_LENGTH = 88

function clampText(value: string, maxLength: number = MAX_DETAIL_LENGTH): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) {
    return normalized
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`
}

function parseLinkedInTarget(value: string | null): string | null {
  if (!value) {
    return null
  }

  const normalized = value.trim()
  const withProtocol = normalized.startsWith('http') ? normalized : `https://${normalized}`
  try {
    const url = new URL(withProtocol)
    if (!url.hostname.includes('linkedin.com')) {
      return clampText(normalized)
    }
    const parts = url.pathname.split('/').filter(Boolean)
    if (parts.length < 2) {
      return 'LinkedIn page'
    }
    const [section, slug] = parts
    const cleanSlug = slug.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim()
    if (!cleanSlug) {
      return section === 'company' ? 'Company page' : 'Profile page'
    }
    return clampText(cleanSlug.replace(/\b\w/g, (char) => char.toUpperCase()), 64)
  } catch {
    return clampText(normalized)
  }
}

function parseSearchQuery(value: string | null): string | null {
  if (!value) {
    return null
  }
  const cleaned = value.split('•')[0]?.trim() ?? value.trim()
  const quoteMatch = cleaned.match(/[“"]([^”"]+)[”"]/)
  if (quoteMatch?.[1]) {
    return clampText(quoteMatch[1], 64)
  }
  return clampText(cleaned, 64)
}

function classifyActivity(entry: ToolEntryDisplay): ActivityKind {
  const toolName = (entry.toolName || '').toLowerCase()
  const label = entry.label.toLowerCase()
  if (toolName === 'thinking') return 'thinking'
  if (toolName === 'kanban') return 'kanban'
  if (toolName.includes('linkedin') || label.includes('linkedin')) return 'linkedin'
  if (toolName.includes('search') || label.includes('search')) return 'search'
  if (
    toolName.includes('scrape_as_markdown') ||
    toolName.includes('scrape_as_html') ||
    label.includes('web snapshot')
  ) {
    return 'snapshot'
  }
  return 'tool'
}

function deriveLinkedInLabel(toolName: string): string {
  if (toolName.includes('company')) return 'Browsing company page'
  if (toolName.includes('people_search')) return 'Searching people'
  if (toolName.includes('job')) return 'Scanning job listings'
  if (toolName.includes('posts')) return 'Scanning posts'
  return 'Browsing profile'
}

function deriveActivityDescriptor(entry: ToolEntryDisplay): ActivityDescriptor {
  const semantic = deriveSemanticPreview(entry)
  const kind = classifyActivity(entry)
  const toolName = (entry.toolName || '').toLowerCase()

  if (kind === 'linkedin') {
    const target = parseLinkedInTarget(semantic ?? entry.caption ?? entry.summary ?? null)
    const label = deriveLinkedInLabel(toolName)
    return {
      kind,
      label,
      detail: target,
      activeHeadline: target ? `Browsing LinkedIn: ${target}` : 'Browsing LinkedIn profiles',
      completeHeadline: target ? `LinkedIn details captured: ${target}` : 'LinkedIn details captured',
    }
  }

  if (kind === 'search') {
    const query = parseSearchQuery(semantic ?? entry.caption ?? entry.summary ?? null)
    return {
      kind,
      label: 'Searching web',
      detail: query ? `“${query}”` : null,
      activeHeadline: query ? `Searching for “${query}”` : 'Searching the web',
      completeHeadline: query ? `Search complete for “${query}”` : 'Search results captured',
    }
  }

  if (kind === 'snapshot') {
    const target = clampText(semantic ?? entry.caption ?? entry.summary ?? 'Web page')
    return {
      kind,
      label: 'Reading page',
      detail: target,
      activeHeadline: `Reading ${target}`,
      completeHeadline: `Page captured: ${target}`,
    }
  }

  if (kind === 'thinking') {
    const thought = clampText(semantic ?? 'Planning next steps')
    return {
      kind,
      label: 'Planning next step',
      detail: thought,
      activeHeadline: thought,
      completeHeadline: 'Plan updated',
    }
  }

  if (kind === 'kanban') {
    const detail = clampText(semantic ?? entry.caption ?? 'Kanban board updated')
    return {
      kind,
      label: 'Updating kanban',
      detail,
      activeHeadline: detail,
      completeHeadline: detail,
    }
  }

  const detail = semantic ? clampText(semantic) : null
  return {
    kind,
    label: entry.label,
    detail,
    activeHeadline: detail ?? entry.label,
    completeHeadline: detail ?? `${entry.label} complete`,
  }
}

function derivePreviewState(activeEntry: ToolEntryDisplay | null, hasActiveProcessing: boolean): PreviewState {
  if (activeEntry?.status === 'error') {
    return 'error'
  }
  if (!activeEntry) {
    return hasActiveProcessing ? 'active' : 'complete'
  }
  if (activeEntry.status === 'pending' || activeEntry.toolName === 'thinking' || hasActiveProcessing) {
    return 'active'
  }
  return 'complete'
}

function stateLabel(state: PreviewState, pulse: PreviewPulse): string {
  if (pulse === 'finish') {
    return 'Step finished'
  }
  if (state === 'active') {
    return 'Working live'
  }
  if (state === 'error') {
    return 'Needs attention'
  }
  return 'Recent activity'
}

function buildHeadline(entry: PreviewEntry | null): string {
  if (!entry) {
    return 'Preparing next step…'
  }
  return entry.activity.completeHeadline
}

export function ToolClusterLivePreview({ cluster, isLatestEvent, timelineDialogId, timelineOpen, onOpenTimeline }: ToolClusterLivePreviewProps) {
  const reduceMotion = useReducedMotion()
  const processingActive = useAgentChatStore((state) => state.processingActive)
  const [pulse, setPulse] = useState<PreviewPulse>(null)
  const [newEntryIds, setNewEntryIds] = useState<string[]>([])
  const previousEntryIdsRef = useRef<string[]>([])
  const previousPendingCountRef = useRef(0)
  const previousProcessingRef = useRef(false)
  const pulseTimeoutRef = useRef<number | null>(null)

  const previewEntries = useMemo<PreviewEntry[]>(
    () =>
      cluster.entries
        .slice(-3)
        .map((entry) => ({
          entry,
          activity: deriveActivityDescriptor(entry),
          relativeTime: formatRelativeTimestamp(entry.timestamp),
        })),
    [cluster.entries],
  )

  const activePreviewEntry = useMemo<PreviewEntry | null>(() => {
    const pendingEntry = [...previewEntries].reverse().find((item) => item.entry.status === 'pending')
    return pendingEntry ?? previewEntries[previewEntries.length - 1] ?? null
  }, [previewEntries])

  const pendingCount = useMemo(
    () => cluster.entries.filter((entry) => entry.status === 'pending' || entry.toolName === 'thinking').length,
    [cluster.entries],
  )
  const hasActiveProcessing = processingActive && isLatestEvent
  const previewState = derivePreviewState(activePreviewEntry?.entry ?? null, hasActiveProcessing)
  const activeEntryId = activePreviewEntry?.entry.id ?? null
  const headline = useMemo(() => {
    if (!activePreviewEntry) {
      return buildHeadline(null)
    }
    if (previewState === 'active') {
      return activePreviewEntry.activity.activeHeadline
    }
    if (previewState === 'error') {
      return `Issue while ${activePreviewEntry.activity.label.toLowerCase()}`
    }
    return activePreviewEntry.activity.completeHeadline
  }, [activePreviewEntry, previewState])
  const newEntryIdSet = useMemo(() => new Set(newEntryIds), [newEntryIds])

  useEffect(() => {
    const currentEntryIds = cluster.entries.map((entry) => entry.id)
    const previousEntryIds = previousEntryIdsRef.current
    const addedEntryIds = currentEntryIds.filter((id) => !previousEntryIds.includes(id))
    const previousPendingCount = previousPendingCountRef.current
    const previousProcessing = previousProcessingRef.current
    const finishedWork =
      (previousPendingCount > 0 && pendingCount === 0) ||
      (previousProcessing && !hasActiveProcessing)
    const startedWork =
      addedEntryIds.length > 0 ||
      (previousPendingCount === 0 && pendingCount > 0) ||
      (!previousProcessing && hasActiveProcessing)

    if (addedEntryIds.length > 0) {
      setNewEntryIds(addedEntryIds.slice(-3))
    }

    if (finishedWork) {
      setPulse('finish')
    } else if (startedWork) {
      setPulse('start')
    }

    previousEntryIdsRef.current = currentEntryIds
    previousPendingCountRef.current = pendingCount
    previousProcessingRef.current = hasActiveProcessing
  }, [cluster.entries, hasActiveProcessing, pendingCount])

  useEffect(() => {
    if (pulse === null && newEntryIds.length === 0) {
      return
    }
    if (pulseTimeoutRef.current !== null) {
      window.clearTimeout(pulseTimeoutRef.current)
    }
    pulseTimeoutRef.current = window.setTimeout(() => {
      setPulse(null)
      setNewEntryIds([])
      pulseTimeoutRef.current = null
    }, pulse === 'finish' ? 1150 : 900)
    return () => {
      if (pulseTimeoutRef.current !== null) {
        window.clearTimeout(pulseTimeoutRef.current)
        pulseTimeoutRef.current = null
      }
    }
  }, [newEntryIds, pulse])

  const showFinishBadge = pulse === 'finish' && !reduceMotion

  return (
    <motion.button
      type="button"
      className="tool-cluster-live-preview"
      data-state={previewState}
      data-pulse={pulse ?? 'none'}
      aria-expanded={timelineOpen ? 'true' : 'false'}
      aria-haspopup="dialog"
      aria-controls={timelineDialogId}
      aria-label={`Open timeline preview with ${cluster.entryCount} events`}
      onClick={onOpenTimeline}
      whileTap={reduceMotion ? undefined : { scale: 0.995 }}
      transition={{ duration: 0.14, ease: 'easeOut' }}
      animate={
        reduceMotion
          ? undefined
          : pulse === 'start'
            ? { scale: [1, 1.004, 1] }
            : pulse === 'finish'
              ? { scale: [1, 1.01, 1] }
              : undefined
      }
    >
      <AnimatePresence>
        {pulse ? (
          <motion.span
            key={pulse}
            className="tool-cluster-live-preview__pulse"
            data-pulse={pulse}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            aria-hidden="true"
          />
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {showFinishBadge ? (
          <motion.span
            className="tool-cluster-live-preview__finish-badge"
            initial={{ opacity: 0, scale: 0.7, y: 6 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.8, y: -4 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            aria-hidden="true"
          >
            <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2.3} />
          </motion.span>
        ) : null}
      </AnimatePresence>
      <span className="tool-cluster-live-preview__header">
        <span className="tool-cluster-live-preview__badge" data-state={previewState}>
          <span className="tool-cluster-live-preview__badge-dot" aria-hidden="true" />
          {stateLabel(previewState, pulse)}
        </span>
        <span className="tool-cluster-live-preview__meta">
          <span className="tool-cluster-live-preview__count">
            {cluster.entryCount} {cluster.entryCount === 1 ? 'event' : 'events'}
          </span>
          {activePreviewEntry?.relativeTime ? (
            <span className="tool-cluster-live-preview__updated">Updated {activePreviewEntry.relativeTime}</span>
          ) : null}
        </span>
        <span className="tool-cluster-live-preview__expand" aria-hidden="true">
          <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.2} />
        </span>
      </span>

      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={activeEntryId ?? 'empty'}
          className="tool-cluster-live-preview__headline"
          initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 4 }}
          animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
        >
          {headline}
        </motion.span>
      </AnimatePresence>

      <span className="tool-cluster-live-preview__window" role="list" aria-label="Recent tool activity">
        {previewEntries.map((item, index) => {
          const { entry } = item
          const isActive = entry.id === activeEntryId
          const isNew = newEntryIdSet.has(entry.id)
          const showSearchSweep = !reduceMotion && isActive && item.activity.kind === 'search' && previewState === 'active'
          return (
            <motion.span
              key={entry.id}
              layout={!reduceMotion}
              role="listitem"
              className="tool-cluster-live-preview__entry"
              data-active={isActive ? 'true' : 'false'}
              data-kind={item.activity.kind}
              data-new={isNew ? 'true' : 'false'}
              initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 3 }}
              animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: 'easeOut', delay: reduceMotion ? 0 : index * 0.04 }}
            >
              {showSearchSweep ? (
                <motion.span
                  className="tool-cluster-live-preview__search-sweep"
                  initial={{ x: '-120%', opacity: 0 }}
                  animate={{ x: ['-120%', '125%'], opacity: [0, 0.8, 0] }}
                  transition={{ duration: 0.95, ease: 'easeInOut', repeat: Infinity, repeatDelay: 0.15 }}
                  aria-hidden="true"
                />
              ) : null}
              <motion.span
                className={`tool-cluster-live-preview__entry-icon ${entry.iconBgClass} ${entry.iconColorClass}`}
                animate={
                  reduceMotion || !isActive
                    ? undefined
                    : item.activity.kind === 'linkedin'
                      ? { scale: [1, 1.06, 1] }
                      : item.activity.kind === 'search'
                        ? { rotate: [0, -4, 4, 0] }
                        : { scale: [1, 1.03, 1] }
                }
                transition={
                  reduceMotion || !isActive
                    ? undefined
                    : item.activity.kind === 'search'
                      ? { duration: 0.6, repeat: Infinity, ease: 'easeInOut' }
                      : { duration: 1.2, repeat: Infinity, ease: 'easeInOut' }
                }
              >
                <ToolIconSlot entry={entry} />
              </motion.span>
              <span className="tool-cluster-live-preview__entry-main">
                <span className="tool-cluster-live-preview__entry-label-row">
                  <span className="tool-cluster-live-preview__entry-label">{item.activity.label}</span>
                  {item.activity.kind === 'search' ? <Search className="tool-cluster-live-preview__entry-search-icon" aria-hidden="true" /> : null}
                </span>
                {item.activity.detail ? <span className="tool-cluster-live-preview__entry-caption">{item.activity.detail}</span> : null}
              </span>
              {item.relativeTime ? (
                <time className="tool-cluster-live-preview__entry-time" dateTime={entry.timestamp ?? undefined}>
                  {item.relativeTime}
                </time>
              ) : null}
            </motion.span>
          )
        })}
      </span>
    </motion.button>
  )
}
