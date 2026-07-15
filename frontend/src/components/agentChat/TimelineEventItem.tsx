import { memo, useMemo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { UserActionEventCard } from './UserActionEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { InlineScheduleCard } from './InlineStatusCard'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { AgentMessage, DeveloperTimelineEvent } from '../../types/agentChat'
import type { ToolClusterEvent } from '../../types/agentChat'
import { buildThinkingCluster, flattenTimelineEventsToEntries } from './activityEntryUtils'
import type { StatusExpansionTargets } from './statusExpansion'
import { useAppSelector } from '../../store/hooks'
import { selectImmersiveShellViewer } from '../../store/immersiveShellSlice'
import { selectActiveChatAgentId } from '../../store/chatSlice'
import { DeveloperTimelineEventCard } from './DeveloperTimelineEventCard'
import { developerMessageToAgentMessage } from './developerTimelineDisplay'

type TimelineEventItemProps = {
  event: SimplifiedTimelineItem
  isLatestEvent: boolean
  agentFirstName: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
  statusExpansionTargets?: StatusExpansionTargets
  animateIncoming?: boolean
  onIncomingAnimationConsumed?: (cursor: string) => void
  onMessageLinkClick?: (href: string) => boolean | void
  onMessageCopied?: (message: AgentMessage) => void | Promise<void>
  onReportMessage?: (message: AgentMessage) => void
  onRetryMessage?: (message: AgentMessage) => void | Promise<void>
}

export const TimelineEventItem = memo(function TimelineEventItem({
  event,
  isLatestEvent,
  agentFirstName,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
  statusExpansionTargets,
  animateIncoming = false,
  onIncomingAnimationConsumed,
  onMessageLinkClick,
  onMessageCopied,
  onReportMessage,
  onRetryMessage,
}: TimelineEventItemProps) {
  const timeZone = useAppSelector(selectImmersiveShellViewer).timeZone
  const activeAgentId = useAppSelector(selectActiveChatAgentId)
  const collapsedEntries = useMemo(() => {
    if (event.kind !== 'collapsed-group') {
      return []
    }
    return event.displayEntries ?? flattenTimelineEventsToEntries(event.events)
  }, [event, timeZone])

  if (event.kind === 'developer_message') {
    return (
      <MessageEventCard
        eventCursor={event.cursor}
        message={developerMessageToAgentMessage(event)}
        agentFirstName={agentFirstName}
        agentAvatarUrl={agentAvatarUrl}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        onMessageLinkClick={onMessageLinkClick}
        onMessageCopied={onMessageCopied}
        onReportMessage={onReportMessage}
      />
    )
  }

  if (event.kind.startsWith('developer_')) {
    return (
      <DeveloperTimelineEventCard
        agentId={activeAgentId ?? ''}
        event={event as DeveloperTimelineEvent}
      />
    )
  }

  if (event.kind === 'collapsed-group') {
    return <CollapsedActivityCard overlayId={event.cursor} entries={collapsedEntries} label={event.summary.label} subtitle="Collapsed actions" />
  }
  if (event.kind === 'inline-schedule') {
    return <InlineScheduleCard entry={event.entry} />
  }
  if (event.kind === 'message') {
    return (
      <MessageEventCard
        eventCursor={event.cursor}
        message={event.message}
        agentFirstName={agentFirstName}
        agentAvatarUrl={agentAvatarUrl}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        onMessageLinkClick={onMessageLinkClick}
        onMessageCopied={onMessageCopied}
        onReportMessage={onReportMessage}
        onRetryMessage={onRetryMessage}
      />
    )
  }
  if (event.kind === 'user_action') {
    return (
      <UserActionEventCard
        event={event}
        viewerUserId={viewerUserId ?? null}
      />
    )
  }
  if (event.kind === 'thinking') {
    return (
      <ToolClusterCard
        cluster={buildThinkingCluster(event)}
        isLatestEvent={isLatestEvent}
        suppressedThinkingCursor={suppressedThinkingCursor}
        statusExpansionTargets={statusExpansionTargets}
        animateIncoming={animateIncoming}
        onIncomingAnimationConsumed={onIncomingAnimationConsumed}
      />
    )
  }
  if (event.kind === 'plan' || event.kind === 'kanban') {
    return null
  }
  return (
    <ToolClusterCard
      cluster={event as ToolClusterEvent}
      isLatestEvent={isLatestEvent}
      suppressedThinkingCursor={suppressedThinkingCursor}
      statusExpansionTargets={statusExpansionTargets}
      animateIncoming={animateIncoming}
      onIncomingAnimationConsumed={onIncomingAnimationConsumed}
    />
  )
})
