import { useMemo } from 'react'
import { ToolStepDetails } from './ToolStepDetails'
import type { ToolCallEntry, ToolClusterEvent } from './types'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function resolvePanelId(cluster: ToolClusterEvent, entry: ToolCallEntry, index: number) {
  const base = `${cluster.cursor}-${entry.id || index}`
  return `tool-panel-${slugify(base)}`
}

export function ToolClusterCard({ cluster }: ToolClusterCardProps) {
  const articleClasses = useMemo(() => {
    const classes = ['timeline-event', 'tool-cluster']
    if (cluster.collapsible) {
      classes.push('tool-cluster--collapsible', 'tool-cluster--collapsed')
    }
    return classes.join(' ')
  }, [cluster.collapsible])

  return (
    <article
      className={articleClasses}
      data-cursor={cluster.cursor}
      data-entry-count={cluster.entryCount}
      data-collapsible={cluster.collapsible ? 'true' : 'false'}
      data-collapse-threshold={cluster.collapseThreshold}
      data-cluster-kind="tool"
      data-earliest={cluster.earliestTimestamp}
    >
      <div className="tool-cluster-shell">
        <div className="tool-cluster-summary">
          <button type="button" className="tool-cluster-batch-toggle" data-role="cluster-toggle" aria-expanded={cluster.collapsible ? 'false' : 'true'}>
            <span className="tool-cluster-batch-icon">
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h10v10H7z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 5h10v10H5z" opacity="0.55" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 9h10v10H9z" opacity="0.4" />
              </svg>
            </span>
            <span className="tool-cluster-batch-label">
              <span data-role="cluster-count">{cluster.entryCount}</span> tools called
            </span>
          </button>
        </div>

        <ul className="tool-chip-list" role="list">
          {cluster.entries.map((entry, index) => {
            const panelId = resolvePanelId(cluster, entry, index)
            return (
              <li key={entry.id || index} className="tool-chip" data-detail-id={panelId}>
                <button className="tool-chip-trigger" type="button" aria-expanded="false" aria-controls={panelId}>
                  <span className={`tool-chip-icon ${entry.meta.iconBg} ${entry.meta.iconColor}`}>
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      {entry.meta.iconPaths.map((d, i) => (
                        <path key={i} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={d} />
                      ))}
                    </svg>
                  </span>
                  <span className="tool-chip-body">
                    <span className="tool-chip-label">{entry.meta.label}</span>
                    {entry.caption ? <span className="tool-chip-caption">{entry.caption}</span> : null}
                  </span>
                </button>
                <div className="tool-chip-detail" id={panelId} hidden>
                  <div className="tool-chip-detail-header">
                    <span className={`tool-chip-detail-icon ${entry.meta.iconBg} ${entry.meta.iconColor}`}>
                      <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        {entry.meta.iconPaths.map((d, i) => (
                          <path key={i} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={d} />
                        ))}
                      </svg>
                    </span>
                    <div className="tool-chip-detail-text">
                      <span className="tool-chip-detail-label">{entry.meta.label}</span>
                      {entry.timestamp ? (
                        <time dateTime={entry.timestamp} className="tool-chip-detail-meta">
                          {entry.timestamp}
                        </time>
                      ) : null}
                    </div>
                    <button type="button" className="tool-chip-close" aria-label="Close tool call details">
                      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" className="h-4 w-4" aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 6l8 8M6 14l8-8" />
                      </svg>
                    </button>
                  </div>
                  <div className="tool-chip-panel">
                    {entry.toolName === 'update_charter' && entry.charterText ? (
                      <div className="space-y-2 text-sm text-slate-600">
                        <p className="tool-chip-panel-title">Assignment details</p>
                        <div className="rounded-md bg-gray-50 p-3 whitespace-pre-wrap">{entry.charterText}</div>
                      </div>
                    ) : (
                      <ToolStepDetails entry={entry} inline />
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
        <div className="tool-cluster-detail-host" hidden aria-live="polite" />
        {cluster.latestTimestamp ? (
          <div className="tool-cluster-timestamp chat-meta" data-role="cluster-timestamp">
            {cluster.latestTimestamp}
          </div>
        ) : null}
      </div>
    </article>
  )
}
