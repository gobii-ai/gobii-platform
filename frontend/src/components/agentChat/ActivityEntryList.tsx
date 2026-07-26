import { useCallback, useEffect, useRef, useState } from 'react'

import { formatRelativeTimestamp } from '../../util/time'
import { slugify } from '../../util/slugify'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { ToolIconSlot } from './ToolIconSlot'
import { ToolProviderBadge } from './ToolProviderBadge'
import { deriveActivityEntryPresentation } from './tooling/activityPresentation'
import { deriveEntryCaption, deriveThinkingPreview } from './tooling/clusterPreviewText'
import { MoodShiftCard } from './MoodShiftCard'
import type { ToolEntryDisplay } from './tooling/types'

type ActivityEntryListProps = {
  entries: ToolEntryDisplay[]
  initialOpenEntryId?: string | null
}

export function ActivityEntryList({
  entries,
  initialOpenEntryId = null,
}: ActivityEntryListProps) {
  const entryRowRefs = useRef<Record<string, HTMLLIElement | null>>({})
  const initialOpenEntryIdRef = useRef<string | null>(null)
  const [openEntryId, setOpenEntryId] = useState<string | null>(null)
  const visibleEntries = entries

  useEffect(() => {
    if (!initialOpenEntryId || initialOpenEntryIdRef.current === initialOpenEntryId) {
      return
    }
    initialOpenEntryIdRef.current = initialOpenEntryId
    const hasTarget = visibleEntries.some((entry) => entry.id === initialOpenEntryId)
    setOpenEntryId(hasTarget ? initialOpenEntryId : null)
  }, [initialOpenEntryId, visibleEntries])

  useEffect(() => {
    if (!openEntryId) {
      return
    }
    const hasOpenEntry = visibleEntries.some((entry) => entry.id === openEntryId)
    if (!hasOpenEntry) {
      setOpenEntryId(null)
    }
  }, [openEntryId, visibleEntries])

  useEffect(() => {
    if (!openEntryId) {
      return
    }
    const row = entryRowRefs.current[openEntryId]
    if (row) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [openEntryId])

  const handleToggleEntry = useCallback((entryId: string) => {
    setOpenEntryId((current) => (current === entryId ? null : entryId))
  }, [])

  return (
    <>
      <ol className="tool-cluster-timeline-list" role="list">
        {visibleEntries.map((entry) => {
          const detailId = `tool-cluster-timeline-detail-${slugify(entry.id)}`
          const isOpen = openEntryId === entry.id
          const relativeTime = formatRelativeTimestamp(entry.timestamp)
          const presentation = deriveActivityEntryPresentation(entry)
          const caption = presentation.caption ?? deriveEntryCaption(entry)
          const thinkingPreview = deriveThinkingPreview(entry)
          const kind = entry.toolName === 'thinking' ? 'thinking' : 'tool'
          const DetailComponent = entry.detailComponent
          const presentationEntry = presentation.icon === entry.icon && presentation.label === entry.label
            ? entry
            : { ...entry, label: presentation.label, icon: presentation.icon ?? entry.icon }

          // A mood is not a tool call with a result to inspect; it is one fact, already fully
          // visible. Giving it a disclosure row to expand would be giving it nothing to show.
          if (entry.emotion !== undefined) {
            return (
              <li
                key={entry.id}
                className="tool-cluster-timeline-item"
                data-kind="mood"
                data-entry-id={entry.id}
                ref={(node) => {
                  entryRowRefs.current[entry.id] = node
                }}
              >
                <MoodShiftCard entry={entry} />
              </li>
            )
          }

          return (
            <li
              key={entry.id}
              className="tool-cluster-timeline-item"
              data-kind={kind}
              data-entry-id={entry.id}
              ref={(node) => {
                entryRowRefs.current[entry.id] = node
              }}
            >
              <button
                type="button"
                className="tool-cluster-timeline-row"
                aria-expanded={isOpen ? 'true' : 'false'}
                aria-controls={detailId}
                data-open={isOpen ? 'true' : 'false'}
                onClick={() => handleToggleEntry(entry.id)}
              >
                <span className={`tool-cluster-timeline-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
                  <ToolIconSlot entry={presentationEntry} />
                </span>
                <span className="tool-cluster-timeline-main">
                  <span className="tool-cluster-timeline-label-row">
                    <span className="tool-cluster-timeline-label">{presentation.label}</span>
                    <ToolProviderBadge entry={entry} className="tool-provider-badge--timeline" />
                  </span>
                  {caption ? <span className="tool-cluster-timeline-caption">{caption}</span> : null}
                  {thinkingPreview ? (
                    <div className="tool-cluster-timeline-preview">
                      <MarkdownViewer
                        content={thinkingPreview}
                        className="tool-cluster-timeline-preview-markdown"
                        enableHighlight={false}
                      />
                    </div>
                  ) : null}
                </span>
                {entry.timestamp ? (
                  <time
                    className="tool-cluster-timeline-time"
                    dateTime={entry.timestamp ?? undefined}
                    title={entry.timestamp ?? undefined}
                  >
                    {relativeTime ?? entry.timestamp}
                  </time>
                ) : null}
              </button>
              {isOpen ? (
                <div className="tool-cluster-timeline-detail" id={detailId} role="region" aria-label={`${presentation.label} details`}>
                  <DetailComponent entry={entry} />
                </div>
              ) : null}
            </li>
          )
        })}
      </ol>
    </>
  )
}
