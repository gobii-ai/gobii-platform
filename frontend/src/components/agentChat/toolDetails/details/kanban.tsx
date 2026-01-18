import type { KanbanCardChange, KanbanEvent } from '../../../../types/agentChat'
import type { ToolDetailProps } from '../../tooling/types'
import { Section } from '../shared'

const ACTION_ORDER: KanbanCardChange['action'][] = [
  'created',
  'started',
  'completed',
  'updated',
  'deleted',
  'archived',
]

const ACTION_LABELS: Record<KanbanCardChange['action'], string> = {
  created: 'Created',
  started: 'Started',
  completed: 'Completed',
  updated: 'Updated',
  deleted: 'Deleted',
  archived: 'Archived',
}

const MAX_TITLES = 3

function toTitleCase(value: string): string {
  return value
    .split(/[\s_\-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatStatus(status?: string | null): string | null {
  if (!status) {
    return null
  }
  const trimmed = status.trim()
  if (!trimmed) {
    return null
  }
  return toTitleCase(trimmed)
}

function formatChangeLabel(change: KanbanCardChange): string {
  const title = change.title?.trim() || 'Untitled card'
  const fromLabel = formatStatus(change.fromStatus)
  const toLabel = formatStatus(change.toStatus)
  let statusLabel: string | null = null

  if (fromLabel && toLabel && fromLabel !== toLabel) {
    statusLabel = `${fromLabel} -> ${toLabel}`
  } else if (toLabel) {
    statusLabel = toLabel
  } else if (fromLabel) {
    statusLabel = fromLabel
  }

  return statusLabel ? `${title} (${statusLabel})` : title
}

function groupChanges(changes: KanbanCardChange[]) {
  const grouped = new Map<KanbanCardChange['action'], string[]>()
  for (const change of changes) {
    const label = formatChangeLabel(change)
    const existing = grouped.get(change.action) ?? []
    existing.push(label)
    grouped.set(change.action, existing)
  }

  return ACTION_ORDER.filter((action) => grouped.has(action)).map((action) => ({
    action,
    labels: grouped.get(action) ?? [],
  }))
}

function extractKanbanEvent(entry: ToolDetailProps['entry']): KanbanEvent | null {
  const raw = entry.rawParameters
  if (raw && typeof raw === 'object' && 'changes' in raw && 'snapshot' in raw) {
    return raw as KanbanEvent
  }
  const result = entry.result
  if (result && typeof result === 'object' && 'changes' in result && 'snapshot' in result) {
    return result as KanbanEvent
  }
  return null
}

export function KanbanUpdateDetail({ entry }: ToolDetailProps) {
  const event = extractKanbanEvent(entry)
  const summary = event?.displayText || entry.caption
  const changes = event?.changes ?? []
  const groups = groupChanges(changes)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {summary ? <p className="text-slate-700">{summary}</p> : null}
      <Section title="Changes">
        {groups.length ? (
          <div className="space-y-2">
            {groups.map((group) => {
              if (!group.labels.length) {
                return null
              }
              const visible = group.labels.slice(0, MAX_TITLES)
              const remaining = group.labels.length - visible.length
              const list = visible.join(', ')
              const suffix = remaining > 0 ? ` (+${remaining} more)` : ''
              return (
                <div key={group.action} className="flex flex-wrap items-start gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {ACTION_LABELS[group.action]}
                  </span>
                  <span className="text-slate-700">{list}{suffix}</span>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-slate-500">No card changes recorded.</p>
        )}
      </Section>
    </div>
  )
}
