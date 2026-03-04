import { memo } from 'react'
import { FileCheck2, CalendarClock } from 'lucide-react'
import type { ToolCallEntry } from '../../types/agentChat'
import { summarizeSchedule } from '../../util/schedule'

// ---------------------------------------------------------------------------
// Charter
// ---------------------------------------------------------------------------

function extractCharterSnippet(entry: ToolCallEntry): string {
  const text =
    entry.charterText ||
    (entry.parameters as Record<string, unknown> | undefined)?.new_charter as string | undefined ||
    (entry.parameters as Record<string, unknown> | undefined)?.charter as string | undefined
  if (!text) return 'Assignment updated'
  const trimmed = text.trim()
  return trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed
}

type InlineCharterCardProps = { entry: ToolCallEntry }

export const InlineCharterCard = memo(function InlineCharterCard({ entry }: InlineCharterCardProps) {
  return (
    <div className="inline-status-card">
      <span className="inline-status-card__icon inline-status-card__icon--charter">
        <FileCheck2 size={14} strokeWidth={2} />
      </span>
      <span className="inline-status-card__body">
        <span className="inline-status-card__label">Assignment updated</span>
        <span className="inline-status-card__detail">{extractCharterSnippet(entry)}</span>
      </span>
    </div>
  )
})

// ---------------------------------------------------------------------------
// Schedule
// ---------------------------------------------------------------------------

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

