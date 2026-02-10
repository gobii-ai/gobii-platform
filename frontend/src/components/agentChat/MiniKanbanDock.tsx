import { memo, useMemo } from 'react'
import type { KanbanBoardSnapshot } from '../../types/agentChat'

type MiniKanbanDockProps = {
  snapshot: KanbanBoardSnapshot
}

type ColumnKey = 'todo' | 'doing' | 'done'
type ColumnDef = {
  key: ColumnKey
  label: string
  count: number
  title: string | null
}

function pickFirstTitle(titles: string[]): string | null {
  const t = titles?.[0]?.trim()
  return t ? t : null
}

export const MiniKanbanDock = memo(function MiniKanbanDock({ snapshot }: MiniKanbanDockProps) {
  const columns = useMemo<ColumnDef[]>(() => ([
    { key: 'todo', label: 'Todo', count: snapshot.todoCount, title: pickFirstTitle(snapshot.todoTitles) },
    { key: 'doing', label: 'Doing', count: snapshot.doingCount, title: pickFirstTitle(snapshot.doingTitles) },
    { key: 'done', label: 'Done', count: snapshot.doneCount, title: pickFirstTitle(snapshot.doneTitles) },
  ]), [snapshot])

  return (
    <div className="mini-kanban-dock" aria-label="Kanban">
      {columns.map((col) => (
        <div key={col.key} className={`mini-kanban-dock__col mini-kanban-dock__col--${col.key}`}>
          <div className="mini-kanban-dock__top">
            <span className="mini-kanban-dock__label">{col.label}</span>
            <span className="mini-kanban-dock__count">{col.count}</span>
          </div>
          <div className="mini-kanban-dock__title" title={col.title ?? undefined}>
            {col.title ?? '—'}
          </div>
        </div>
      ))}
    </div>
  )
})

