import { useCallback, useEffect, useMemo, useState } from 'react'
import { useToolDetailController, entryKey } from './tooling/ToolDetailContext'
import { transformToolCluster, isClusterRenderable } from './tooling/toolRegistry'
import type { ToolClusterEvent } from './types'
import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
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

  useEffect(() => {
    if (!transformed.collapsible) {
      setCollapsed(false)
    }
  }, [transformed.collapsible])

  const activeEntry = useMemo<ToolEntryDisplay | null>(() => {
    if (!openKey) return null
    return transformed.entries.find((entry) => entryKey(entry) === openKey) ?? null
  }, [openKey, transformed.entries])

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
  }, [cluster.cursor, transformed.entries.length, setOpenKey])

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
      if (collapsed && transformed.collapsible) {
        setCollapsed(false)
        setOpenKey(entryKey(entry))
        return
      }

      const key = entryKey(entry)
      if (openKey === key) {
        setOpenKey(null)
      } else {
        setOpenKey(key)
      }
    },
    [collapsed, openKey, setOpenKey, transformed.collapsible],
  )

  const handleCloseDetail = useCallback(() => {
    setOpenKey(null)
  }, [setOpenKey])

  if (!isClusterRenderable(transformed)) {
    return null
  }

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

  const detailHostId = useMemo(() => `tool-detail-host-${slugify(cluster.cursor)}`, [cluster.cursor])

  const renderDetail = (entry: ToolEntryDisplay) => {
    const DetailComponent = entry.detailComponent
    const detailRelative = formatRelativeTimestamp(entry.timestamp) || entry.timestamp || ''
    return (
      <div className="tool-chip-detail">
        <div className="tool-chip-detail-header">
          <span className={`tool-chip-detail-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              {entry.iconPaths.map((path, index) => (
                <path key={index} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={path} />
              ))}
            </svg>
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
            return (
              <li key={entry.id} className={`tool-chip${isOpen ? ' is-open' : ''}`}>
                <button
                  className="tool-chip-trigger"
                  type="button"
                  aria-expanded={isOpen ? 'true' : 'false'}
                  aria-controls={detailHostId}
                  onClick={() => handleChipClick(entry)}
                >
                  <span className={`tool-chip-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      {entry.iconPaths.map((path, index) => (
                        <path key={index} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={path} />
                      ))}
                    </svg>
                  </span>
                  <span className="tool-chip-body">
                    <span className="tool-chip-label">{entry.label}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        <div className="tool-cluster-detail-host" hidden={!activeEntry || collapsed} id={detailHostId} aria-live="polite">
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
