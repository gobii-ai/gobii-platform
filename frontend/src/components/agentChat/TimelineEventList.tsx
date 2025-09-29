import { Fragment } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import type { TimelineEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
}

export function TimelineEventList({ agentFirstName, events }: TimelineEventListProps) {
  if (!events.length) {
    return <div className="timeline-empty text-center text-sm text-slate-400">No activity yet.</div>
  }

  return (
    <Fragment>
      {events.map((event) => {
        if (event.kind === 'message') {
          return (
            <MessageEventCard
              key={event.cursor}
              eventCursor={event.cursor}
              message={event.message}
              agentFirstName={agentFirstName}
            />
          )
        }
        return <ToolClusterCard key={event.cursor} cluster={event} />
      })}
    </Fragment>
  )
}
