import { Fragment } from 'react'
import type { ReactNode } from 'react'
import { CalendarClock, Clock, Repeat } from 'lucide-react'

import { describeSchedule } from '../../../../util/schedule'
import type { ScheduleDescription } from '../../../../util/schedule'
import type { ToolDetailProps } from '../../tooling/types'
import type { AgentConfigCharterChange } from '../../../tooling/agentConfigSql'
import { KeyValueList, Section, TruncatedMarkdown } from '../shared'
import { useAppSelector } from '../../../../store/hooks'
import { selectImmersiveShellViewer } from '../../../../store/immersiveShellSlice'

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

const scheduleHeroBaseClassName = 'relative flex items-center gap-[0.875rem] overflow-hidden rounded-[0.875rem] border px-[1.125rem] py-4'
const scheduleHeroEnabledClassName = 'border-[rgba(99,102,241,0.12)] bg-[linear-gradient(135deg,rgba(99,102,241,0.06)_0%,rgba(139,92,246,0.04)_100%)]'
const scheduleHeroDisabledClassName = 'border-[rgba(100,116,139,0.1)] bg-[linear-gradient(135deg,rgba(100,116,139,0.05)_0%,rgba(148,163,184,0.03)_100%)]'
const scheduleHeroOverlayClassName = 'pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,transparent_60%,rgba(139,92,246,0.04)_100%)]'
const scheduleHeroIconBaseClassName = 'flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[rgba(255,255,255,0.85)]'
const scheduleHeroIconEnabledClassName = 'shadow-[0_1px_3px_rgba(99,102,241,0.06),0_2px_8px_rgba(99,102,241,0.04)]'
const scheduleHeroIconDisabledClassName = 'shadow-[0_1px_3px_rgba(100,116,139,0.06),0_2px_8px_rgba(100,116,139,0.04)]'
const scheduleHeroEmojiClassName = 'text-[1.375rem] leading-none'
const scheduleHeroContentClassName = 'min-w-0 flex-1'
const scheduleHeroLabelBaseClassName = 'm-0 mb-[0.125rem] text-[0.6875rem] font-semibold uppercase tracking-[0.05em]'
const scheduleHeroValueBaseClassName = 'm-0 text-[1.0625rem] font-semibold leading-[1.3]'
const scheduleHeroBadgeBaseClassName = 'h-[1.125rem] w-[1.125rem] shrink-0'

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
    <div className={`${scheduleHeroBaseClassName} ${isDisabled ? scheduleHeroDisabledClassName : scheduleHeroEnabledClassName}`}>
      <span className={scheduleHeroOverlayClassName} aria-hidden="true" />
      <div className={`${scheduleHeroIconBaseClassName} ${isDisabled ? scheduleHeroIconDisabledClassName : scheduleHeroIconEnabledClassName}`}>
        <span className={scheduleHeroEmojiClassName} aria-hidden="true">{emoji}</span>
      </div>
      <div className={scheduleHeroContentClassName}>
        <p className={`${scheduleHeroLabelBaseClassName} ${isDisabled ? 'text-[#64748b]' : 'text-[#6366f1]'}`}>
          {isDisabled ? 'Schedule paused' : 'Runs automatically'}
        </p>
        <p className={`${scheduleHeroValueBaseClassName} ${isDisabled ? 'text-[#475569]' : 'text-[#312e81]'}`}>{summaryText}</p>
      </div>
      <Icon className={`${scheduleHeroBadgeBaseClassName} ${isDisabled ? 'text-[rgba(100,116,139,0.25)]' : 'text-[rgba(99,102,241,0.3)]'}`} aria-hidden="true" />
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
  const timeZone = useAppSelector(selectImmersiveShellViewer).timeZone
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
  const scheduleDetails = describeSchedule(scheduleValue, { timeZone: timeZone ?? undefined })
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

function CharterChangeDetail({ change }: { change: AgentConfigCharterChange }) {
  const previousText = change.previousText?.trim() || null
  const replacementText = change.replacementText?.trim() || null

  return (
    <div className="space-y-2 text-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Change applied</p>
      {previousText ? (
        <div className="grid gap-1 sm:grid-cols-[3rem_minmax(0,1fr)]">
          <span className="text-xs font-medium text-slate-500">From</span>
          <p className="whitespace-pre-wrap break-words text-slate-500 line-through">{previousText}</p>
        </div>
      ) : null}
      <div className="grid gap-1 sm:grid-cols-[3rem_minmax(0,1fr)]">
        <span className="text-xs font-medium text-slate-500">{previousText ? 'To' : 'Added'}</span>
        <p className="whitespace-pre-wrap break-words text-slate-700">
          {replacementText ?? (previousText ? 'Removed from the assignment.' : 'No assignment text was added.')}
        </p>
      </div>
    </div>
  )
}

export function AgentConfigUpdateDetail({ entry }: ToolDetailProps) {
  const timeZone = useAppSelector(selectImmersiveShellViewer).timeZone
  const parsedUpdate = entry.agentConfigUpdate
  const charterText = entry.charterText ?? parsedUpdate?.charterValue ?? null
  const charterChange = parsedUpdate?.charterChange ?? null
  const hasCharterText = charterText !== null
  const updatesCharter = Boolean(parsedUpdate?.updatesCharter || hasCharterText || charterChange)
  const updatesSchedule = parsedUpdate?.updatesSchedule ?? false
  const scheduleCleared = parsedUpdate?.scheduleCleared ?? false
  const scheduleRaw = parsedUpdate?.scheduleValue ?? null
  const scheduleKnown = scheduleCleared || scheduleRaw !== null
  const scheduleValue = scheduleCleared ? null : scheduleRaw
  const scheduleDetails = scheduleKnown
    ? describeSchedule(scheduleValue, { timeZone: timeZone ?? undefined })
    : null

  return (
    <div className="space-y-4">
      {updatesSchedule && scheduleDetails ? renderScheduleCard(scheduleDetails) : null}
      {updatesCharter && charterChange ? <CharterChangeDetail change={charterChange} /> : null}
      {updatesCharter && hasCharterText ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Updated assignment</p>
          {charterText ? (
            <TruncatedMarkdown content={charterText} maxLines={3} />
          ) : (
            <p className="text-sm text-slate-600">The assignment is now empty.</p>
          )}
        </div>
      ) : null}
      {updatesCharter && !hasCharterText && !charterChange ? (
        <p className="text-sm text-slate-600">
          The updated assignment text is not available for this historical event.
        </p>
      ) : null}
    </div>
  )
}
