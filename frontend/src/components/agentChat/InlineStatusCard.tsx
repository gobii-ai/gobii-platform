import { memo } from 'react'
import { CalendarClock } from 'lucide-react'
import type { ToolCallEntry } from '../../types/agentChat'
import { summarizeSchedule } from '../../util/schedule'

function extractScheduleSummary(entry: ToolCallEntry): string {
  const params = entry.parameters as Record<string, unknown> | undefined
  const raw = typeof params?.new_schedule === 'string' ? params.new_schedule : null
  return summarizeSchedule(raw) ?? entry.caption ?? 'Schedule updated'
}

type InlineScheduleCardProps = { entry: ToolCallEntry }

export const InlineScheduleCard = memo(function InlineScheduleCard({ entry }: InlineScheduleCardProps) {
  return (
    <div className="inline-status-card">
      <span className="inline-status-card__icon inline-status-card__icon--schedule">
        <CalendarClock size={14} strokeWidth={2} />
      </span>
      <span className="inline-status-card__body">
        <span className="inline-status-card__label">Schedule updated</span>
        <span className="inline-status-card__detail">{extractScheduleSummary(entry)}</span>
      </span>
    </div>
  )
})
