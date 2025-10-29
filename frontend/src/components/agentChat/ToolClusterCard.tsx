import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Workflow } from 'lucide-react'
import { useToolDetailController, entryKey } from './tooling/ToolDetailContext'
import { transformToolCluster, isClusterRenderable } from './tooling/toolRegistry'
import type { ToolClusterEvent } from './types'
import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'
import { scrollIntoViewIfNeeded } from './scrollIntoView'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
}

function ToolIcon({ icon, className }: { icon: ToolEntryDisplay['icon'] | undefined; className?: string }) {
  const IconComponent = icon ?? Workflow
  return <IconComponent className={className} aria-hidden="true" />
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function ToolClusterCard({ cluster }: ToolClusterCardProps) {
  const transformed = useMemo(() => transformToolCluster(cluster), [cluster])

  const { openKey, setOpenKey } = useToolDetailController()
  const [collapsed, setCollapsed] = useState<boolean>(transformed.collapsible)
  const detailHostRef = useRef<HTMLDivElement>(null)
  const closeScrollRef = useRef<number | null>(null)
  const pendingScrollKeyRef = useRef<string | null>(null)
  const lastOpenKeyRef = useRef<string | null>(null)
  const previousCollapsibleRef = useRef<boolean>(transformed.collapsible)

  useEffect(() => {
    const wasCollapsible = previousCollapsibleRef.current
    previousCollapsibleRef.current = transformed.collapsible

    if (!transformed.collapsible) {
      setCollapsed(false)
      return
    }

    if (!wasCollapsible && transformed.collapsible) {
      setCollapsed(true)
    }
  }, [transformed.collapsible])

  const activeEntry = useMemo<ToolEntryDisplay | null>(() => {
    if (!openKey) return null
    return transformed.entries.find((entry) => entryKey(entry) === openKey) ?? null
  }, [openKey, transformed.entries])

  const detailHostId = useMemo(() => `tool-detail-host-${slugify(cluster.cursor)}`, [cluster.cursor])

  useEffect(() => {
    if (collapsed && activeEntry) {
      setOpenKey(null)
    }
  }, [collapsed, activeEntry, setOpenKey])

  useEffect(() => {
    if (!isClusterRenderable(transformed)) {
      setOpenKey((current) => {
        if (current && current.startsWith(`${cluster.cursor}::`)) {
          return null
        }
        return current
      })
    }
  }, [cluster.cursor, setOpenKey, transformed])

  const handleToggleCluster = useCallback(() => {
    if (!transformed.collapsible) return
    setCollapsed((prev) => {
      const next = !prev
      if (next) {
        setOpenKey((current) => {
          if (current && current.startsWith(`${cluster.cursor}::`)) {
            return null
          }
          return current
        })
      }
      return next
    })
  }, [setOpenKey, transformed.collapsible, cluster.cursor])

  const handleChipClick = useCallback(
    (entry: ToolEntryDisplay) => {
      const key = entryKey(entry)

      if (collapsed && transformed.collapsible) {
        setCollapsed(false)
        pendingScrollKeyRef.current = key
        setOpenKey(key)
        return
      }

      if (openKey === key) {
        setOpenKey(null)
        pendingScrollKeyRef.current = null
        return
      }

      setOpenKey((current) => (current === key ? null : key))
      pendingScrollKeyRef.current = key
    },
    [collapsed, openKey, setOpenKey, transformed.collapsible],
  )

  const handleCloseDetail = useCallback(() => {
    closeScrollRef.current = window.scrollY
    setOpenKey(null)
    pendingScrollKeyRef.current = null
  }, [setOpenKey])

  useEffect(() => {
    const previousOpenKey = lastOpenKeyRef.current

    if (collapsed || !openKey || !activeEntry) {
      if (!openKey && closeScrollRef.current !== null) {
        window.scrollTo({ top: closeScrollRef.current })
        closeScrollRef.current = null
      }
      if (!openKey) {
        pendingScrollKeyRef.current = null
      }
      lastOpenKeyRef.current = openKey ?? null
      return
    }

    const shouldScroll =
      pendingScrollKeyRef.current === openKey || previousOpenKey !== openKey

    lastOpenKeyRef.current = openKey

    if (!shouldScroll) {
      return
    }

    const host = detailHostRef.current
    if (!host) {
      return
    }

    const detail = host.querySelector('.tool-chip-detail') as HTMLElement | null
    scrollIntoViewIfNeeded(detail ?? host)
    pendingScrollKeyRef.current = null
    closeScrollRef.current = null
  }, [activeEntry, collapsed, openKey])

  const articleClasses = useMemo(() => {
    const classes = ['timeline-event', 'tool-cluster']
    if (transformed.collapsible) {
      classes.push('tool-cluster--collapsible')
      if (collapsed) {
        classes.push('tool-cluster--collapsed')
      }
    }
    return classes.join(' ')
  }, [collapsed, transformed.collapsible])

  if (!isClusterRenderable(transformed)) {
    return null
  }

  const renderDetail = (entry: ToolEntryDisplay) => {
    const DetailComponent = entry.detailComponent
    const detailRelative = formatRelativeTimestamp(entry.timestamp) || entry.timestamp || ''
    return (
      <div className="tool-chip-detail">
        <div className="tool-chip-detail-header">
          <span className={`tool-chip-detail-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
            <ToolIcon icon={entry.icon} className="h-5 w-5" />
          </span>
          <div className="tool-chip-detail-text">
            <span className="tool-chip-detail-label">{entry.label}</span>
            {entry.timestamp ? (
              <time dateTime={entry.timestamp ?? undefined} className="tool-chip-detail-meta" title={entry.timestamp ?? undefined}>
                {detailRelative}
              </time>
            ) : null}
          </div>
          <button type="button" className="tool-chip-close" aria-label="Close tool call details" onClick={handleCloseDetail}>
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" className="h-4 w-4" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 6l8 8M6 14l8-8" />
            </svg>
          </button>
        </div>
        <div className="tool-chip-panel">
          <DetailComponent entry={entry} />
        </div>
      </div>
    )
  }

  return (
    <article
      className={articleClasses}
      data-cursor={cluster.cursor}
      data-entry-count={transformed.entryCount}
      data-collapsible={transformed.collapsible ? 'true' : 'false'}
      data-collapse-threshold={cluster.collapseThreshold}
      data-cluster-kind="tool"
      data-earliest={transformed.earliestTimestamp}
    >
      <div className="tool-cluster-shell">
        {transformed.collapsible ? (
          <div className="tool-cluster-summary">
            <button
              type="button"
              className="tool-cluster-batch-toggle"
              data-role="cluster-toggle"
              aria-expanded={collapsed ? 'false' : 'true'}
              onClick={handleToggleCluster}
            >
              <span className="tool-cluster-batch-icon">
                <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h10v10H7z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 5h10v10H5z" opacity="0.55" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 9h10v10H9z" opacity="0.4" />
                </svg>
              </span>
              <span className="tool-cluster-batch-label">
                <span data-role="cluster-count">{transformed.entryCount}</span> tools called
              </span>
            </button>
          </div>
        ) : null}

        <ul className="tool-chip-list" role="list">
          {transformed.entries.map((entry) => {
            const key = entryKey(entry)
            const isOpen = key === openKey && !collapsed
            const chipTitle = entry.caption && entry.caption !== entry.label ? `${entry.label} — ${entry.caption}` : entry.label
            return (
              <li key={entry.id} className={`tool-chip${isOpen ? ' is-open' : ''}`}>
                <button
                  className="tool-chip-trigger"
                  type="button"
                  aria-expanded={isOpen ? 'true' : 'false'}
                  aria-controls={detailHostId}
                  title={chipTitle}
                  onClick={() => handleChipClick(entry)}
                >
                  <span className={`tool-chip-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
                    <ToolIcon icon={entry.icon} className="h-5 w-5" />
                  </span>
                  <span className="tool-chip-body">
                    <span className="tool-chip-label">{entry.label}</span>
                    {entry.caption ? (
                      <>
                        <span className="tool-chip-separator" aria-hidden="true" />
                        <span className="tool-chip-caption">{entry.caption}</span>
                      </>
                    ) : null}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        <div ref={detailHostRef} className="tool-cluster-detail-host" hidden={!activeEntry || collapsed} id={detailHostId} aria-live="polite">
          {!collapsed && activeEntry ? renderDetail(activeEntry) : null}
        </div>
        {transformed.latestTimestamp ? (
          <div
            className="tool-cluster-timestamp chat-meta"
            data-role="cluster-timestamp"
            title={transformed.latestTimestamp}
          >
            {formatRelativeTimestamp(transformed.latestTimestamp) ?? transformed.latestTimestamp}
          </div>
        ) : null}
      </div>
    </article>
  )
}
