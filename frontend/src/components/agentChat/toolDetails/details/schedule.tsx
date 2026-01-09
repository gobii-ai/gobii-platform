import { Fragment } from 'react'
import type { ReactNode } from 'react'
import { CalendarClock, Clock, Repeat } from 'lucide-react'

import { describeSchedule } from '../../../../util/schedule'
import type { ScheduleDescription } from '../../../../util/schedule'
import type { ToolDetailProps } from '../../tooling/types'
import { parseAgentConfigUpdates } from '../../../tooling/agentConfigSql'
import { KeyValueList, Section, TruncatedMarkdown } from '../shared'

function formatSummaryText(summary: string): string {
  return /[.!?]\s*$/.test(summary) ? summary : `${summary}.`
}

function getScheduleIcon(schedule: ScheduleDescription) {
  if (schedule.kind === 'disabled') return Clock
  if (schedule.kind === 'interval' || (schedule.kind === 'cron' && schedule.summary?.toLowerCase().includes('every'))) {
    return Repeat
  }
  return CalendarClock
}

function getScheduleEmoji(schedule: ScheduleDescription): string {
  if (schedule.kind === 'disabled') return '⏸️'
  const summary =
    schedule.kind === 'cron'
      ? schedule.summary
      : schedule.kind === 'interval' || schedule.kind === 'preset'
        ? schedule.summary
        : null
  if (!summary) return '📅'
  const lower = summary.toLowerCase()
  if (lower.includes('hour')) return '⏰'
  if (lower.includes('day') || lower.includes('daily')) return '🌅'
  if (lower.includes('week')) return '📆'
  if (lower.includes('month')) return '🗓️'
  return '🔄'
}

function renderScheduleCard(schedule: ScheduleDescription): ReactNode {
  const Icon = getScheduleIcon(schedule)
  const emoji = getScheduleEmoji(schedule)

  // Get the human-readable summary
  const getSummaryText = (): string => {
    switch (schedule.kind) {
      case 'disabled':
        return 'Paused'
      case 'preset':
        return schedule.summary
      case 'interval':
        return schedule.summary
      case 'cron':
        return schedule.summary ?? 'Custom schedule'
      case 'unknown':
        return 'Custom schedule'
      default:
        return 'Scheduled'
    }
  }

  const summaryText = getSummaryText()
  const isDisabled = schedule.kind === 'disabled'

  return (
    <div className={`schedule-hero ${isDisabled ? 'schedule-hero--disabled' : ''}`}>
      <div className="schedule-hero-icon">
        <span className="schedule-hero-emoji" aria-hidden="true">{emoji}</span>
      </div>
      <div className="schedule-hero-content">
        <p className="schedule-hero-label">{isDisabled ? 'Schedule paused' : 'Runs automatically'}</p>
        <p className="schedule-hero-value">{summaryText}</p>
      </div>
      <Icon className="schedule-hero-badge" aria-hidden="true" />
    </div>
  )
}

function renderScheduleDetails(schedule: ScheduleDescription): ReactNode {
  switch (schedule.kind) {
    case 'disabled':
      return (
        <Section title="Schedule">
          <p className="text-slate-700">No automated runs are scheduled.</p>
        </Section>
      )
    case 'preset':
      return (
        <Section title="Preset Interval">
          <div className="schedule-card">
            <span className="schedule-card-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10m-12 8h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </span>
            <div>
              <p className="schedule-card-label">{schedule.raw}</p>
              <p className="schedule-card-description">{schedule.description}</p>
            </div>
          </div>
        </Section>
      )
    case 'interval':
      return (
        <Section title="Repeats Every">
          <div className="schedule-interval">
            {schedule.parts.map((part, index) => (
              <span key={`${part.unit}-${index}`} className="schedule-pill">
                <span className="schedule-pill-value">{part.magnitude}</span>
                <span className="schedule-pill-unit">{part.label.replace(/^[0-9]+\s/, '')}</span>
              </span>
            ))}
          </div>
          <p className="schedule-note">{formatSummaryText(schedule.summary)}</p>
        </Section>
      )
    case 'cron':
      return (
        <Section title="Schedule details">
          {schedule.summary ? <p className="schedule-note">{formatSummaryText(schedule.summary)}</p> : null}
          <dl className="schedule-cron-grid">
            {schedule.fields.map((field) => (
              <Fragment key={field.label}>
                <dt>{field.label}</dt>
                <dd>
                  <code>{field.value}</code>
                </dd>
              </Fragment>
            ))}
          </dl>
          {!schedule.summary ? (
            <p className="schedule-note">Custom schedule details with {schedule.fields.length} field(s).</p>
          ) : null}
        </Section>
      )
    case 'unknown':
      return (
        <Section title="Schedule">
          <p className="schedule-note">
            Unable to parse schedule format. Raw value: <code>{schedule.raw}</code>
          </p>
        </Section>
      )
    default:
      return null
  }
}

export function UpdateScheduleDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const newScheduleValue = params['new_schedule']
  const newScheduleRaw = typeof newScheduleValue === 'string' ? newScheduleValue.trim() : null
  const scheduleValue = newScheduleRaw && newScheduleRaw.length > 0 ? newScheduleRaw : null
  const resultObject =
    entry.result && typeof entry.result === 'object'
      ? (entry.result as { status?: string; message?: string })
      : null
  const statusLabel = resultObject?.status ? resultObject.status.toUpperCase() : null
  const messageText =
    resultObject?.message || entry.summary || (scheduleValue ? 'Schedule updated successfully.' : 'Schedule disabled.')
  const scheduleDetails = describeSchedule(scheduleValue)
  const detailItems: Array<{ label: string; value: ReactNode }> = []
  if (statusLabel) {
    detailItems.push({ label: 'Status', value: statusLabel })
  }
  return (
    <div className="space-y-4 text-sm text-slate-600">
      <p className="text-slate-700">{messageText}</p>
      <KeyValueList items={detailItems} />
      {renderScheduleDetails(scheduleDetails)}
    </div>
  )
}

export function AgentConfigUpdateDetail({ entry }: ToolDetailProps) {
  const statements = entry.sqlStatements ?? []
  const parsedUpdate = parseAgentConfigUpdates(statements)
  const charterText = parsedUpdate?.charterValue ?? entry.charterText ?? null
  const updatesCharter = parsedUpdate?.updatesCharter ?? Boolean(charterText)
  const updatesSchedule = parsedUpdate?.updatesSchedule ?? false
  const scheduleCleared = parsedUpdate?.scheduleCleared ?? false
  const scheduleRaw = parsedUpdate?.scheduleValue ?? null
  const scheduleKnown = scheduleCleared || scheduleRaw !== null
  const scheduleValue = scheduleCleared ? null : scheduleRaw
  const scheduleDetails = scheduleKnown ? describeSchedule(scheduleValue) : null

  return (
    <div className="space-y-4">
      {/* Schedule - shown first as the hero element when present */}
      {updatesSchedule && scheduleDetails ? renderScheduleCard(scheduleDetails) : null}

      {/* Assignment - truncated with expand */}
      {updatesCharter && charterText ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Assignment</p>
          <div className="rounded-xl bg-slate-50/80 p-3.5 shadow-sm border border-slate-100">
            <TruncatedMarkdown content={charterText} maxLines={3} />
          </div>
        </div>
      ) : null}
    </div>
  )
}
