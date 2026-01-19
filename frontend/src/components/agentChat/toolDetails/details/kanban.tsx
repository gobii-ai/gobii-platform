import type { KanbanEvent } from '../../../../types/agentChat'
import type { ToolDetailProps } from '../../tooling/types'
import { KanbanEventCard } from '../../KanbanEventCard'

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
  if (!event) {
    return <p className="text-sm text-slate-600">Kanban update details unavailable.</p>
  }
  return <KanbanEventCard event={event} />
}
