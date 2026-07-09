import { memo } from 'react'
import { KeyRound, MessageSquareQuote, UserCheck, Users } from 'lucide-react'
import type { HumanInputActionResponse, UserActionEvent } from '../../types/agentChat'
import { useRelativeTimestamp } from '../../hooks/useRelativeTimestamp'

type UserActionEventCardProps = {
  event: UserActionEvent
  viewerUserId?: number | null
}

function actionIcon(actionType: string) {
  if (actionType.startsWith('secrets_')) {
    return KeyRound
  }
  if (actionType.startsWith('contacts_')) {
    return actionType === 'contacts_approved' ? UserCheck : Users
  }
  return MessageSquareQuote
}

function actorLabel(event: UserActionEvent, viewerUserId?: number | null): string {
  const actorUserId = event.action.actorUserId
  if (actorUserId !== null && actorUserId !== undefined && actorUserId === viewerUserId) {
    return 'You'
  }
  return event.action.actorName?.trim() || 'User'
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? singular : plural
}

function countPhrase(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? `a ${singular}` : `${count} ${plural}`
}

function metadataStringArray(event: UserActionEvent, key: string): string[] {
  const value = event.action.metadata?.[key]
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function metadataNumber(event: UserActionEvent, key: string): number {
  const value = event.action.metadata?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function compactLabelList(labels: string[], maxVisible = 2): string {
  const cleanLabels = labels.map((label) => label.trim()).filter(Boolean)
  if (!cleanLabels.length) {
    return ''
  }
  if (cleanLabels.length <= maxVisible) {
    return cleanLabels.join(cleanLabels.length === 2 ? ' and ' : ', ')
  }
  const visibleLabels = cleanLabels.slice(0, maxVisible).join(', ')
  return `${visibleLabels}, and ${cleanLabels.length - maxVisible} more`
}

function buildActionSummary(event: UserActionEvent): string {
  const count = Math.max(event.action.count || 1, 1)
  switch (event.action.actionType) {
    case 'human_input_answered':
      return `answered ${countPhrase(count, 'question')}`
    case 'human_input_dismissed':
      return 'dismissed a question'
    case 'secrets_saved':
      return `saved ${countPhrase(count, 'secret')}`
    case 'secrets_removed':
      return `removed ${countPhrase(count, 'secret request')}`
    case 'contacts_approved':
      return `approved ${countPhrase(count, 'contact')}`
    case 'contacts_declined':
      return `declined ${countPhrase(count, 'contact')}`
    case 'contacts_resolved':
      return `resolved ${countPhrase(count, 'contact request')}`
    default:
      return 'took an action'
  }
}

function buildTitle(event: UserActionEvent, viewerUserId?: number | null): string {
  const actor = actorLabel(event, viewerUserId)
  return `${actor} ${buildActionSummary(event)}`
}

function buildActionDetail(event: UserActionEvent): string {
  const count = Math.max(event.action.count || 1, 1)
  const secretNames = metadataStringArray(event, 'secret_names')
  const contactLabels = metadataStringArray(event, 'contact_labels')
  const compactContactLabels = compactLabelList(contactLabels)
  const skippedCount = metadataNumber(event, 'skipped_count')

  let detail = ''
  switch (event.action.actionType) {
    case 'human_input_answered':
      detail = count === 1 ? 'A response was submitted.' : `${count} responses were submitted.`
      break
    case 'human_input_dismissed':
      detail = 'Question dismissed.'
      break
    case 'secrets_saved':
      detail = count === 1
        ? `${secretNames[0]?.trim() || 'Secret'} saved.`
        : `${count} secrets saved.`
      break
    case 'secrets_removed':
      detail = count === 1
        ? `${secretNames[0]?.trim() || 'Secret'} request removed.`
        : `${count} secret requests were removed.`
      break
    case 'contacts_approved':
      detail = compactContactLabels
        ? `${compactContactLabels}.`
        : count === 1 ? 'Contact was approved.' : `${count} contacts were approved.`
      break
    case 'contacts_declined':
      detail = compactContactLabels
        ? `${compactContactLabels}.`
        : count === 1 ? 'Contact was declined.' : `${count} contacts were declined.`
      break
    case 'contacts_resolved': {
      const approvedCount = metadataNumber(event, 'approved_count')
      const declinedCount = metadataNumber(event, 'declined_count')
      const approvedPhrase = `${approvedCount} ${pluralize(approvedCount, 'contact')} ${pluralize(approvedCount, 'was', 'were')} approved`
      const declinedPhrase = `${declinedCount} ${pluralize(declinedCount, 'contact')} ${pluralize(declinedCount, 'was', 'were')} declined`
      detail = compactContactLabels ? `${compactContactLabels}.` : `${approvedPhrase} and ${declinedPhrase}.`
      break
    }
    default:
      detail = ''
      break
  }

  if (skippedCount > 0 && detail) {
    detail = `${detail.replace(/\.$/, '')}. ${skippedCount} left pending due to the contact limit.`
  }
  return detail
}

function readHumanInputResponses(event: UserActionEvent): HumanInputActionResponse[] {
  if (event.action.actionType !== 'human_input_answered') {
    return []
  }
  const responses = event.action.metadata?.responses
  if (!Array.isArray(responses)) {
    return []
  }
  return responses.flatMap((item) => {
    if (!item || typeof item !== 'object') {
      return []
    }
    const response = item as Record<string, unknown>
    const requestId = typeof response.request_id === 'string' ? response.request_id : ''
    const question = typeof response.question === 'string' ? response.question.trim() : ''
    const answer = typeof response.answer === 'string' ? response.answer.trim() : ''
    if (!question || !answer) {
      return []
    }
    return [{
      requestId,
      question,
      answer,
      answerType: typeof response.answer_type === 'string' ? response.answer_type : undefined,
      selectedOptionKey: typeof response.selected_option_key === 'string' ? response.selected_option_key : null,
    }]
  })
}

function HumanInputResponses({ responses }: { responses: HumanInputActionResponse[] }) {
  if (!responses.length) {
    return null
  }
  return (
    <div className="user-action-card__qa-list">
      {responses.map((response, index) => (
        <div className="user-action-card__qa" key={response.requestId || `${response.question}-${index}`}>
          <div className="user-action-card__qa-row">
            <span className="user-action-card__qa-label">Question</span>
            <p className="user-action-card__qa-text user-action-card__qa-text--question">{response.question}</p>
          </div>
          <div className="user-action-card__qa-row">
            <span className="user-action-card__qa-label">Answer</span>
            <p className="user-action-card__qa-text user-action-card__qa-text--answer">{response.answer}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

export const UserActionEventCard = memo(function UserActionEventCard({
  event,
  viewerUserId,
}: UserActionEventCardProps) {
  const Icon = actionIcon(event.action.actionType)
  const relativeLabel = useRelativeTimestamp(event.timestamp) || event.timestamp || ''
  const detail = buildActionDetail(event)
  const humanInputResponses = readHumanInputResponses(event)

  return (
    <article
      className="timeline-event chat-event is-user user-action-event"
      data-cursor={event.cursor}
      data-user-action-id={event.action.id}
      data-action-type={event.action.actionType}
    >
      <div className="user-action-card">
        <div className="user-action-card__icon" aria-hidden="true">
          <Icon size={20} strokeWidth={2.2} />
        </div>
        <div className="user-action-card__body">
          <div className="user-action-card__header">
            <span className="user-action-card__title">{buildTitle(event, viewerUserId)}</span>
            <span className="user-action-card__time" title={event.timestamp || undefined}>{relativeLabel}</span>
          </div>
          {humanInputResponses.length ? (
            <HumanInputResponses responses={humanInputResponses} />
          ) : detail ? (
            <p className="user-action-card__detail">{detail}</p>
          ) : null}
        </div>
      </div>
    </article>
  )
})
